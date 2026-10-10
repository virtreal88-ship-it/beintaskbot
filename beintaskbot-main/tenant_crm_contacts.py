"""Bounded contact enrichment for a single tenant's lead sync page."""
from tenant_policy import positive_id
from tenant_crm_sync_data import PAGE_SIZE, page_items


async def enrich_contacts(tenant_id: str, leads: list[dict], request) -> list[dict]:
    contacts = {}
    # One page contains at most 250 leads. Bound unusually many linked
    # contacts as well; only the primary contact is displayed in the CRM.
    primary_ids = sorted({positive_id(primary_contact(lead).get('id')) for lead in leads} - {0})
    for start in range(0, len(primary_ids), 100):
        batch = primary_ids[start:start + 100]
        payload = await request(tenant_id, 'GET', 'contacts',
                                params={'limit': PAGE_SIZE, 'filter[id][]': batch})
        for contact in page_items(payload, 'contacts'):
            cid = positive_id(contact.get('id'))
            if cid in batch:
                contacts[cid] = contact
    result = []
    for lead in leads:
        linked = (lead.get('_embedded') or {}).get('contacts') or []
        result.append({**lead, '_embedded': {**(lead.get('_embedded') or {}), 'contacts': [
            {**item, **contacts.get(positive_id(item.get('id')), {})}
            if isinstance(item, dict) else item for item in linked]}})
    return result


def primary_contact(lead: dict) -> dict:
    contacts = [item for item in (lead.get('_embedded') or {}).get('contacts') or [] if isinstance(item, dict)]
    return next((item for item in contacts if item.get('is_main')), contacts[0] if contacts else {})
