import unittest
from unittest.mock import AsyncMock
from tenant_crm_contacts import enrich_contacts
from tenant_crm_sync_data import normalize_page


class ContactsTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_contact_is_enriched_without_mutating_lead(self):
        lead = {'id': 10, '_embedded': {'contacts': [{'id': 1}, {'id': 2, 'is_main': True}]}}
        request = AsyncMock(return_value={'_embedded': {'contacts': [
            {'id': 2, 'name': 'Aysel', 'custom_fields_values': [
                {'field_code': 'PHONE', 'values': [{'value': '+994551234567'}]}]}]}})
        result = await enrich_contacts('company-a', [lead, lead], request)
        request.assert_awaited_once_with('company-a', 'GET', 'contacts',
                                        params={'limit': 250, 'filter[id][]': [2]})
        row = normalize_page('leads', result, {})[0]
        self.assertEqual((row['contact_name'], row['phone']), ('Aysel', '+994551234567'))
        self.assertNotIn('name', lead['_embedded']['contacts'][1])

    async def test_bounded_batches(self):
        leads = [{'id': i, '_embedded': {'contacts': [{'id': i}]}} for i in range(1, 251)]
        request = AsyncMock(return_value={'_embedded': {'contacts': []}})
        await enrich_contacts('company-b', leads, request)
        self.assertEqual(request.await_count, 3)
        self.assertEqual([len(call.kwargs['params']['filter[id][]']) for call in request.await_args_list], [100, 100, 50])

    async def test_no_contacts_no_request(self):
        request = AsyncMock()
        self.assertEqual(await enrich_contacts('a', [{'id': 1}], request),
                         [{'id': 1}])
        request.assert_not_awaited()

    async def test_failure_is_not_silently_converted_to_empty_contact(self):
        with self.assertRaises(RuntimeError):
            await enrich_contacts('a', [{'id': 1, '_embedded': {'contacts': [{'id': 9}]}}],
                                  AsyncMock(side_effect=RuntimeError('provider unavailable')))

    async def test_company_enrichment_matches_only_linked_ids_and_preserves_proof(self):
        from tenant_crm_import_provenance import board_observation_digest
        lead = {'id':9,'_embedded':{'companies':[{'id':31}]}}
        request = AsyncMock(return_value={'_embedded':{'companies':[
            {'id':32,'name':'unrelated'},{'id':31,'name':'Matching company'}]}})
        updated, = await enrich_contacts('tenant',[lead],request)
        self.assertEqual(updated['_embedded']['companies'], [{'id':31,'name':'Matching company'}])
        original = lead
        self.assertEqual(board_observation_digest('leads', updated),board_observation_digest('leads',original))
        updated['_embedded']['companies'][0]['id'] = 32
        self.assertNotEqual(board_observation_digest('leads', updated),board_observation_digest('leads',original))

    async def test_company_denial_does_not_discard_authorized_lead(self):
        lead = {'id':9, '_embedded':{'companies':[{'id':31}]}}
        denied = RuntimeError('provider denied companies')
        denied.provider_status = 403
        self.assertEqual(await enrich_contacts('tenant',[lead],AsyncMock(side_effect=denied)), [lead])
