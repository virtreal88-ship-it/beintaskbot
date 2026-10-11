"""HTTP handlers for tenant Kommo setup and pipeline selection."""
import asyncio
from collections.abc import Callable
from aiohttp import web
import requests
from urllib.parse import quote, urlencode

from tenant_kommo import KommoContext, TenantKommo, callback_domain


def handlers(context: KommoContext, client: TenantKommo) -> dict[str, Callable]:
        async def request_connection(request: web.Request) -> web.Response:
            profile = context.member(request, owner_only=True)
            if not profile:
                return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                integration = await asyncio.to_thread(
                    context.request_connection, tenant_id=profile["tenant_id"],
                    owner_id=int(profile["telegram_id"]),
                    account_domain=str((data or {}).get("account_domain") or ""))
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)
            try:
                client.settings()
                ready = True
            except context.platform_error:
                ready = False
            return web.json_response({"success": True, "integration": integration, "oauth_ready": ready})

        async def disconnect(request: web.Request) -> web.Response:
            profile = context.member(request, owner_only=True)
            if not profile:
                return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                item = await asyncio.to_thread(
                    context.disconnect_integration, tenant_id=profile["tenant_id"],
                    owner_id=int(profile["telegram_id"]), provider=str((data or {}).get("provider") or ""))
                await asyncio.to_thread(
                    context.append_audit, tenant_id=profile["tenant_id"],
                    actor_telegram_id=int(profile["telegram_id"]), action="integration_disconnected",
                    entity_type="integration", entity_id=item["provider"], payload={})
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)
            return web.json_response({"success": True, "integration": item})

        async def start(request: web.Request) -> web.Response:
            profile = context.member(request, owner_only=True)
            if not profile:
                return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
            try:
                data = await request.json()
            except Exception:
                data = {}
            try:
                client_id, _, _ = client.settings()
                state = await asyncio.to_thread(
                    context.begin_oauth, tenant_id=profile["tenant_id"], owner_id=int(profile["telegram_id"]),
                    account_domain=str((data or {}).get("account_domain") or ""))
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)
            return web.json_response({"success": True, "authorize_url": "https://www.kommo.com/oauth?" + urlencode({
                "client_id": client_id, "state": state, "mode": "popup"})})

        async def callback(request: web.Request) -> web.Response:
            query = request.rel_url.query
            state = str(query.get("state") or "")
            if not state:
                raise web.HTTPFound("/setup?kommo=invalid")
            try:
                oauth_context = await asyncio.to_thread(context.consume_oauth_state, state)
                if isinstance(oauth_context, dict):
                    tenant_id = str(oauth_context.get("tenant_id") or "")
                    saved_domain = str(oauth_context.get("account_domain") or "")
                else:
                    tenant_id, saved_domain = str(oauth_context or ""), ""
                if str(query.get("error") or ""):
                    raise context.platform_error("Kommo bağlantısına icazə verilmədi.")
                code = str(query.get("code") or "").strip()
                hint = str(query.get("account_domain") or query.get("referer") or query.get("referrer")
                           or query.get("account") or query.get("subdomain") or query.get("domain")
                           or saved_domain or "")
                context.logger.info("Kommo OAuth callback received: query_keys=%s domain_source=%s saved_domain=%s",
                                         sorted(str(key) for key in query.keys()),
                                         "query" if hint and hint != saved_domain else "saved", bool(saved_domain))
                domain = callback_domain(hint, context.platform_error)
                client_id, client_secret, redirect_uri = client.settings()
                if not code:
                    raise context.platform_error("Kommo təsdiq kodunu göndərmədi.")

                def exchange() -> dict:
                    response = requests.post(
                        f"https://{domain}/oauth2/access_token",
                        json={"client_id": client_id, "client_secret": client_secret,
                              "grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect_uri}, timeout=15)
                    if response.status_code != 200:
                        context.logger.warning("Kommo OAuth exchange failed: status=%s", response.status_code)
                        raise context.platform_error("Kommo bağlantısı təsdiqlənmədi. Yenidən cəhd edin.")
                    payload = response.json()
                    return payload if isinstance(payload, dict) else {}

                tokens = await asyncio.to_thread(exchange)
                await asyncio.to_thread(context.save_tokens, tenant_id=tenant_id,
                                        account_domain=domain, token_payload=tokens)
            except context.platform_error as exc:
                context.logger.info("Kommo OAuth callback rejected: %s", exc)
                raise web.HTTPFound(f"/setup?kommo=error&reason={quote(str(exc)[:220], safe='')}")
            except Exception:
                context.logger.exception("Kommo OAuth callback failed")
                raise web.HTTPFound("/setup?kommo=error&reason=" + quote("Kommo bağlantısı tamamlanmadı.", safe=""))
            raise web.HTTPFound("/setup?kommo=connected")

        async def pipelines(request: web.Request) -> web.Response:
            profile = context.member(request, owner_only=True)
            if not profile:
                return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
            try:
                payload = await client.request(profile["tenant_id"], "GET", "leads/pipelines")
                rows = []
                for pipeline in ((payload.get("_embedded") or {}).get("pipelines") or [])[:100]:
                    stages = ((pipeline.get("_embedded") or {}).get("statuses") or [])
                    rows.append({"id": int(pipeline.get("id") or 0),
                                 "name": str(pipeline.get("name") or "Adsız vərəq"),
                                 "stages": [{"id": int(stage.get("id") or 0),
                                             "name": str(stage.get("name") or "Adsız mərhələ")}
                                            for stage in stages[:100] if int(stage.get("id") or 0)]})
                return web.json_response({"success": True, "pipelines": [row for row in rows if row["id"]]})
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)

        async def test(request: web.Request) -> web.Response:
            profile = context.member(request, owner_only=True)
            if not profile:
                return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
            try:
                account = await client.request(profile["tenant_id"], "GET", "account")
                return web.json_response({"success": True, "message": "Kommo bağlantısı işləyir.", "account": {
                    "id": account.get("id"), "name": str(account.get("name") or "Kommo hesabı"),
                    "subdomain": str(account.get("subdomain") or "")}})
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)

        async def save_pipelines(request: web.Request) -> web.Response:
            profile = context.member(request, owner_only=True)
            if not profile:
                return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
            try:
                data = await request.json()
            except Exception:
                data = {}
            selected = []
            raw_items = (data or {}).get("pipelines", []) if isinstance((data or {}).get("pipelines", []), list) else []
            for item in raw_items:
                try:
                    pipeline_id = int(item.get("id") or 0)
                except (AttributeError, TypeError, ValueError):
                    continue
                stage_ids = []
                for stage_id in item.get("stage_ids") if isinstance(item.get("stage_ids"), list) else []:
                    try:
                        normalized = int(stage_id)
                        if normalized > 0:
                            stage_ids.append(normalized)
                    except (TypeError, ValueError):
                        continue
                if pipeline_id > 0:
                    selected.append({"id": pipeline_id, "stage_ids": stage_ids[:100]})
            try:
                remote = await client.request(profile["tenant_id"], "GET", "leads/pipelines")
                remote_rows = (remote.get("_embedded") or {}).get("pipelines") or []
                selected_by_id = {str(item["id"]): item["stage_ids"] for item in selected}
                remote_by_id = {str(item["id"]): item for item in remote_rows}
                if any(pid not in remote_by_id for pid in selected_by_id):
                    raise context.platform_error("Seçilən vərəq Kommo hesabınızda yoxdur.")
                prior = profile.get("workflow") or {}
                prior_pipelines = {str(item["pipeline_id"]): item for item in prior.get("pipelines", [])}
                prior_stages = {(str(item["pipeline_id"]), str(item["stage_id"])): item
                                for item in prior.get("stages", [])}
                pipeline_config, stages = [], []
                for pid, pipeline in remote_by_id.items():
                    pipeline_config.append({**prior_pipelines.get(pid, {}), "pipeline_id": pid,
                        "name": pipeline.get("name") or "", "active": pid in selected_by_id})
                    status_rows = (pipeline.get("_embedded") or {}).get("statuses") or []
                    if any(sid not in {int(stage["id"]) for stage in status_rows}
                           for sid in selected_by_id.get(pid, [])):
                        raise context.platform_error("Seçilən mərhələ bu Kommo vərəqində yoxdur.")
                    for stage in status_rows:
                        old = prior_stages.get((pid, str(stage["id"])), {})
                        visible = not selected_by_id.get(pid) or int(stage["id"]) in selected_by_id[pid]
                        stages.append({**old, "pipeline_id": pid, "stage_id": str(stage["id"]),
                            "name": stage.get("name") or "", "sort_order": int(stage.get("sort") or 0),
                            "stage_type": old.get("stage_type") or "open",
                            "settings": {**old.get("settings", {}), "visible": visible}})
                for pid, old in prior_pipelines.items():
                    if pid not in remote_by_id:
                        pipeline_config.append({**old, "active": False})
                await asyncio.to_thread(context.save_workflow, tenant_id=profile["tenant_id"],
                    owner_id=int(profile["telegram_id"]), pipelines=pipeline_config, stages=stages)
                tenant = await asyncio.to_thread(context.update_onboarding, tenant_id=profile["tenant_id"],
                    owner_id=int(profile["telegram_id"]), patch={"pipeline": {"selected": selected[:30]}})
                return web.json_response({"success": True, "tenant": tenant})
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)

        return {"request": request_connection, "disconnect": disconnect, "start": start,
                "callback": callback, "pipelines": pipelines, "test": test,
                "save_pipelines": save_pipelines}
