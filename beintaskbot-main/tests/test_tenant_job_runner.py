import asyncio
import unittest
from unittest.mock import Mock, AsyncMock
from tenant_job_runner import run_jobs


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_failed_queue_does_not_stop_other_queues_or_leak_error(self):
        first=AsyncMock(side_effect=RuntimeError('PRIVATE_TOKEN'));second=AsyncMock();logger=Mock()
        await run_jobs([('first',first),('second',second)],logger)
        second.assert_awaited_once();self.assertNotIn('PRIVATE',str(logger.mock_calls))

    async def test_cancellation_propagates(self):
        first=AsyncMock(side_effect=asyncio.CancelledError());second=AsyncMock()
        with self.assertRaises(asyncio.CancelledError):await run_jobs([('first',first),('second',second)],Mock())
        second.assert_not_awaited()
