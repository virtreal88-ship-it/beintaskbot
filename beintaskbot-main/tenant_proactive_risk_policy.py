"""Deterministic, evidence-labeled risk candidates. No model or external side effects."""
from datetime import datetime, timezone
import hashlib
import json
import re


_SIGNALS = {
    'access': re.compile(r'\b(permission|permissions|access|acl|unauthori[sz]ed|bypass|role|right|rights)\b|icaz[əe]|səlahiyyət|hüquq|icazəsiz|\b(прав\w*|доступ|обход|разрешен\w*)\b', re.I),
    'price': re.compile(r'\b(price|pricing|discount|payment|edit price|change price|modify price)\b|qiymət|ödəniş|endirim|\b(цен\w*|стоимост\w*|скидк\w*|оплат\w*)\b', re.I),
    'outage': re.compile(r'\b(outage|service down|system down|unavailable|incident|crash|failure|downtime)\b|işləmir|dayanıb|nasazlıq|\b(сбой|недоступ\w*|не работает|падени\w*)\b', re.I),
    'action': re.compile(r'\b(can|could|allow|allows|edit|change|changes|changing|modify|modifies|modifying|override|bypass|disable|without|despite|issue|risk|fail|failed|failure|blocked)\b|dəyiş|edə bilər|olmadan|baxmayaraq|mümkün|problem|\b(может|измен\w*|меня\w*|обойти|без права|несмотря|проблем)\b', re.I),
    'urgent_text': re.compile(r'\b(urgent|asap|critical|immediately)\b|təcili|dərhal|kritik|\b(срочно|немедленно|критич)\b', re.I),
}


def _priority(value):
    # Linear uses 0 for "no priority"; 1 is urgent and 2 is high.
    return value if type(value) is int and value in {1, 2, 3, 4} else None


def source_timestamp(value) -> datetime | None:
    """Parse a cache timestamp only when its timezone is explicit."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
        except ValueError:
            return None
    return None


def _finding(rule_id, severity, confidence, facts, assessment, recommendation, source_url=''):
    return {
        'rule_id': rule_id,
        'severity': severity,
        'confidence': confidence,
        'facts': facts,
        'assessment': assessment,
        'recommended_step': recommendation,
        'source_url': source_url if isinstance(source_url, str) and source_url.startswith('https://') else '',
    }


def analyze_linear(issue: dict) -> list[dict]:
    """Analyze only existing issue fields; text remains untrusted source data."""
    text = f"{issue.get('title') or ''}\n{issue.get('description') or ''}"
    access, price, outage, action = (_SIGNALS[key].search(text) is not None
                                    for key in ('access', 'price', 'outage', 'action'))
    priority = _priority(issue.get('priority'))
    assignee = issue.get('assignee') or {}
    facts = []
    if priority is not None:
        facts.append(f"Linear priority field: {priority} (1=urgent, 2=high).")
    elif _SIGNALS['urgent_text'].search(text):
        facts.append('Urgency appears in task text; no structured priority was confirmed.')
    if not assignee.get('id'):
        facts.append('Linear assignee field is empty.')
    due_date = issue.get('dueDate')
    if isinstance(due_date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', due_date):
        facts.append(f'Linear due date field: {due_date} (date only; timezone/overdue status not evaluated).')
    elif due_date:
        facts.append('Linear due date is present but could not be interpreted safely.')

    result = []
    severity = 'P1' if priority == 1 else 'P2'
    if access and action:
        if price:
            result.append(_finding('access_price_claim', severity, 'medium', facts + [
                'Task text mentions a price change and an access/permission concern.'
            ], 'The task reports a possible price-control or permission issue; runtime permissions and any actual price change are unverified. No loss is established.',
                'Check the target system ACL and audit log; assign an owner if missing.', issue.get('url', '')))
        else:
            result.append(_finding('access_control_claim', severity, 'medium', facts + [
                'Task text mentions an access/permission concern.'
            ], 'The task reports a possible access-control issue; runtime permissions are unverified.',
                'Verify the affected role and permission in the target system; assign an owner if missing.', issue.get('url', '')))
    if price and action and not access:
        result.append(_finding('price_control_claim', severity, 'low', facts + [
            'Task text mentions a price or payment change.'
        ], 'The task reports a possible price-control issue; current prices and any actual transaction were not checked. No loss is established.',
            'Check the pricing rules and audit log; assign an owner if missing.', issue.get('url', '')))
    if outage:
        result.append(_finding('service_failure_claim', severity, 'low', facts + [
            'Task text mentions a service failure or outage.'
        ], 'The task text reports a possible service issue; current service health was not checked.',
            'Check current service health and the incident timeline; assign an owner if missing.', issue.get('url', '')))
    if priority in {1, 2} and not assignee.get('id'):
        result.append(_finding('high_priority_unassigned', 'P2', 'high', [
            f"Linear priority field: {priority} (1=urgent, 2=high).",
            'Linear assignee field is empty.'
        ], 'A high-priority task has no assigned owner. This is an organization signal, not evidence of damage.',
            'Assign an accountable owner and confirm the next step.', issue.get('url', '')))
    return result


def analyze_crm_task(task: dict, *, checked_at: datetime | None = None) -> list[dict]:
    """Analyze tenant-owned cached CRM tasks; no provider calls or message content."""
    if task.get('completed') is True:
        return []
    text = str(task.get('text') or '')
    access, price, outage, action = (_SIGNALS[key].search(text) is not None
                                    for key in ('access', 'price', 'outage', 'action'))
    if not (outage or (action and (access or price))):
        return []
    due = task.get('due_at')
    overdue = False
    if isinstance(due, datetime) and due.tzinfo is not None:
        overdue = due.astimezone(timezone.utc) <= (checked_at or datetime.now(timezone.utc)) and task.get('completed') is False
    unassigned = not task.get('responsible_id')
    topic = ('service failure concern' if outage and not (action and (access or price)) else
             'price/access concern' if price and access else 'price-control concern' if price else
             'access concern' if access else 'service failure concern')
    facts = [f"CRM task {task.get('kommo_task_id') or ''} text mentions a {topic}; this is a report, not a verified incident."]
    if due:
        facts.append(f"Task due_at is {due.isoformat() if hasattr(due, 'isoformat') else due}.")
    if overdue:
        facts.append('Task is incomplete and due_at is in the past (UTC comparison).')
    if unassigned:
        facts.append('CRM task responsible_id is empty.')
    rule = 'crm_task_' + ('overdue_' if overdue else '') + ('unassigned_' if unassigned else '') + (
        'outage' if outage and not (action and (access or price)) else
        'price_access' if price and access else 'price' if price else 'access' if access else 'outage')
    return [_finding(rule, 'P2', 'low' if not due else 'medium', facts,
        f'The CRM task reports a possible {topic}; no runtime ACL, price, or service-health evidence was checked. No loss is established.',
        'Verify the underlying system evidence and assign an owner if missing.', '')]


def fingerprint(findings: list[dict]) -> str:
    value = [{k: v for k, v in item.items() if k != 'source_url'} for item in findings]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
