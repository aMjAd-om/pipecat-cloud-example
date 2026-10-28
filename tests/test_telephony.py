"""Offline Asterisk protocol and real Pipecat pipeline checks; no Azure calls."""
import asyncio
import audioop
import json
import os
import signal
import socket
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from asterisk_transport import AsteriskPeer, PCMResampler, TelephoneAudioStats, media_start
from telephony_server import create_telephony_app, telephone_listener


def start_event(channel=None):
    # Mirrors Asterisk 22.11 outbound clients: config name shared by calls.
    return dict(event="MEDIA_START", connection_id="pipecat",
                channel_id=channel or str(uuid4()), format="slin16",
                optimal_frame_size=640, ptime=20)


class Wire:
    def __init__(self):
        self.sent = []
    async def send_text(self, text):
        self.sent.append(json.loads(text))
    async def send_bytes(self, data):
        self.sent.append(data)
    async def close(self):
        pass


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_audio_diagnostics_keep_only_counters_and_window_levels(self):
        stats = TelephoneAudioStats()
        stats.received(b"\x00\x10" * 320)
        stats.received(bytes(640))
        stats.forwarded_frames = 2
        snapshot = stats.snapshot()
        self.assertEqual(snapshot["rx_frames"], 2)
        self.assertEqual(snapshot["forwarded_frames"], 2)
        self.assertEqual(snapshot["peak"], 4096)
        self.assertEqual(snapshot["rms_max"], 4096)
        self.assertEqual(stats.snapshot()["peak"], 0)
        self.assertFalse(any(isinstance(value, (bytes, bytearray))
                             for value in vars(stats).values()))

    def test_handshake_requires_negotiated_pcm_and_ids(self):
        event = start_event()
        self.assertEqual(media_start(json.dumps(event)), event)
        for changes in ({"format": "ulaw"}, {"optimal_frame_size": 960},
                        {"ptime": 40}, {"channel_id": ""},
                        {"connection_id": "bad\nname"}, {"event": "DTMF_END"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                media_start(json.dumps({**event, **changes}))

    def test_incoming_uuid_and_outbound_config_name_are_both_valid(self):
        for identity in ("pipecat", str(uuid4())):
            event = {**start_event(), "connection_id": identity}
            self.assertEqual(media_start(json.dumps(event))["connection_id"], identity)

    async def test_flush_invalidates_writer_waiting_for_flow_control(self):
        ws, stop = Wire(), asyncio.Event()
        peer = AsteriskPeer(ws, start_event(), stop)
        peer.event('{"event":"MEDIA_XOFF"}')
        pending = asyncio.create_task(peer.audio(b"x" * 640, peer.epoch))
        await asyncio.sleep(0)
        await peer.flush()
        await pending
        self.assertEqual(ws.sent, [{"command": "FLUSH_MEDIA"}])
        await peer.audio(b"y" * 640, peer.epoch)
        self.assertEqual(ws.sent[-1], b"y" * 640)
        await peer.close()
        await peer.audio(b"z" * 640, peer.epoch)
        self.assertEqual(len(ws.sent), 2)
        self.assertTrue(stop.is_set())

    async def test_pacing_and_queue_drain_ack(self):
        ws = Wire()
        peer = AsteriskPeer(ws, start_event(), asyncio.Event())
        now = asyncio.get_running_loop().time()
        for _ in range(4):
            await peer.audio(bytes(640), peer.epoch)
        self.assertGreaterEqual(asyncio.get_running_loop().time() - now, .05)
        drain = asyncio.create_task(peer.drain())
        await asyncio.sleep(.01)
        self.assertFalse(drain.done())
        peer.event('{"event":"QUEUE_DRAINED"}')
        await drain

    async def test_resampling_keeps_filter_state_across_azure_chunks(self):
        import numpy as np
        data = (np.sin(np.arange(24000) * 2 * np.pi * 440 / 24000) * 10000).astype("int16").tobytes()
        resampler = PCMResampler()
        result = b"".join([await resampler.resample(data[n:n + 962], 24000, 16000)
                           for n in range(0, len(data), 962)])
        expected, _ = audioop.ratecv(data, 2, 1, 24000, 16000, None)
        self.assertEqual(result, expected)
        self.assertAlmostEqual(len(result) / 32000, 1, places=3)

    async def test_listener_is_opt_in_and_releases_port_and_signals(self):
        with patch.dict(os.environ, {"TELEPHONY_ENABLED": "false"}):
            async with telephone_listener():
                pass
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        previous = signal.getsignal(signal.SIGINT)
        async def pipeline(_connection, *, stop_event, transport):
            await transport.peer.command("GET_STATUS")
            data = await transport.peer.ws.receive_bytes()
            await transport.peer.audio(data, transport.peer.epoch)
            await stop_event.wait()
        app = create_telephony_app(pipeline=pipeline)
        with patch.dict(os.environ, {"TELEPHONY_ENABLED": "true", "TELEPHONY_PORT": str(port)}), \
             patch("telephony_server.create_telephony_app", return_value=app):
            async with telephone_listener():
                import httpx
                async with httpx.AsyncClient() as client:
                    result = await client.get(f"http://127.0.0.1:{port}/health")
                    self.assertEqual(result.json(), {"status": "ok", "calls": 0})
                import websockets
                ws = await websockets.connect(f"ws://127.0.0.1:{port}/telephony/media", subprotocols=["media"])
                await ws.send(json.dumps(start_event()))
                self.assertEqual(json.loads(await ws.recv()), {"command": "GET_STATUS"})
                await ws.send(bytes(640))
                self.assertEqual(await ws.recv(), bytes(640))
                self.assertEqual(len(app.state.calls), 1)
                self.assertEqual(signal.getsignal(signal.SIGINT), previous)
            await asyncio.wait_for(ws.wait_closed(), 2)
            self.assertEqual(app.state.calls, {})
        self.assertEqual(signal.getsignal(signal.SIGINT), previous)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    async def test_listener_reports_a_port_conflict(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            with patch.dict(os.environ, {"TELEPHONY_ENABLED": "true",
                    "TELEPHONY_PORT": str(occupied.getsockname()[1])}):
                with self.assertRaises(OSError):
                    async with telephone_listener():
                        self.fail("A conflicting listener should fail startup")


class EndpointTests(unittest.TestCase):
    def client(self, pipeline, **kwargs):
        return TestClient(create_telephony_app(pipeline=pipeline),
                          client=("127.0.0.1", 12345), **kwargs)

    def test_duplicate_call_limits_and_release_on_pipeline_exit(self):
        async def pipeline(_connection, *, stop_event, transport):
            while True:
                message = await transport.peer.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
        with patch.dict(os.environ, {"TELEPHONY_MAX_CALLS": "1"}):
            with self.client(pipeline) as client:
                with client.websocket_connect("/telephony/media", subprotocols=["media"]) as first:
                    event = start_event()
                    first.send_json(event)
                    self.assertEqual(client.get("/health").json()["calls"], 1)
                    for candidate in (event, start_event()):
                        with client.websocket_connect("/telephony/media", subprotocols=["media"]) as second:
                            second.send_json(candidate)
                            with self.assertRaises(WebSocketDisconnect):
                                second.receive_bytes()
                # Use the portal to yield to route cleanup, rather than sleeping.
                client.portal.call(asyncio.sleep, .02)
                self.assertEqual(client.get("/health").json()["calls"], 0)

    def test_reject_origin_missing_subprotocol_and_invalid_handshake(self):
        pipeline = AsyncMock()
        with self.client(pipeline) as client:
            for options in ({}, {"subprotocols": ["media"], "headers": {"origin": "https://example.com"}}):
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect("/telephony/media", **options):
                        pass
            with client.websocket_connect("/telephony/media", subprotocols=["media"]) as ws:
                ws.send_json({"event": "MEDIA_START", "format": "ulaw"})
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_bytes()
        pipeline.assert_not_awaited()

    def test_parallel_calls_have_distinct_peers_and_shutdown_stops_them(self):
        peers = []
        async def pipeline(_connection, *, stop_event, transport):
            peers.append(transport.peer)
            await transport.peer.command("GET_STATUS")
            await stop_event.wait()
        with patch.dict(os.environ, {"TELEPHONY_MAX_CALLS": "2"}):
            with self.client(pipeline) as client:
                with client.websocket_connect("/telephony/media", subprotocols=["media"]) as a, \
                     client.websocket_connect("/telephony/media", subprotocols=["media"]) as b:
                    a.send_json(start_event())
                    b.send_json(start_event())
                    a.receive_json()
                    b.receive_json()
                    self.assertEqual(client.get("/health").json()["calls"], 2)
                    self.assertIsNot(peers[0], peers[1])
                    self.assertIsNot(peers[0].stop_event, peers[1].stop_event)
                    client.portal.call(peers[0].stop_event.set)
                    with self.assertRaises(WebSocketDisconnect):
                        a.receive_bytes()
                    self.assertFalse(peers[1].stop_event.is_set())
                    client.portal.call(peers[1].stop_event.set)
                    with self.assertRaises(WebSocketDisconnect):
                        b.receive_bytes()
                self.assertEqual(client.get("/health").json()["calls"], 0)

    def test_call_duration_limit_requests_normal_pipeline_stop(self):
        stopped = []
        async def pipeline(_connection, *, stop_event, transport):
            await stop_event.wait()
            stopped.append(True)
        with patch.dict(os.environ, {"TELEPHONY_MAX_SECONDS": "30"}):
            with self.client(pipeline) as client:
                # Accelerate only the call-duration timer, retaining real async
                # I/O and unrelated timeout handling.
                loop = client.portal.call(asyncio.get_running_loop)
                original = loop.call_later
                def accelerated(delay, *args, **kwargs):
                    return original(.02 if delay == 30 else delay, *args, **kwargs)
                with patch.object(loop, "call_later", accelerated):
                    with client.websocket_connect("/telephony/media", subprotocols=["media"]) as ws:
                        ws.send_json(start_event())
                        with self.assertRaises(WebSocketDisconnect):
                            ws.receive_bytes()
                self.assertEqual(stopped, [True])
                self.assertEqual(client.get("/health").json()["calls"], 0)

    def test_real_pipeline_greeting_multiple_turns_resampling_and_cleanup(self):
        from pipecat.frames.frames import (
            InputAudioRawFrame, LLMTextFrame, TTSAudioRawFrame,
            TTSStoppedFrame, TranscriptionFrame, StartInterruptionFrame,
        )
        from pipecat.processors.frame_processor import FrameProcessor

        texts, services, decisions = [], [], []

        class STT(FrameProcessor):
            def __init__(self, **kwargs):
                super().__init__()
                self.close = AsyncMock()
                services.append(self)
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, InputAudioRawFrame):
                    if frame.audio[:2] == b"\x01\x00":
                        await self.push_frame(TranscriptionFrame("نعم", "phone", "1"))
                    elif frame.audio[:2] == b"\x02\x00":
                        await self.push_frame(TranscriptionFrame("خمسة", "phone", "2"))
                    elif frame.audio[:2] == b"\x03\x00":
                        # A full second of queued old speech, to interrupt.
                        await self.push_frame(TTSAudioRawFrame(b"\x00\x30" * 24000, 24000, 1))
                        await self.push_frame(TTSStoppedFrame())
                    elif frame.audio[:2] == b"\x04\x00":
                        await self.push_frame(StartInterruptionFrame())
                    elif frame.audio[:2] == b"\x05\x00":
                        await self.push_frame(TTSAudioRawFrame(b"\x00\x50" * 1200, 24000, 1))
                        await self.push_frame(TTSStoppedFrame())
                else:
                    await self.push_frame(frame, direction)

        class TTS(FrameProcessor):
            def __init__(self, **kwargs):
                super().__init__()
                self.close = AsyncMock()
                services.append(self)
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, LLMTextFrame):
                    texts.append(frame.text)
                    await self.push_frame(TTSAudioRawFrame(b"\x00\x10" * 1200, 24000, 1))
                    await self.push_frame(TTSStoppedFrame())
                else:
                    await self.push_frame(frame, direction)

        class LLM:
            InputParams = object
            def __init__(self, **kwargs):
                self._client = NS(close=AsyncMock())
                services.append(self._client)
            def create_context_aggregator(self, context):
                return NS()

        class DecisionClient:
            def __init__(self, *args):
                pass
            async def __call__(self, messages):
                number = len(decisions)
                decisions.append(messages)
                return json.dumps(dict(intent="answer", answer_status="valid",
                    answer_value="نعم" if number == 0 else 5,
                    action="next_question", spoken_response="شكراً لك.",
                    current_question_id="consent" if number == 0 else "overall_rating")), {}

        import bot
        async def wait_closed(client):
            for _ in range(300):
                if not client.app.state.calls:
                    return
                await asyncio.sleep(.01)
            raise AssertionError("Call cleanup did not complete")
        with patch.dict(os.environ, {"SURVEY_CONVERSATION_MANAGER": "true", "DEFAULT_LANGUAGE": "ar",
                "SURVEY_QUESTIONNAIRE": "questionnaire.customer-satisfaction.ar.json",
                "AZURE_OPENAI_API_KEY": "test", "AZURE_OPENAI_BASE_URL": "https://example.com/openai/v1/",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini"}), \
             patch.object(bot, "StreamingAzureSTT", STT), \
             patch.object(bot, "StreamingAzureTTS", TTS), \
             patch.object(bot, "FoundryLLMService", LLM), \
             patch("survey_voice.FoundryDecisionClient", DecisionClient):
            with self.client(bot.main) as client:
                with client.websocket_connect("/telephony/media", subprotocols=["media"]) as ws:
                    initial = start_event()
                    ws.send_json(initial)
                    # Each fake synthesis emits 50ms, resampled + tail-padded to
                    # three correctly sized PBX packets. No RTVI ready message.
                    for packet in range(3):
                        self.assertEqual(len(ws.receive_bytes()), 640)
                    self.assertEqual(len(texts), 1)
                    self.assertIn("هل تسمح", texts[0])
                    for marker in (1, 2):
                        ws.send_bytes(bytes([marker, 0]) * 320)
                        for packet in range(3):
                            self.assertEqual(len(ws.receive_bytes()), 640)
                    self.assertEqual(len(decisions), 2)
                    self.assertIn("بشكل عام", texts[1])
                    self.assertIn("السبب", texts[2])
                    ws.send_bytes(b"\x03\x00" * 320)
                    self.assertEqual(ws.receive_bytes()[:2], b"\x00\x30")
                    ws.send_bytes(b"\x04\x00" * 320)
                    for _ in range(10):
                        message = ws.receive()
                        if message.get("text"):
                            self.assertEqual(json.loads(message["text"]), {"command": "FLUSH_MEDIA"})
                            break
                    else:
                        self.fail("Interruption did not flush queued playback promptly")
                    ws.send_bytes(b"\x05\x00" * 320)
                    for _ in range(3):
                        self.assertEqual(ws.receive_bytes()[:2], b"\x00\x50")
                    # Send a real disconnect while allowing the ASGI route to
                    # finish, before TestClient cancels its connection scope.
                    ws.close()
                    client.portal.call(wait_closed, client)
                self.assertEqual(client.get("/health").json()["calls"], 0)
                # A fresh call can reuse a released channel, greets once again,
                # and malformed PCM ends it without reaching the model.
                with client.websocket_connect("/telephony/media", subprotocols=["media"]) as ws:
                    ws.send_json(start_event(channel=initial["channel_id"]))
                    for _ in range(3):
                        self.assertEqual(len(ws.receive_bytes()), 640)
                    self.assertIn("هل تسمح", texts[-1])
                    ws.send_bytes(b"x")
                    with self.assertRaises(WebSocketDisconnect):
                        ws.receive_bytes()
                    client.portal.call(wait_closed, client)
                self.assertEqual(len(decisions), 2)
        for service in services:
            service.close.assert_awaited()


if __name__ == "__main__":
    unittest.main()
