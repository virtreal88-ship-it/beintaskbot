"""Shared, side-effect-free metadata for Linear cards and archived news."""
import re


def issue_environment(issue: dict) -> str:
    """Resolve environment labels without interpreting unrelated labels."""
    labels = issue.get("labels") or []
    if isinstance(labels, dict):
        labels = labels.get("nodes") or []
    environments: list[str] = []
    for label in labels:
        name = str(label.get("name") or "") if isinstance(label, dict) else str(label or "")
        name = name.strip().upper()
        if name in {"ONLINE", "BETA", "DEV", "DEPLOY"} and name not in environments:
            environments.append(name)
    if environments:
        return ", ".join(environments)
    match = re.search(r"^\s*(?:Mühit|Muhit|Environment):\s*(.+)$", str(issue.get("description") or ""), re.I | re.M)
    for value in (match.group(1) if match else None, issue.get("environment")):
        clean = str(value or "").strip()
        if clean and clean.casefold() != "göstərilməyib":
            return clean
    return "Göstərilməyib"


def news_environment(item: dict) -> str:
    """Recover older news metadata from its stored Linear snapshot, not live API."""
    raw = item.get("raw")
    linear = raw.get("linear") if isinstance(raw, dict) else None
    value = issue_environment(linear) if isinstance(linear, dict) else "Göstərilməyib"
    return value if value != "Göstərilməyib" else issue_environment(item)
