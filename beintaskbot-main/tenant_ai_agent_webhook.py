"""Isolated Kommo ingress for a staging AI Agents Test Mode deployment.

This handler deliberately does not invoke the legacy webhook path: it only
parses message events, applies the live Test Mode binding, and writes the
durable AI queue / manager-reply signal.
"""
import asyncio
import os
import re

from aiohttp import web


_ENABLED = {"1", "true", "yes", "on"}
_ACK_BUDGET_SECONDS = 1.5


def _add_rows(data, group):
    if not group and isinstance(data.get("add"), list):
        return [dict(row) for row in data["add"] if isinstance(row, dict)]
    value = data.get(group)
    if isinstance(value, dict) and isinstance(value.get("add"), list):
        return [dict(row) for row in value["add"] if isinstance(row, dict)]
    # Kommo can encode each entity field as a separate form key.
    prefix = f"{group}[add]" if group else "add"
    pattern = re.compile(r"^" + re.escape(prefix) + r"\[(\d+)\]\[(.+)\]$")
    indexed = {}
    for key, item in data.items():
        match = pattern.match(str(key))
        if match:
            indexed.setdefault(int(match.group(1)), {})[match.group(2)] = item
    return [indexed[index] for index in sorted(indexed)]


def parse_message_rows(data):
    """Read JSON and form-encoded incoming/outgoing message event shapes."""
    if not isinstance(data, dict):
        return []
    rows = []
    for row in _add_rows(data, "message"):
        if not str(row.get("type") or "").strip():
            row["type"] = "incoming"
        rows.append(row)
    # The incoming-message webhook is documented with a root-level `add`.
    for row in _add_rows(data, ""):
        if not str(row.get("type") or "").strip():
            row["type"] = "incoming"
        rows.append(row)
    for row in _add_rows(data, "outgoing_message"):
        row["type"] = "outgoing"
        rows.append(row)
    return rows


def build_handler(kommo_request, logger, *, ingest=None, observe=None):
    """Build a narrow staging endpoint; no legacy CRM or notification effects."""
    del kommo_request  # Live Kommo reads belong in the worker, after the fast durable ACK.

    async def handle(request: web.Request) -> web.Response:
        try:
            # Turning off the feature acknowledges and discards callbacks
            # without parsing or touching the database.
            if (os.environ.get("AI_AGENTS_ENABLED", "").strip().lower() not in _ENABLED
                    or os.environ.get("AI_AGENTS_TEST_WEBHOOK_ENABLED", "").strip().lower() not in _ENABLED):
                return web.Response(status=200, text="AI Agents disabled")

            data = {}
            if "json" in str(request.content_type or ""):
                parsed = await request.json()
                data = parsed if isinstance(parsed, dict) else {}
            else:
                data = dict(await request.post())

            rows = parse_message_rows(data)
            incoming = [row for row in rows if str(row.get("type") or "incoming").strip().lower() != "outgoing"]
            outgoing = [row for row in rows if str(row.get("type") or "").strip().lower() == "outgoing"]
            if not incoming and not outgoing:
                return web.Response(status=200, text="No message event")

            if ingest is None or observe is None:
                from tenant_ai_agent_queue import ingest_kommo_messages, observe_kommo_outgoing
                current_ingest = ingest or ingest_kommo_messages
                current_observe = observe or observe_kommo_outgoing
            else:
                current_ingest, current_observe = ingest, observe

            # The DB ingest functions require a full exact Test Mode match
            # (tenant/account/contact/deal/channel/talk/chat) before storing.
            # Do no synchronous Kommo API reads here: Kommo expects a quick
            # webhook response, while the worker revalidates live identities.
            operations = []
            if incoming:
                operations.append(asyncio.to_thread(current_ingest, data, incoming, fallback_domain=""))
            if outgoing:
                operations.append(asyncio.to_thread(current_observe, data, outgoing, fallback_domain=""))
            if operations:
                # Leave headroom under Kommo's webhook response window. A timed
                # out durable write may finish in its thread; DB uniqueness
                # constraints make Kommo's retry idempotent.
                await asyncio.wait_for(asyncio.gather(*operations), timeout=_ACK_BUDGET_SECONDS)
            return web.Response(status=200, text="OK")
        except Exception:
            # Do not log body content. A 503 lets Kommo retry if durable queue
            # storage failed before commit.
            logger.exception("Staging AI Agents webhook could not commit event")
            return web.Response(status=503, text="Retry")

    return handle
