"""Real HTTP and AMI socket boundaries with a simulated PBX; no telephone calls."""
import asyncio
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_agent.api.app import create_app
from voice_agent.api.dialer import Call, Dialer, normalize_number
from voice_agent.api.telephony import create_telephony_app
from voice_agent.transports.ami import AMIClient
from voice_agent.voice.transcript import TranscriptObserver
from tests.integration.test_telephony import start_event

ENV = dict(OUTBOUND_ENABLED="true", DIALER_PASSWORD="offline_operator_password_123",
           ASTERISK_AMI_SECRET="offline_ami_secret_123", TELEPHONY_ENABLED="true",
           OUTBOUND_ALLOWED_NUMBERS="", AGENT_ID="", TELEPHONY_AGENT_ID="",
           TELEPHONY_CALL_CONFIG="", SURVEY_CONVERSATION_MANAGER="false")
AUTH = ("operator", ENV["DIALER_PASSWORD"])


class FakePBX:
    def __init__(self):
        self.actions = []
        self.writer = None
        self.channel = "Local/12345678@web-dialer-00000001;1"
        self.auto_channel = True

    def send(self, **fields):
        self.writer.write(("".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n").encode())

    async def serve(self, reader, writer):
        self.writer = writer
        writer.write(b"Asterisk Call Manager/9.0\r\n")
        try:
            while True:
                raw = await reader.readuntil(b"\r\n\r\n")
                fields = {}
                for line in raw.decode().strip().split("\r\n"):
                    key, value = line.split(": ", 1)
                    if key == "Variable":
                        fields.setdefault("Variable", []).append(value)
                    else:
                        fields[key] = value
                self.actions.append(fields)
                if fields["Action"] == "Originate" and self.auto_channel:
                    self.identity = fields["ChannelId"]
                    self.send(Event="Newchannel", Uniqueid=self.identity, Channel=self.channel)
                self.send(Response="Success", ActionID=fields["ActionID"])
                if fields["Action"] == "Hangup":
                    self.send(Event="Hangup", Uniqueid=self.identity, Channel=self.channel, Cause="16")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


class DialerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.pbx = FakePBX()
        self.server = await asyncio.start_server(self.pbx.serve, "127.0.0.1", 0)
        self.control = AMIClient("web-dialer", ENV["ASTERISK_AMI_SECRET"],
                                 port=self.server.sockets[0].getsockname()[1])
        self.dialer = Dialer(client=self.control)
        await self.dialer.start()
        await eventually(lambda: self.control.connected)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dialer, validate_settings=False)),
                                     base_url="http://localhost:7860", auth=AUTH)

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.dialer.close()
        self.server.close()
        await self.server.wait_closed()

    async def dial(self, **changes):
        body = dict(number="12345678", request_id=str(uuid4()))
        body.update(changes)
        response = await self.http.post("/api/calls", json=body)
        self.assertEqual(response.status_code, 202, response.text)
        await eventually(lambda: any(item["Action"] == "Originate" for item in self.pbx.actions))
        return response.json(), body

    async def test_originate_ring_answer_transcript_hangup_and_isolation(self):
        view, _ = await self.dial(number="+968 1234 5678")
        identity = view["id"]
        origin = next(item for item in self.pbx.actions if item["Action"] == "Originate")
        self.assertEqual(origin["Channel"], "Local/12345678@web-dialer/n")
        self.assertEqual(origin["CallerID"], "21130800")
        self.assertEqual(origin["Context"], "from-web-agent")
        self.assertIn(f"__WEB_CALL_ID={identity}", origin["Variable"])
        self.pbx.send(Event="Newstate", Uniqueid=identity, ChannelStateDesc="Ringing")
        await eventually(lambda: self.dialer.get(identity).status == "ringing")
        self.pbx.send(Event="Newstate", Uniqueid=identity, ChannelStateDesc="Up")
        await eventually(lambda: self.dialer.get(identity).status == "connecting")
        stop = asyncio.Event()
        self.dialer.attach(identity, "websocket-owned-channel", stop)
        self.dialer.transcript(identity, "agent", "مرحبا، كيف حالك؟")
        self.dialer.transcript(identity, "customer", "بخير")
        self.dialer.transcript("other-call", "customer", "Must not leak")
        snapshot = (await self.http.get(f"/api/calls/{identity}")).json()
        self.assertEqual([m["role"] for m in snapshot["messages"]], ["agent", "customer"])
        self.assertEqual(snapshot["status"], "connected")
        self.assertNotIn("Must not leak", json.dumps(snapshot))
        self.assertNotIn("channel", snapshot)
        await self.http.delete(f"/api/calls/{identity}")
        await eventually(lambda: self.dialer.get(identity).status == "cancelled")
        self.assertTrue(stop.is_set())
        self.assertEqual(self.pbx.actions[-1]["Channel"], self.pbx.channel)

    async def test_login_csrf_injection_and_own_number_are_rejected(self):
        self.assertEqual((await self.http.get("/", auth=None)).status_code, 401)
        self.assertEqual((await self.http.get("/api/settings", auth=("operator", "wrong"))).status_code, 401)
        request = dict(number="12345678", request_id=str(uuid4()))
        self.assertEqual((await self.http.post("/api/calls", json=request,
                              headers={"Origin": "https://foreign.invalid"})).status_code, 403)
        self.assertEqual((await self.http.delete("/api/calls/anything",
                              headers={"Sec-Fetch-Site": "cross-site"})).status_code, 403)
        for number in ("12345678\r\nAction: Originate", "${SHELL(id)}", "+97112345678", "21130800"):
            response = await self.http.post("/api/calls", json={**request, "number": number})
            self.assertEqual(response.status_code, 422)
        self.assertFalse(any(item["Action"] == "Originate" for item in self.pbx.actions))

    async def test_operator_password_accepts_eight_characters_and_rejects_seven(self):
        with patch.dict(os.environ, {"DIALER_PASSWORD": "eight123"}):
            app = create_app(self.dialer, validate_settings=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost:7860") as client:
            self.assertEqual((await client.get("/api/settings", auth=("operator", "eight123"))).status_code, 200)
            self.assertEqual((await client.get("/api/settings", auth=("operator", "wrong123"))).status_code, 401)
        with patch.dict(os.environ, {"DIALER_PASSWORD": "seven12"}):
            with self.assertRaisesRegex(ValueError, "at least 8 characters"):
                create_app(self.dialer, validate_settings=False)
        with patch.dict(os.environ, {"DIALER_PASSWORD": "eight123", "ASTERISK_AMI_SECRET": "short123"}):
            with self.assertRaisesRegex(ValueError, "ASTERISK_AMI_SECRET"):
                create_app(self.dialer, validate_settings=False)

    async def test_request_retry_duplicate_destination_capacity_and_disabled(self):
        first, body = await self.dial()
        self.assertEqual((await self.http.post("/api/calls", json=body)).json()["id"], first["id"])
        self.assertEqual((await self.http.post("/api/calls", json={**body, "request_id":str(uuid4())})).status_code, 409)
        self.assertEqual(sum(item["Action"] == "Originate" for item in self.pbx.actions), 1)
        self.dialer.media["incoming"] = None
        self.assertEqual((await self.http.post("/api/calls", json=dict(number="12345679", request_id=str(uuid4())))).status_code, 429)
        self.dialer.enabled = False
        self.assertEqual((await self.http.post("/api/calls", json=dict(number="12345679", request_id=str(uuid4())))).status_code, 503)

    async def test_fast_cancel_before_channel_is_allocated(self):
        self.pbx.auto_channel = False
        view, _ = await self.dial()
        identity = view["id"]
        await self.http.delete(f"/api/calls/{identity}")
        self.assertEqual(self.dialer.get(identity).status, "cancelling")
        self.pbx.identity = identity
        self.pbx.send(Event="Newchannel", Uniqueid=identity, Channel=self.pbx.channel)
        await eventually(lambda: self.dialer.get(identity).status == "cancelled")
        self.assertTrue(any(item["Action"] == "Hangup" for item in self.pbx.actions))

    async def test_async_originate_failure_is_dispatched_as_event(self):
        view, _ = await self.dial()
        self.pbx.send(Event="OriginateResponse", ActionID=view["id"], Uniqueid="<unknown>", Response="Failure", Reason="5")
        await eventually(lambda: self.dialer.get(view["id"]).status == "busy")

    async def test_dial_status_from_second_local_leg_preserves_no_answer(self):
        view, _ = await self.dial()
        self.pbx.send(Event="DialState", Uniqueid=view["id"] + "-peer", DialStatus="RINGING")
        await eventually(lambda: self.dialer.get(view["id"]).status == "ringing")
        self.pbx.send(Event="DialEnd", Uniqueid=view["id"] + "-peer", DialStatus="NOANSWER")
        self.pbx.send(Event="Hangup", Uniqueid=view["id"], Cause="16")
        await eventually(lambda: self.dialer.get(view["id"]).status == "no_answer")
        self.assertEqual(self.dialer.get(view["id"]).channel, self.pbx.channel)

    async def test_allowed_numbers_and_transcript_retention(self):
        self.dialer.allowed = {"12345679"}
        response = await self.http.post("/api/calls", json=dict(number="12345678", request_id=str(uuid4())))
        self.assertEqual(response.status_code, 403)
        self.dialer.allowed.clear()
        view, _ = await self.dial()
        for _ in range(1002):
            self.dialer.transcript(view["id"], "customer", "a"*6100)
        call = self.dialer.get(view["id"])
        self.assertLessEqual(len(call.messages), 1000)
        self.assertEqual(sum(len(message["text"]) for message in call.messages), 160000)
        self.assertEqual(len(call.messages[0]["text"]), 6000)
        self.dialer.finish(call, "completed", "Call ended")
        call.touched -= 3601
        self.dialer.prune()
        self.assertNotIn(call.id, self.dialer.calls)

    async def test_disconnect_never_redials_and_stops_media(self):
        view, _ = await self.dial()
        stop = asyncio.Event()
        call = self.dialer.attach(view["id"], "media", stop)
        self.dialer.disconnected()
        self.assertTrue(stop.is_set())
        self.assertTrue(call.cancel_requested)
        self.assertEqual(call.status, "cancelling")
        self.assertEqual(sum(item["Action"] == "Originate" for item in self.pbx.actions), 1)

    async def test_removed_browser_routes_and_dependency_free_assets(self):
        self.assertEqual((await self.http.post("/api/sessions", json={})).status_code, 405)
        html = await self.http.get("/")
        self.assertEqual(html.status_code, 200)
        self.assertIn("Live transcript", html.text)
        javascript = (await self.http.get("/main.js")).text
        for removed in ("getUserMedia", "RTCPeerConnection", "@pipecat-ai", "microphone"):
            self.assertNotIn(removed, javascript)
        self.assertIn("textContent=message.text", javascript)


class TranscriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_customer_finals_and_synthesized_agent_speech_only(self):
        from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame, TTSTextFrame, LLMTextFrame
        from pipecat.processors.frame_processor import FrameDirection
        seen=[]
        for role, frames in (
            ("customer", [InterimTranscriptionFrame("partial", "", "1"), TranscriptionFrame("Hello", "", "2")]),
            ("agent", [LLMTextFrame("not synthesized"), TTSTextFrame("Welcome")]),
        ):
            observer=TranscriptObserver(lambda role,text: seen.append((role,text)), role)
            observer.push_frame=AsyncMock()
            for frame in frames:
                await observer.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(seen, [("customer","Hello"),("agent","Welcome")])


class MediaCorrelationTests(unittest.TestCase):
    def test_media_owns_exact_web_call_and_customer_greeting_is_in_transcript(self):
        class Control:
            connected=True
            async def action(self, *args, **kwargs):
                pass
        with patch.dict(os.environ, ENV):
            dialer=Dialer(client=Control())
            identity=uuid4().hex
            dialer.calls[identity]=Call(identity,"12345678",str(uuid4()))
            async def pipeline(_connection, *, stop_event, transport, publish_transcript):
                publish_transcript("agent", "Hello")
                publish_transcript("customer", "Hi")
                await transport.peer.command("GET_STATUS")
            app=create_telephony_app(pipeline=pipeline,dialer=dialer)
            with TestClient(app,client=("127.0.0.1",12345)) as client:
                with client.websocket_connect(f"/telephony/media?call_id={identity}",subprotocols=["media"]) as ws:
                    ws.send_json(start_event("trusted-media"))
                    self.assertEqual(ws.receive_json(),{"command":"GET_STATUS"})
                    with self.assertRaises(WebSocketDisconnect):
                        ws.receive_bytes()
                self.assertEqual([m["text"] for m in dialer.get(identity).messages],["Hello","Hi"])
                self.assertFalse(dialer.media)
                with client.websocket_connect("/telephony/media?call_id=unknown",subprotocols=["media"]) as ws:
                    ws.send_json(start_event("unowned"))
                    with self.assertRaises(WebSocketDisconnect):
                        ws.receive_bytes()


if __name__ == "__main__":
    unittest.main()
