"""Bounded read-only Linear adapter. Never falls back to a global API key."""
import requests
from tenant_linear_policy import TenantLinearError


def query(key, document, variables=None):
    try:
        response = requests.post('https://api.linear.app/graphql',
            headers={'Authorization': key, 'Content-Type': 'application/json'},
            json={'query': document, 'variables': variables or {}}, timeout=(5, 20), allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('data'), dict):
            raise ValueError('Invalid provider response')
        return payload['data']
    except (requests.RequestException, ValueError):
        # Provider responses may contain credentials/customer details: do not forward/log them.
        raise TenantLinearError('Linear məlumatları alınmadı. Açarı və icazələri yoxlayın.', 502) from None


def verify(key):
    if not isinstance(key, str) or not 10 <= len(key.strip()) <= 512 or any(ord(c) < 33 for c in key.strip()):
        raise TenantLinearError('Linear API açarı düzgün deyil.')
    data = query(key.strip(), 'query { viewer { id } }')
    if not isinstance(data.get('viewer'), dict) or not data['viewer'].get('id'):
        raise TenantLinearError('Linear hesabı təsdiqlənmədi.', 502)


def catalog(key, after=None):
    # One explicit page; no full workspace loads and no shared cross-tenant cache.
    return query(key, '''query($after:String) {
      teams(first:20,after:$after) { nodes { id name key } pageInfo { hasNextPage endCursor } }
    }''', {'after': after})['teams']


def team_catalog(key, team_id):
    return query(key, '''query($id:String!) { team(id:$id) { id name
      states(first:50) { nodes { id name type } pageInfo { hasNextPage } }
      projects(first:50) { nodes { id name } pageInfo { hasNextPage } }
    } }''', {'id': team_id}).get('team')
