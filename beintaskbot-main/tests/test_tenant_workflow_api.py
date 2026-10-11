import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tenant_workflow_api import WorkflowApiContext, handlers


class PlatformError(RuntimeError):
    pass


class Request:
    def __init__(self, method="GET", payload=None):
        self.method = method
        self.payload = payload

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class WorkflowApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {"tenant_id": "company-a", "telegram_id": 10, "role": "owner"}
        self.member = Mock(side_effect=lambda request, owner_only=False: self.profile)
        self.load = Mock(return_value={"pipelines": [], "stages": [], "policies": {}})
        self.save = Mock(return_value={"pipelines": [{"pipeline_id": "1"}], "stages": [], "policies": {}})
        self.crm = AsyncMock(return_value={"_embedded": {"pipelines": [{
            "id": 1, "name": "Sales", "_embedded": {"statuses": [
                {"id": 2, "name": "New", "sort": 10},
            ]},
        }]}})
        self.context = WorkflowApiContext(
            member=self.member, load_workflow=self.load, save_workflow=self.save,
            crm_request=self.crm, role_permissions={"worker": ["tasks"]},
            platform_error=PlatformError, logger=Mock(),
        )
        self.api = handlers(self.context)

    async def test_config_is_owner_only(self):
        self.member.side_effect = None
        self.member.return_value = None
        response = await self.api["config"](Request())
        self.assertEqual(response.status, 403)
        self.load.assert_not_called()

    async def test_get_keeps_public_contract(self):
        response = await self.api["config"](Request())
        self.assertEqual(response.status, 200)
        data = response.body.decode()
        self.assertIn('"success": true', data)
        self.assertIn('"role_permissions": {"worker": ["tasks"]}', data)
        self.load.assert_called_once_with(tenant_id="company-a", owner_id=10)

    async def test_post_passes_only_workflow_fields(self):
        payload = {"pipelines": [1], "stages": [2], "policies": {"x": True},
                   "members": [3], "tenant_id": "other"}
        response = await self.api["config"](Request("POST", payload))
        self.assertEqual(response.status, 200)
        self.save.assert_called_once_with(
            tenant_id="company-a", owner_id=10, pipelines=[1], stages=[2],
            policies={"x": True}, members=[3])

    async def test_invalid_body_and_platform_error_are_client_errors(self):
        self.assertEqual((await self.api["config"](Request("POST", []))).status, 400)
        self.save.side_effect = PlatformError("bad workflow")
        response = await self.api["config"](Request("POST", {}))
        self.assertEqual(response.status, 400)
        self.assertIn("bad workflow", response.body.decode())

    async def test_catalog_reads_only_pipelines_and_keeps_users_empty(self):
        response = await self.api["catalog"](Request())
        self.assertEqual(response.status, 200)
        body = response.body.decode()
        self.assertIn('"pipeline_id": "1"', body)
        self.assertIn('"stage_id": "2"', body)
        self.assertIn('"users": []', body)
        self.crm.assert_awaited_once_with("company-a", "GET", "leads/pipelines", params=None)

    async def test_catalog_failure_is_a_non_destructive_warning(self):
        self.crm.side_effect = RuntimeError("offline")
        response = await self.api["catalog"](Request())
        self.assertEqual(response.status, 200)
        self.assertIn("Kommo", response.body.decode())
        self.context.logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
