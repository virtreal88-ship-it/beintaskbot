import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import tenant_connect_access_api
from tenant_connect_access_api import ConnectAccessContext, handler


class Request:
    method = 'GET'


class ConnectAccessApiTests(unittest.TestCase):
    def test_get_serializes_datetime_in_entitlement_payload(self):
        access = Mock(return_value={
            'entitlement': {'status': 'active', 'updated_at': datetime(2026, 10, 15, tzinfo=timezone.utc)},
            'members': [],
        })
        endpoint = handler(ConnectAccessContext(
            member=Mock(return_value={'tenant_id': 'tenant-a', 'telegram_id': 7}),
            access=access, update_entitlement=Mock(), set_member=Mock(),
            platform_error=RuntimeError,
        ))

        async def inline_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(tenant_connect_access_api.asyncio, 'to_thread', inline_to_thread):
            task = endpoint(Request())
            try:
                task.send(None)
            except StopIteration as complete:
                response = complete.value
            else:
                self.fail('endpoint unexpectedly yielded')

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['entitlement']['updated_at'], '2026-10-15 00:00:00+00:00')
        access.assert_called_once_with(tenant_id='tenant-a', owner_id=7)


if __name__ == '__main__':
    unittest.main()
