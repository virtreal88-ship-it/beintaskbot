"""Kommo-native deal tags. Mutations use deltas, never overwrite unseen tags."""
import asyncio

from aiohttp import web


def public_tags(lead):
    return [{"id": int(row["id"]), "name": str(row.get("name") or "")}
            for row in (lead.get("_embedded") or {}).get("tags") or []
            if isinstance(row, dict) and row.get("id")]


def tag_ref(value):
    if not isinstance(value, dict):
        raise ValueError("Teq seçin.")
    if value.get("id") is not None:
        if isinstance(value["id"], bool) or not str(value["id"]).isdigit() or int(value["id"]) <= 0:
            raise ValueError("Teq düzgün deyil.")
        return {"id": int(value["id"])}
    name = str(value.get("name") or "").strip()
    if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
        raise ValueError("Teq adı 1–100 simvol olmalıdır.")
    return {"name": name}


def tag_list(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("Ən çox 50 teq seçin.")
    refs = [tag_ref(row) for row in value]
    return [row for index, row in enumerate(refs) if row not in refs[:index]]


def mutation(data, lead):
    # https://developers.kommo.com/reference/updating-single-lead
    operation = data.get("operation")
    if operation not in {"add", "remove", "replace"}:
        raise ValueError("Əməliyyat düzgün deyil.")
    payload = {}
    if operation in {"remove", "replace"}:
        old = tag_ref({"id": data.get("remove_id")})
        if old["id"] not in {row["id"] for row in public_tags(lead)}:
            raise ValueError("Teq artıq sövdələşmədə yoxdur. Yeniləyin.")
        payload["tags_to_delete"] = [old]
    if operation in {"add", "replace"}:
        payload["tags_to_add"] = [tag_ref(data.get("tag"))]
    return payload


def tags_handler(*, identify, authorize, http, base_url, headers, update, invalidate, logger):
    """Mounted behind the signed-session and module-permission middleware."""
    async def handle(request):
        response_headers = {"Cache-Control": "no-store"}
        try:
            user = identify(request)
            if not user:
                return web.json_response({"success": False}, status=401, headers=response_headers)
            data = await request.json() if request.method == "POST" else request.query
            if request.method == "POST" and not isinstance(data, dict):
                raise ValueError("Invalid JSON object")
            lead_id = int(data.get("lead_id") or 0)
            lead = None
            if lead_id:
                lead, error = await asyncio.to_thread(authorize, user, lead_id)
                if error is not None:
                    return error
            if request.method == "POST":
                if not lead_id or not lead:
                    raise ValueError("Sövdələşmə seçin.")
                payload = mutation(data, lead)
                result = await asyncio.to_thread(update, lead_id, payload)
                if not result:
                    return web.json_response({"success": False, "error": "Teqlər Kommo-da saxlanmadı."},
                                             status=502, headers=response_headers)
                invalidate()
                # PATCH responses may contain only IDs. Do not mistake that for an empty tag set.
                fresh, error = await asyncio.to_thread(authorize, user, lead_id)
                tags = public_tags(fresh) if fresh and error is None else None
                return web.json_response({"success": True, "tags": tags}, headers=response_headers)
            page = max(1, min(int(data.get("page") or 1), 10000))
            query = str(data.get("query") or "").strip()[:100]
            def catalog():
                result = http.get(base_url + "/api/v4/leads/tags", headers=headers,
                                  params={"page": page, "limit": 50, "query": query}, timeout=8)
                if result.status_code == 204:
                    return [], False
                if result.status_code != 200:
                    raise RuntimeError("Kommo tags unavailable")
                body = result.json()
                return public_tags(body), bool((body.get("_links") or {}).get("next"))
            rows, more = await asyncio.to_thread(catalog)
            return web.json_response({"success": True, "catalog": rows, "more": more,
                                      "tags": public_tags(lead) if lead else []}, headers=response_headers)
        except (ValueError, TypeError):
            return web.json_response({"success": False, "error": "Teq və ya sövdələşmə düzgün deyil."},
                                     status=400, headers=response_headers)
        except Exception:
            logger.warning("Kommo deal tag operation failed", exc_info=True)
            return web.json_response({"success": False, "error": "Kommo teqləri yüklənmədi. Yenidən cəhd edin."},
                                     status=502, headers=response_headers)
    return handle
