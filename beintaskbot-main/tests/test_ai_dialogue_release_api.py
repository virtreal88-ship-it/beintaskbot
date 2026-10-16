import asyncio
import json
from unittest.mock import Mock, patch
from aiohttp.test_utils import make_mocked_request
import tenant_ai_agents_api as api


def test_get_handoffs_routes_to_dialogues_not_knowledge():
    profile={'tenant_id':'company','telegram_id':11}
    rows=[{'provider':'connect','conversation_id':'client','state':'human'}]
    request=make_mocked_request('GET','/api/platform/ai/handoffs?limit=100')
    with patch.object(api,'list_handoffs',return_value=rows) as listing, patch.object(api,'list_knowledge') as knowledge:
        response=asyncio.run(api.handler(lambda r:profile,'https://crm.test',Mock(),'handoffs')(request))
    assert response.status==200
    assert json.loads(response.text)['result']==rows
    listing.assert_called_once_with(profile,'100')
    knowledge.assert_not_called()
