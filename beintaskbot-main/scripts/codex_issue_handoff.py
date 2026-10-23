#!/usr/bin/env python3
"""Validate and render the bounded GitHub Issue handoff contract.

The queue is GitHub Issues, not a control plane for Beintaskbot.  Consequently
this utility never contacts Beintaskbot, Railway, GitHub, or an AI provider.
It makes the data passed from an issue to an automation runner explicit and
rejects modes that could perform a production write.
"""
import argparse
import json
import re
import sys


MARKER = re.compile(r"<!--\s*codex-handoff\s*(\{.*?\})\s*-->", re.DOTALL)
KEY = re.compile(r"[a-z][a-z0-9_.:-]{2,199}$")
SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}$")
SEVERITIES = {"P0", "P1", "P2", "P3"}
MODES = {"read_only", "dry_run", "change"}
APPROVALS = {"not_required_read_only", "pending_owner_approval", "owner_approved"}


class ContractError(ValueError):
    pass


def contract_from_body(body):
    match = MARKER.search(str(body or ""))
    if not match:
        raise ContractError("Missing codex-handoff JSON marker.")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ContractError("Invalid codex-handoff JSON marker.") from exc
    if not isinstance(data, dict):
        raise ContractError("Handoff contract must be an object.")
    required = {"schema_version", "mode", "evidence", "tenant_safe_ids", "severity", "acceptance_criteria", "approval_state", "dedupe_key"}
    if set(data) != required or data["schema_version"] != 1:
        raise ContractError("Unexpected handoff fields or schema version.")
    if data["mode"] not in MODES or data["severity"] not in SEVERITIES or data["approval_state"] not in APPROVALS:
        raise ContractError("Invalid mode, severity, or approval state.")
    if not isinstance(data["dedupe_key"], str) or not KEY.fullmatch(data["dedupe_key"]):
        raise ContractError("Invalid dedupe key.")
    for key in ("evidence", "acceptance_criteria"):
        if not isinstance(data[key], list) or not 1 <= len(data[key]) <= 12 or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in data[key]):
            raise ContractError("Invalid " + key + ".")
    if not isinstance(data["tenant_safe_ids"], dict) or len(data["tenant_safe_ids"]) > 12:
        raise ContractError("Invalid tenant-safe IDs.")
    for name, value in data["tenant_safe_ids"].items():
        if not isinstance(name, str) or not KEY.fullmatch(name) or not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise ContractError("Invalid tenant-safe ID.")
    # A GitHub label is not a server-side authorization for a Beintaskbot write.
    # The handoff runner consequently never executes `change` work.
    if data["mode"] == "change":
        if data["approval_state"] != "owner_approved":
            raise ContractError("Change work requires an owner approval record.")
    elif data["approval_state"] == "owner_approved":
        raise ContractError("Read-only and dry-run handoffs must not claim a write approval.")
    return data


def validate(issue):
    if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
        raise ContractError("Issue number is required.")
    contract = contract_from_body(issue.get("body"))
    labels = {str(x.get("name", "")) for x in issue.get("labels", []) if isinstance(x, dict)}
    return {"issue": issue["number"], "contract": contract,
            "labels": sorted(labels),
            "eligible_for_codex": "codex:approved" in labels and contract["mode"] in {"read_only", "dry_run"} and contract["approval_state"] != "pending_owner_approval",
            "production_write_allowed": False}


def status(payload, phase, details="", pr="", tests="", rollback=""):
    contract = payload["contract"]
    lines = ["<!-- codex-handoff-status -->", "## Codex handoff status", "",
             "- Issue: #" + str(payload["issue"]), "- Phase: `" + phase + "`",
             "- Mode: `" + contract["mode"] + "`", "- Approval state: `" + contract["approval_state"] + "`",
             "- Dedupe key: `" + contract["dedupe_key"] + "`", "- Production writes: blocked by this workflow"]
    if details: lines.extend(["", "Details: " + details[:1000]])
    if tests: lines.append("- Tests: " + tests[:1000])
    if pr: lines.append("- PR: " + pr[:500])
    if rollback: lines.append("- Rollback: " + rollback[:1000])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "status"))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--phase", default="validated")
    parser.add_argument("--details", default="")
    parser.add_argument("--pr", default="")
    parser.add_argument("--tests", default="")
    parser.add_argument("--rollback", default="")
    args = parser.parse_args()
    try:
        if args.input == "-":
            payload = validate(json.load(sys.stdin))
        else:
            with open(args.input, encoding="utf-8") as handle:
                payload = validate(json.load(handle))
        output = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) if args.command == "validate" else status(payload, args.phase, args.details, args.pr, args.tests, args.rollback)
        if args.output == "-":
            print(output, end="")
        else:
            with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(output)
    except (OSError, ContractError, json.JSONDecodeError) as exc:
        print("codex issue handoff: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
