"""Explicit public, localized marketplace pages (no session or customer data)."""

from pathlib import Path

from aiohttp import web


def localized_page(request: web.Request, kind: str) -> web.FileResponse | None:
    """Only select known files; never interpolate untrusted query text into paths."""
    language = request.query.get("lang", "")
    filenames = {
        ("site", "en"): "integration-en.html",
        ("site", "ru"): "integration-ru.html",
        ("privacy", "en"): "privacy-policy-en.html",
        ("privacy", "ru"): "privacy-policy.html",
    }
    filename = filenames.get((kind, language))
    if filename is None:
        return None
    if kind == "site" and request.query.get("tour") in {"deals", "chat"}:
        filename = "integration-tour.html"
    path = Path(__file__).resolve().parent / "docs" / filename
    if not path.is_file():
        raise web.HTTPNotFound(text="Public information page not found")
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
