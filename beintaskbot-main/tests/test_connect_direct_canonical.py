"""Fast contract tests for channel-scoped direct identity routing."""
import tenant_connect_transport as transport


def test_only_real_direct_whatsapp_ids_are_identity_keys():
    assert transport._direct_identity('994501234567@s.whatsapp.net') == '994501234567@s.whatsapp.net'
    assert transport._direct_identity('123456789@lid') == '123456789@lid'
    assert transport._direct_identity('120363411822115409@g.us') == ''
    assert transport._direct_identity('not-a-phone@s.whatsapp.net') == ''


def test_unpaired_or_group_identity_never_canonicalizes():
    class Cursor:
        def execute(self, *_args):
            raise AssertionError('unverified identities must not query or create a mapping')
    cursor=Cursor()
    assert transport._canonical_direct_conversation(cursor,'tenant','channel','123@lid',
        {'directIdentity':{'lid':'123@lid'}},{'kind':'contact'}) == ('123@lid',False)
    assert transport._canonical_direct_conversation(cursor,'tenant','channel','120@g.us',
        {'directIdentity':{'lid':'123@lid','jid':'994501234567@s.whatsapp.net'}},{'kind':'group'}) == ('120@g.us',False)
