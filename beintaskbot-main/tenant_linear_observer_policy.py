"""Side-effect-free observer decisions: no backfilled alarms or invented news."""
from datetime import datetime, timezone
import uuid


def source_time(value: str) -> datetime:
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Source timestamp requires timezone')
    return result.astimezone(timezone.utc)


def event_kind(config: dict, previous: dict | None, issue: dict, *, baseline: bool) -> str | None:
    state = (issue.get('state') or {}).get('id')
    if baseline or not previous or previous['state_id'] == state or source_time(issue['updatedAt']) <= previous['source_version']:
        return None
    if state not in config.get('workflow', {}).get('notification_state_ids', []):
        return None
    return 'linear_done' if state in config.get('done_state_ids', []) else 'linear_status_changed'


def event_id(tenant: str, issue: dict) -> str:
    return str(uuid.uuid5(uuid.UUID(tenant), issue['id'] + ':' + source_time(issue['updatedAt']).isoformat()))


def news_candidate(config: dict, issue: dict) -> bool:
    settings = config.get('news', {})
    if settings.get('enabled') is not True or 'parent' not in issue or issue.get('parent'):
        return False
    if (issue.get('state') or {}).get('id') not in config.get('done_state_ids', []):
        return False
    if (issue.get('project') or {}).get('id') not in settings.get('projects', []):
        return False
    # Only explicit labels are confident exclusions; ambiguous tasks remain for review.
    labels = {str(row.get('name') or '').strip().casefold() for row in (issue.get('labels') or {}).get('nodes', [])}
    return not labels.intersection({'bug', 'bug fix', 'bugfix', 'optimization', 'optimisation', 'refactoring'})
