"""
GitHub-based JSON storage for balance and KPI data.
Stores data in JSON files on a 'data' branch to avoid triggering redeploys.
"""
import json
import base64
import logging
import os
import threading
import uuid
import requests
import psycopg
from datetime import datetime, timezone
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

_GH_TOKEN = None
_GH_REPO = "virtreal88-ship-it/beintaskbot"
_GH_BRANCH = "data"
_GH_API = "https://api.github.com"
_DATABASE_URL = None
_pg_schema_ready = False
_pg_schema_lock = threading.Lock()

# In-memory cache
_cache = {}
_cache_sha = {}
_lock = threading.Lock()


def init_storage(token: str):
    global _GH_TOKEN, _DATABASE_URL
    _GH_TOKEN = token
    _DATABASE_URL = str(os.environ.get("DATABASE_URL") or "").strip() or None
    # Always refresh persisted data on startup, even if the cache was populated earlier.
    # When DATABASE_URL exists, PostgreSQL is the primary store. Documents from
    # the legacy GitHub data branch are copied lazily on their first read.
    _load_file("balance.json", force=True)
    _load_file("kpi.json", force=True)


def _headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if _GH_TOKEN:
        headers["Authorization"] = f"token {_GH_TOKEN}"
    return headers


def _ensure_postgres_schema(conn) -> None:
    global _pg_schema_ready
    if _pg_schema_ready:
        return
    with _pg_schema_lock:
        if _pg_schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_documents (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
        conn.commit()
        _pg_schema_ready = True


def _postgres_load_file(filename: str, force: bool = False):
    """Read a document from PostgreSQL, importing its GitHub legacy value once."""
    try:
        with psycopg.connect(_DATABASE_URL, row_factory=dict_row) as conn:
            _ensure_postgres_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM app_documents WHERE key = %s", (filename,))
                row = cur.fetchone()
            if row is not None:
                data = row["value"]
                # Most documents are objects, but a few important records
                # (notably pending_actions.json) are arrays.  Converting an
                # array to {} here made every unresolved confirmation vanish
                # after a Railway restart even though PostgreSQL still held
                # the original value.
                if not isinstance(data, (dict, list)):
                    data = {}
                with _lock:
                    _cache[filename] = data
                    _cache_sha[filename] = "postgres"
                return data
    except Exception as exc:
        logger.error("PostgreSQL load %s failed: %s", filename, exc)
        # A temporary database outage must not wipe the running CRM cache.
        with _lock:
            if filename in _cache:
                return _cache[filename]
        return _github_load_file(filename, force=force)

    # First PostgreSQL read: migrate the existing document without deleting the
    # GitHub source. This gives the current installation a reversible rollout.
    legacy = _github_load_file(filename, force=force)
    if _postgres_save_file(filename, legacy):
        return legacy
    return legacy


def _postgres_save_file(filename: str, data: dict) -> bool:
    try:
        payload = json.dumps(data, ensure_ascii=False)
        with psycopg.connect(_DATABASE_URL) as conn:
            _ensure_postgres_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO app_documents (key, value, updated_at)
                    VALUES (%s, %s::jsonb, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """, (filename, payload))
            conn.commit()
        with _lock:
            _cache_sha[filename] = "postgres"
        return True
    except Exception as exc:
        logger.error("PostgreSQL save %s failed: %s", filename, exc)
        return False


def _load_file(filename: str, force: bool = False) -> dict:
    """Load from PostgreSQL when configured, otherwise use legacy GitHub JSON."""
    with _lock:
        if not force and filename in _cache:
            return _cache[filename]
    if _DATABASE_URL:
        return _postgres_load_file(filename, force=force)
    return _github_load_file(filename, force=force)


def _github_load_file(filename: str, force: bool = False) -> dict:
    """Load a JSON file from the legacy GitHub data branch into cache."""
    with _lock:
        if not force and filename in _cache:
            return _cache[filename]
    url = f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}?ref={_GH_BRANCH}"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        # A stale or scope-limited GH_TOKEN makes GitHub reject even a public
        # repository request. Read-only fallback keeps existing balances and
        # pending approvals available while the write token is rotated in
        # Railway. No token value is logged or exposed.
        if r.status_code in (401, 403) and _GH_TOKEN:
            fallback = requests.get(url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15)
            if fallback.status_code == 200:
                r = fallback
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            data = json.loads(content)
            with _lock:
                _cache[filename] = data
                _cache_sha[filename] = r.json()["sha"]
            return data
        else:
            logger.error("gh_storage load %s returned HTTP %s", filename, r.status_code)
            with _lock:
                if filename not in _cache:
                    _cache[filename] = {}
                _cache_sha[filename] = None
            return _cache.get(filename, {})
    except Exception as e:
        logger.error(f"gh_storage load {filename}: {e}")
        with _lock:
            if filename not in _cache:
                _cache[filename] = {}
                _cache_sha[filename] = None
        return _cache.get(filename, {})


def _save_file(filename: str):
    """Save cached data to PostgreSQL, or legacy GitHub when PostgreSQL is absent."""
    with _lock:
        data = _cache.get(filename, {})
    if _DATABASE_URL:
        return _postgres_save_file(filename, data)
    # Always fetch fresh SHA before saving to avoid conflicts after redeploy
    logger.info(f"gh_storage _save_file({filename}): starting save...")
    try:
        r_sha = requests.get(
            f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}?ref={_GH_BRANCH}",
            headers=_headers(), timeout=15
        )
        if r_sha.status_code == 200:
            sha = r_sha.json()["sha"]
            with _lock:
                _cache_sha[filename] = sha
        else:
            sha = None
            with _lock:
                _cache_sha[filename] = None
    except:
        sha = _cache_sha.get(filename)
    content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    payload = {
        "message": f"Update {filename}",
        "content": content,
        "branch": _GH_BRANCH
    }
    if sha:
        payload["sha"] = sha

    for attempt in range(3):
        try:
            r = requests.put(
                f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}",
                headers=_headers(), json=payload, timeout=15
            )
            if r.status_code in (200, 201):
                logger.info(f"gh_storage _save_file({filename}): SUCCESS")
                with _lock:
                    _cache_sha[filename] = r.json()["content"]["sha"]
                return True
            elif r.status_code in (409, 422):
                # SHA conflict or file exists without sha: refetch and retry.
                logger.warning(
                    f"gh_storage save {filename}: {r.status_code}, "
                    f"refetching SHA (attempt {attempt + 1})"
                )
                try:
                    r2 = requests.get(
                        f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}?ref={_GH_BRANCH}",
                        headers=_headers(), timeout=15
                    )
                    if r2.status_code == 200:
                        new_sha = r2.json()["sha"]
                        with _lock:
                            _cache_sha[filename] = new_sha
                        payload["sha"] = new_sha
                    else:
                        payload.pop("sha", None)
                        with _lock:
                            _cache_sha[filename] = None
                except Exception as e2:
                    logger.error(f"gh_storage refetch SHA error: {e2}")
                continue
            else:
                logger.error(f"gh_storage save {filename}: {r.status_code} {r.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"gh_storage save {filename} error: {e}")
            return False
    return False


def read_json(filename: str):
    """Read an arbitrary JSON document from the GitHub-backed data branch."""
    data = _load_file(filename)
    # Return a detached value so callers cannot mutate the shared cache without
    # going through write_json().
    return json.loads(json.dumps(data, ensure_ascii=False))


def write_json(filename: str, data) -> bool:
    """Persist an arbitrary JSON-serializable document to the data branch."""
    detached = json.loads(json.dumps(data, ensure_ascii=False))
    with _lock:
        _cache[filename] = detached
    return _save_file(filename)
