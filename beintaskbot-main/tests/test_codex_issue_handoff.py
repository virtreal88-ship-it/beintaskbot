import json
from pathlib import Path

import pytest

from scripts import codex_issue_handoff as handoff


QUEUE = "2f10a768-b169-45be-9e81-bcc15f0ff93c"


def issue(mode="read_only", approval="not_required_read_only", labels=None):
    contract = {"schema_version": 1, "mode": mode,
                "evidence": ["The queue item is queued with attempts zero."],
                "tenant_safe_ids": {"tenant_id": "e09a5c24-093f-4141-992d-532052e00c90", "queue_id": QUEUE},
                "severity": "P1", "acceptance_criteria": ["Return admission facts without a model call."],
                "approval_state": approval, "dedupe_key": "simulation-queue:" + QUEUE}
    return {"number": 152, "body": "Pilot\n<!-- codex-handoff\n" + json.dumps(contract) + "\n-->",
            "labels": [{"name": name} for name in (labels or ["codex:ready"])]}


def test_read_only_contract_is_bounded_and_needs_explicit_approved_label():
    pending = handoff.validate(issue())
    assert pending["eligible_for_codex"] is False
    approved = handoff.validate(issue(labels=["codex:ready", "codex:approved"]))
    assert approved["eligible_for_codex"] is True
    assert approved["production_write_allowed"] is False
    text = handoff.status(approved, "investigated", tests="targeted tests passed", rollback="revert application PR")
    assert "Production writes: blocked" in text and QUEUE in text


def test_contract_rejects_missing_or_unsafe_structure():
    with pytest.raises(handoff.ContractError): handoff.contract_from_body("no marker")
    unsafe = issue(); unsafe["body"] = unsafe["body"].replace('"mode": "read_only"', '"mode": "shell"')
    with pytest.raises(handoff.ContractError): handoff.validate(unsafe)
    with pytest.raises(handoff.ContractError): handoff.validate(issue(mode="change", approval="pending_owner_approval"))
    with pytest.raises(handoff.ContractError): handoff.validate(issue(mode="read_only", approval="owner_approved"))


def test_workflow_has_explicit_labels_environment_and_no_merge_or_deploy():
    repository_root = Path(__file__).resolve().parents[2]
    workflow_path = repository_root / ".github/workflows/codex-issue-handoff.yml"
    assert workflow_path.is_file()
    workflow = workflow_path.read_text(encoding="utf-8")
    assert "codex:ready" in workflow and "codex:approved" in workflow
    assert "codex-readonly-approved" in workflow and "openai/codex-action" in workflow
    assert "python beintaskbot-main/scripts/codex_issue_handoff.py" in workflow
    assert "beintaskbot-main/.codex-handoff-contract.json" in workflow
    assert "open(sys.argv[1])" in workflow and "eligible=$eligible" in workflow
    assert "gh pr merge" not in workflow and "railway up" not in workflow.casefold()
