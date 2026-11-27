"""Display facts from WhatsApp shares. No raw vCards or provider URLs."""
import re


KINDS = frozenset({"contact", "location"})
_PHONE = re.compile(r"\+?[0-9]{3,20}\Z")


def _label(value, limit):
    return value.replace("\x00", "").strip()[:limit] if isinstance(value, str) else ""


def facts(kind, data):
    if kind == "contact":
        contacts = data.get("contacts")
        result = []
        if isinstance(contacts, list):
            for item in contacts[:32]:
                if not isinstance(item, dict):
                    continue
                name = _label(item.get("name"), 256)
                phones = []
                values = item.get("phones")
                if isinstance(values, list):
                    phones = list(dict.fromkeys(
                        p for p in values[:12] if isinstance(p, str) and _PHONE.fullmatch(p)
                    ))
                if name or phones:
                    result.append({"name": name, "phones": phones})
        return {"contacts": result}
    if kind == "location":
        location = data.get("location")
        if isinstance(location, dict):
            latitude, longitude = location.get("latitude"), location.get("longitude")
            if (type(latitude) in (int, float) and -90 <= latitude <= 90
                    and type(longitude) in (int, float) and -180 <= longitude <= 180):
                return {"location": {"latitude": latitude, "longitude": longitude,
                                     "name": _label(location.get("name"), 256),
                                     "address": _label(location.get("address"), 512)}}
    return {}
