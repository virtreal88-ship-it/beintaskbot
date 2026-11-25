"""Local witnesses for the public aiohttp listener, independent of its event loop."""
import asyncio
import http.client
import json
import os
import re
import sys
import threading
import time
import uuid


def probe_local(port, timeout=3):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    phase = "connect"
    try:
        connection.connect()
        phase = "headers"
        connection.request("HEAD", "/health", headers={"Connection": "close"})
        response = connection.getresponse()
        if response.status == 200 and response.getheader("X-Beintaskbot-Health") == "worker":
            return "ok"
        return "unexpected_http_" + str(response.status)
    except ConnectionRefusedError:
        return "refused"
    except TimeoutError:
        return phase + "_timeout"
    except (OSError, http.client.HTTPException):
        return "transport_error"
    finally:
        connection.close()


def listener_snapshot(port, table="/proc/net/tcp"):
    # TCPSite binds IPv4. Inspect only LISTEN rows. No peer addresses are retained.
    try:
        with open(table, encoding="ascii") as rows:
            for line in rows:
                cols = line.split()
                if len(cols) > 4 and cols[3] == "0A" and int(cols[1].split(":")[1], 16) == port:
                    return {"listening": True, "accept_queue": int(cols[4].split(":")[1], 16)}
        return {"listening": False}
    except (OSError, ValueError, IndexError):
        return {"listening": None}


def stack_locations(thread):
    frame = sys._current_frames().get(thread.ident) if thread.is_alive() else None
    result = []
    while frame and len(result) < 24:
        code = frame.f_code
        # Only code locations. Synthetic filenames may come from compiled input.
        filename = os.path.basename(code.co_filename)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}\.py", filename):
            filename = "dynamic"
        name = code.co_name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", code.co_name) else "anonymous"
        result.append(f"{filename}:{frame.f_lineno}:{name}")
        frame = frame.f_back
    return result


class HTTPWitness:
    def __init__(self, port, logger):
        self.port = port
        self.logger = logger
        self.stop = threading.Event()
        self.last_tick = 0.0
        self.timer = None
        self.boot = uuid.uuid4().hex[:12]

    def tick(self):
        self.last_tick = time.monotonic()
        self.timer = self.loop.call_later(1, self.tick)

    def sample(self):
        local = probe_local(self.port)
        age = round(time.monotonic() - self.last_tick, 1)
        return {"boot": self.boot, "local": local, "tick_age_s": age,
                "http_thread_alive": self.owner.is_alive(), **listener_snapshot(self.port),
                "stack_leaf_first": stack_locations(self.owner)}

    def observe(self):
        next_baseline = 0.0
        was_healthy = True
        while not self.stop.wait(15):
            try:
                data = self.sample()
                if self.stop.is_set():
                    return
                healthy = data["local"] == "ok" and data["tick_age_s"] < 5
                now = time.monotonic()
                if healthy and was_healthy and now < next_baseline:
                    continue
                was_healthy = healthy
                next_baseline = now + 60
                log = self.logger.info if healthy else self.logger.warning
                log("worker_http_witness %s", json.dumps(data, separators=(",", ":")))
            except Exception:
                self.logger.warning("worker_http_witness sample_unavailable")

    async def lifecycle(self, _app):
        self.loop = asyncio.get_running_loop()
        self.owner = threading.current_thread()
        self.tick()
        monitor = threading.Thread(target=self.observe, name="http-witness", daemon=True)
        monitor.start()
        try:
            yield
        finally:
            self.stop.set()
            self.timer.cancel()
