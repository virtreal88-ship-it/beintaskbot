"""Deal card fields and a per-connection Kommo user directory."""
import asyncio
from collections import OrderedDict
import time

from tenant_crm_relation_identity import current_binding
from tenant_kommo_board_provenance import revisions
from tenant_platform_core import _connect
from tenant_policy import TenantPolicy, positive_id
from tenant_task_flow import TaskFlowError, _profiles


def _objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value, limit=240):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _relations(raw):
    embedded = raw.get('_embedded')
    embedded = embedded if isinstance(embedded, dict) else {}
    contacts = _objects(embedded.get('contacts'))
    primary = [contact for contact in contacts if contact.get('is_main') is True]
    contact = primary[0] if len(primary) == 1 else contacts[0] if len(contacts) == 1 else {}
    companies = _objects(embedded.get('companies'))
    company = companies[0] if len(companies) == 1 and positive_id(companies[0].get('id')) else {}
    return contact, company


def _contact_field(contact, code):
    for field in _objects(contact.get('custom_fields_values')):
        if field.get('field_code') == code:
            for entry in _objects(field.get('values')):
                if value := _text(entry.get('value')):
                    return value
    return ''


def snapshot_fields(deal):
    raw = deal.get('raw') or {}
    contact, company = _relations(raw)
    fields = []
    for field in _objects(raw.get('custom_fields_values'))[:40]:
        label = _text(field.get('field_name'))
        values = [entry['value'] for entry in _objects(field.get('values'))[:10]
                  if type(entry.get('value')) in (str, int, float)]
        if label and values:
            fields.append({'label': label, 'value': ', '.join(str(value)[:500] for value in values)})
    return {
        'responsible_id': positive_id(raw.get('responsible_user_id')),
        'responsible_name': '',
        'company_linked': bool(company),
        'company_name': _text(company.get('name')),
        'contact_name': _text(contact.get('name')) or deal.get('contact_name'),
        'contact_phone': _contact_field(contact, 'PHONE') if 'custom_fields_values' in contact else deal.get('phone'),
        'contact_email': _contact_field(contact, 'EMAIL'),
        'price': raw.get('price') if type(raw.get('price')) in (int, float) else None,
        'detail_fields': fields,
    }


def _authorized_binding(profile, deal):
    tenant = str(profile['tenant_id'])
    lead = str(deal['kommo_lead_id'])
    with _connect() as db:
        with db.cursor() as cursor:
            binding = current_binding(cursor, tenant, lock=False)
            try:
                current, _ = _profiles(cursor, tenant, int(profile['telegram_id']))
            except TaskFlowError:
                return None
            if not binding or not current or not TenantPolicy(current).can_read_kommo_chat(deal):
                return None
            proof = revisions(cursor, tenant, 'leads', [lead], binding, expected_raw={lead: deal['raw']})
            if not proof.get(lead):
                return None
            return binding


class DealDetails:
    def __init__(self, request):
        self.request = request
        self.cache = OrderedDict()
        self.pending = {}

    async def _load_users(self, binding):
        users = {}
        ttl = 900
        try:
            for page in range(1, 9):
                result = await self.request(binding, 'GET', 'users', params={'page': page, 'limit': 250})
                rows = _objects((result.get('_embedded') or {}).get('users'))
                for row in rows:
                    ident, name = positive_id(row.get('id')), _text(row.get('name'))
                    if ident and name:
                        users[ident] = name
                if len(rows) < 250 and not (result.get('_links') or {}).get('next'):
                    break
        except Exception:
            # Failed/forbidden directory reads are also throttled.
            users, ttl = {}, 60
        self.cache[binding] = (time.monotonic() + ttl, users)
        self.cache.move_to_end(binding)
        if len(self.cache) > 128:
            self.cache.popitem(last=False)
        return users

    async def _users(self, binding):
        expires, users = self.cache.get(binding, (0, {}))
        if time.monotonic() < expires:
            return users
        if binding not in self.pending:
            task = asyncio.create_task(self._load_users(binding))
            self.pending[binding] = task
            task.add_done_callback(lambda _: self.pending.pop(binding, None))
        return await asyncio.shield(self.pending[binding])

    async def read(self, profile, deal):
        assignee = positive_id(deal['raw'].get('responsible_user_id'))
        if not assignee:
            return {}
        binding = await asyncio.to_thread(_authorized_binding, profile, deal)
        if not binding:
            return {}
        name = (await self._users(binding)).get(assignee)
        if name and await asyncio.to_thread(_authorized_binding, profile, deal) == binding:
            return {'responsible_name': name}
        return {}
