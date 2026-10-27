import asyncio
import unittest
from unittest.mock import patch

import httpx
from server import Sessions, Offer, create_app

SDP = "v=0\r\n" + "a=test\r\n" * 8


class FakeConnection:
    count = 0
    def __init__(self, **kwargs):
        type(self).count += 1
        self.pc_id = f"test-{self.count}"
        self.handlers = {}
        self.closed = False
        self.negotiations = 0
    def add_event_handler(self, name, handler):
        self.handlers[name] = handler
    async def initialize(self, **kwargs):
        self.sdp = kwargs["sdp"]
    async def renegotiate(self, **kwargs):
        self.negotiations += 1
    def get_answer(self):
        return {"sdp": self.sdp, "type": "answer", "pc_id": self.pc_id}
    async def disconnect(self):
        self.closed = True
    def is_connected(self):
        return False


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.started = []
        self.finished = []
        async def pipeline(connection, *, stop_event):
            self.started.append(connection)
            try:
                await stop_event.wait()
            finally:
                self.finished.append(connection)
        self.manager = Sessions(pipeline=pipeline, connection_factory=FakeConnection)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(
            app=create_app(self.manager, validate_settings=False)), base_url="http://localhost:7860")
    async def asyncTearDown(self):
        await self.manager.shutdown()
        await self.client.aclose()

    async def create(self):
        response = await self.client.post("/api/sessions", json={})
        self.assertEqual(response.status_code, 200)
        return response.json()["id"]

    async def test_offer_retry_and_renegotiation_keep_one_pipeline(self):
        sid = await self.create()
        url = f"/api/sessions/{sid}/offer"
        payload = {"sdp": SDP, "type": "offer"}
        first, retry = await asyncio.gather(self.client.post(url, json=payload),
                                           self.client.post(url, json=payload))
        self.assertEqual(first.json(), retry.json())
        await asyncio.sleep(0)
        self.assertEqual(len(self.started), 1)
        response = await self.client.post(url, json={**payload, "pc_id": first.json()["pc_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.started[0].negotiations, 1)

    async def test_two_sessions_are_isolated_and_stop_releases_resources(self):
        a, b = await self.create(), await self.create()
        for sid in (a, b):
            response = await self.client.post(f"/api/sessions/{sid}/offer", json={"sdp": SDP, "type": "offer"})
            self.assertEqual(response.status_code, 200)
        await asyncio.sleep(0)
        self.assertEqual(len(self.started), 2)
        self.assertIsNot(self.started[0], self.started[1])
        response = await self.client.delete(f"/api/sessions/{a}")
        self.assertEqual(response.status_code, 204)
        self.assertTrue(self.started[0].closed)
        self.assertIn(self.started[0], self.finished)
        self.assertIn(b, self.manager.items)

    async def test_signaling_rejects_foreign_origin_bad_sdp_and_unowned_session(self):
        foreign = await self.client.post("/api/sessions", json={}, headers={"Origin": "https://foreign.invalid"})
        self.assertEqual(foreign.status_code, 403)
        sid = await self.create()
        bad = await self.client.post(f"/api/sessions/{sid}/offer", json={"sdp": "x", "type": "answer"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json(), {"detail": "Invalid signaling request"})
        self.client.cookies.clear()
        missing = await self.client.post(f"/api/sessions/{sid}/offer", json={"sdp": SDP, "type": "offer"})
        self.assertEqual(missing.status_code, 404)

    async def test_limits_and_mismatched_peer_id(self):
        self.manager.limit = 1
        sid = await self.create()
        self.assertEqual((await self.client.post("/api/sessions", json={})).status_code, 429)
        url = f"/api/sessions/{sid}/offer"
        await self.client.post(url, json={"sdp": SDP, "type": "offer"})
        wrong = await self.client.post(url, json={"sdp": SDP, "type": "offer", "pc_id": "foreign"})
        self.assertEqual(wrong.status_code, 404)


if __name__ == "__main__":
    unittest.main()
