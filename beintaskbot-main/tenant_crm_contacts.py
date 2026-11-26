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
    # Provider relations often carry only a company ID. Resolve those IDs
    # once per batch, keeping the original relationship and source scope.
    missing = {positive_id(company.get('id')) for lead in leads
               for company in (lead.get('_embedded') or {}).get('companies') or []
               if isinstance(company, dict) and not company.get('name')} - {0}
    company_names = {}
    ids = sorted(missing)
    for start in range(0, len(ids), 100):
        batch = ids[start:start + 100]
        try:
            payload = await request(tenant_id, 'GET', 'companies',
                                    params={'limit': PAGE_SIZE, 'filter[id][]': batch})
        except Exception as error:
            if getattr(error, 'provider_status', None) == 403:
                break  # Contact and deal snapshots remain available without company access.
            raise
        for company in page_items(payload, 'companies'):
            ident = positive_id(company.get('id'))
            if ident in batch and isinstance(company.get('name'), str):
                company_names[ident] = company['name'][:500]
    result = []
    for lead in leads:
        linked = (lead.get('_embedded') or {}).get('contacts') or []
        embedded = dict(lead.get('_embedded') or {})
        if company_names and isinstance(embedded.get('companies'), list):
            embedded['companies'] = [{**item, 'name': company_names[positive_id(item['id'])]}
                if isinstance(item, dict) and positive_id(item.get('id')) in company_names else item
                for item in embedded['companies']]
        if isinstance(embedded.get('contacts'), list):
            embedded['contacts'] = [{**item, **contacts.get(positive_id(item.get('id')), {})}
                                    if isinstance(item, dict) else item for item in linked]
        result.append({**lead, '_embedded': embedded} if embedded or '_embedded' in lead else lead)
    return result


def primary_contact(lead: dict) -> dict:
    contacts = [item for item in (lead.get('_embedded') or {}).get('contacts') or [] if isinstance(item, dict)]
    return next((item for item in contacts if item.get('is_main')), contacts[0] if contacts else {})
