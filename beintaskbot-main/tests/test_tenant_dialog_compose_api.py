from tenant_dialog_compose_api import normalize_international_number, _find_exact_routes
from tenant_linear_policy import TenantLinearError


def test_new_chat_requires_explicit_international_number():
    assert normalize_international_number('+994501234567') == '994501234567'
    for value in ('994501234567', '0501234567', '+0123456789', '+12345', '+1234567890123456', None):
        try:
            normalize_international_number(value)
        except TenantLinearError:
            pass
        else:
            raise AssertionError(f'accepted invalid number: {value!r}')


def test_existing_chat_lookup_is_exact_and_scoped_to_selected_channel():
    class Cursor:
        query = ''
        params = None

        def execute(self, query, params):
            self.query, self.params = query, params

        def fetchall(self):
            return []

    cur = Cursor()
    _find_exact_routes(cur, 'tenant-a', 'channel-a', '994501234567')
    assert 'c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid' in cur.query
    assert 'regexp_replace(COALESCE(c.phone' in cur.query
    assert 'c.conversation_id=ANY(%s::text[])' in cur.query
    assert 'i.tenant_id=c.tenant_id AND i.connect_channel_id=c.connect_channel_id' in cur.query
    assert 'LIKE' not in cur.query.upper() or 'NOT LIKE' in cur.query.upper()
    assert cur.params[0:3] == ('tenant-a', 'channel-a', '994501234567')
    assert cur.params[3] == ['994501234567@s.whatsapp.net', '994501234567@c.us']

