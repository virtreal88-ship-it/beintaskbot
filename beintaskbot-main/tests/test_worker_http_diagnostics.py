import asyncio
import logging
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from worker_http_diagnostics import HTTPWitness, listener_snapshot, probe_local, stack_locations


class ListenerTests(unittest.TestCase):
    def test_proc_distinguishes_absent_unknown_and_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tcp"
            self.assertEqual(listener_snapshot(8080, path), {"listening": None})
            path.write_text(" sl local_address rem_address st tx_queue:rx_queue\n"
                            " 0: 00000000:1F90 00000000:0000 0A 00000000:00000005\n")
            self.assertEqual(listener_snapshot(8080, path), {"listening": True, "accept_queue": 5})
            self.assertEqual(listener_snapshot(8081, path), {"listening": False})

    def test_closed_port(self):
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.assertEqual(probe_local(reserve.getsockname()[1], 5), "refused")

    def test_connect_timeout(self):
        with patch("http.client.HTTPConnection.connect", side_effect=TimeoutError):
            self.assertEqual(probe_local(8080), "connect_timeout")


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.marker = "worker"
        self.witness = HTTPWitness(0, logging.getLogger(__name__))
        app = web.Application()
        app.cleanup_ctx.append(self.witness.lifecycle)
        app.router.add_get("/health", self.health)
        self.server = TestServer(app)
        await self.server.start_server()
        self.witness.port = self.server.port
        self.addAsyncCleanup(self.server.close)

    async def health(self, request):
        self.assertEqual(request.method, "HEAD")
        return web.Response(headers={"X-Beintaskbot-Health": self.marker})

    async def test_healthy_and_wrong_service(self):
        sample = await asyncio.to_thread(self.witness.sample)
        self.assertEqual(sample["local"], "ok")
        self.assertTrue(sample["http_thread_alive"])
        self.marker = "other"
        self.assertEqual(await asyncio.to_thread(probe_local, self.server.port), "unexpected_http_200")
        await self.server.close()
        self.assertTrue(self.witness.stop.is_set())
        self.assertTrue(self.witness.timer.cancelled())

    async def test_stalled_http_loop_has_listener_and_stack_but_no_reply(self):
        # Only this synthetic server pauses. The observer runs in another thread.
        result = {}
        sampled = threading.Event()
        entered = threading.Event()

        def observe():
            if entered.wait(3):
                result["http"] = probe_local(self.server.port, timeout=0.2)
                result["stack"] = stack_locations(self.witness.owner)
                result["port"] = listener_snapshot(self.server.port)
                result["tick"] = self.witness.last_tick
            sampled.set()

        def simulate_blocking_handler():
            private_value = "synthetic-private-request"
            entered.set()
            self.assertTrue(sampled.wait(4), private_value)

        thread = threading.Thread(target=observe)
        thread.start()
        tick_before = self.witness.last_tick
        simulate_blocking_handler()
        thread.join(1)
        self.assertEqual(result["http"], "headers_timeout")
        self.assertEqual(result["tick"], tick_before)
        self.assertTrue(any("simulate_blocking_handler" in frame for frame in result["stack"]))
        self.assertNotIn("synthetic-private-request", str(result["stack"]))
        if os.path.isfile("/proc/net/tcp"):
            self.assertTrue(result["port"]["listening"])
        await asyncio.sleep(1.1)
        self.assertGreater(self.witness.last_tick, tick_before)
        self.assertEqual(await asyncio.to_thread(probe_local, self.server.port), "ok")
