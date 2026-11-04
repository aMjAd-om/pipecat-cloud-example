"""Offline protocol and lifecycle checks; no provider requests leave this process."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStoppedFrame
from voice_agent.config import load_settings
from voice_agent.observability.diagnostics import safe_diagnostic
from voice_agent.providers.cartesia_tts import StreamingCartesiaTTS, cartesia_settings
from voice_agent.providers.munsit_tts import StreamingMunsitTTS, munsit_settings
from voice_agent.providers.tts import create_tts, tts_provider


class AudioStream(httpx.AsyncByteStream):
    def __init__(self, chunks, block=False):
        self.chunks, self.block = chunks, block
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.block:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


class CartesiaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CARTESIA_API_KEY": "test-secret", "DEFAULT_LANGUAGE": "ar",
            "TTS_PROVIDER": "azure", "CARTESIA_MODEL": "sonic-3.6",
            "CARTESIA_VOICE_ID": "47c38ca4-5f35-497b-b1a3-415245fb35e1",
            "CARTESIA_SPEED": "1.3", "CARTESIA_VOLUME": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def service(self, stream, status=200):
        self.requests = []
        def handler(request):
            self.requests.append(request)
            return httpx.Response(status, stream=stream)
        return StreamingCartesiaTTS(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def test_request_and_incremental_aligned_pcm(self):
        stream = AudioStream([b"a", b"bc", b"def"])
        tts = self.service(stream)
        try:
            frames = [f async for f in tts.run_tts("السلام عليكم")]
            audio = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
            self.assertEqual(b"".join(f.audio for f in audio), b"abcdef")
            self.assertGreater(len(audio), 1)
            self.assertTrue(all(f.sample_rate == 24000 and f.num_channels == 1 and len(f.audio) % 2 == 0 for f in audio))
            request = self.requests[0]
            self.assertEqual(str(request.url), "https://api.cartesia.ai/tts/bytes")
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            self.assertEqual(request.headers["Cartesia-Version"], "2026-08-14")
            body = json.loads(request.content)
            self.assertEqual(body["transcript"], "السلام عليكم")
            self.assertEqual(body["language"], "ar")
            self.assertEqual(body["model_id"], "sonic-3.6")
            self.assertEqual(body["voice"], "47c38ca4-5f35-497b-b1a3-415245fb35e1")
            self.assertEqual(body["generation_config"], {"speed": 1.3, "volume": 1})
            self.assertEqual(body["output_format"], {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 24000})
            self.assertTrue(stream.closed)
            self.assertIsInstance(frames[-1], TTSStoppedFrame)
        finally:
            await tts.close()

    async def test_cancel_closes_stream_and_next_turn_works(self):
        stream = AudioStream([bytes(480)], block=True)
        tts = self.service(stream)
        async def consume():
            return [f async for f in tts.run_tts("first")]
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(stream.waiting.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stream.closed)
            self.assertFalse(tts._active)
            # Same client remains usable after interruption.
            stream.block = False
            frames = [f async for f in tts.run_tts("second")]
            self.assertTrue(any(isinstance(f, TTSAudioRawFrame) for f in frames))
            self.assertEqual(len(self.requests), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await tts.close()
            await tts.close()

    async def test_bad_http_empty_and_truncated_audio(self):
        for status, chunks in ((401, [b"test-secret private text"]), (200, []), (200, [b"x"])):
            with self.subTest(status=status, chunks=chunks):
                stream = AudioStream(chunks)
                tts = self.service(stream, status)
                try:
                    frames = [f async for f in tts.run_tts("private text")]
                    errors = [f.error for f in frames if isinstance(f, ErrorFrame)]
                    self.assertEqual(len(errors), 1)
                    self.assertNotIn("test-secret", errors[0])
                    self.assertNotIn("private text", errors[0])
                    self.assertEqual(len(self.requests), 1)
                    self.assertTrue(stream.closed)
                finally:
                    await tts.close()

    async def test_selection_and_closed_service(self):
        with patch("voice_agent.providers.azure_tts.StreamingAzureTTS") as azure:
            self.assertIs(create_tts(timing=None), azure.return_value)
        with patch.dict(os.environ, {"TTS_PROVIDER": "cartesia"}):
            tts = create_tts()
            self.assertIsInstance(tts, StreamingCartesiaTTS)
            await tts.close()
            self.assertTrue(any(isinstance(f, ErrorFrame) for f in [f async for f in tts.run_tts("closed")]))

    async def test_read_failure_does_not_retry_and_wrong_content_type_is_rejected(self):
        calls = []
        def fail(request):
            calls.append(request)
            raise httpx.ReadTimeout("test-secret private text")
        client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
        tts = StreamingCartesiaTTS(client=client)
        try:
            frames = [f async for f in tts.run_tts("private text")]
            self.assertEqual(len(calls), 1)
            self.assertEqual([f.error for f in frames if isinstance(f, ErrorFrame)], ["Cartesia speech synthesis failed"])
        finally:
            await tts.close()
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{}")))
        tts = StreamingCartesiaTTS(client=client)
        try:
            frames = [f async for f in tts.run_tts("private text")]
            self.assertTrue(any(isinstance(f, ErrorFrame) for f in frames))
            self.assertFalse(any(isinstance(f, TTSAudioRawFrame) for f in frames))
        finally:
            await tts.close()


class MunsitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MUNSIT_API_KEY": "munsit-secret",
            "MUNSIT_VOICE_ID": "SyU41Fvh3zAI9ZRf81uuTYQ4", "MUNSIT_SPEED": "1.0",
            "MUNSIT_STABILITY": "0.5", "TTS_PROVIDER": "munsit"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def service(self, stream, status=200, content_type="audio/raw;codec=pcm16;rate=24000;channels=1", **options):
        self.requests = []
        def handler(request):
            self.requests.append(request)
            return httpx.Response(status, headers={"Content-Type": content_type}, stream=stream)
        return StreamingMunsitTTS(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **options)

    async def test_protocol_incremental_audio_and_timing(self):
        from unittest.mock import Mock
        timing = Mock()
        stream = AudioStream([b"a", b"bc", b"def"])
        tts = self.service(stream, timing=timing)
        try:
            frames = [f async for f in tts.run_tts("مرحبا")]
            audio = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
            self.assertEqual(b"".join(f.audio for f in audio), b"abcdef")
            self.assertGreater(len(audio), 1)
            self.assertTrue(all(f.sample_rate == 24000 and f.num_channels == 1 and len(f.audio) % 2 == 0 for f in audio))
            request = self.requests[0]
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), "https://api.munsit.com/api/v1/text-to-speech/faseeh-v1-preview")
            self.assertEqual(request.headers["x-api-key"], "munsit-secret")
            self.assertEqual(json.loads(request.content), {"text": "مرحبا", "voice_id": "SyU41Fvh3zAI9ZRf81uuTYQ4",
                "speed": 1.0, "stability": 0.5, "streaming": True, "sample_rate": 24000})
            self.assertEqual([c.args[0] for c in timing.mark.call_args_list], ["tts_start", "tts_first"])
            self.assertIsInstance(frames[-1], TTSStoppedFrame)
            self.assertTrue(stream.closed)
        finally:
            await tts.close()

    async def test_failures_do_not_leak_or_retry(self):
        cases = [(401, [b"munsit-secret private text"], "application/json"),
                 (200, [], "audio/raw"), (200, [b"x"], "audio/raw"),
                 (200, [b"RIFFWAVE"], "audio/wav"), (200, [b"{}"], "application/json"),
                 (200, [b"ab"], "audio/raw;rate=48000"),
                 (200, [b"ab"], "audio/raw;channels=2"),
                 (200, [b"ab"], "audio/raw;codec=mp3")]
        for status, chunks, content_type in cases:
            with self.subTest(status=status, content_type=content_type, chunks=chunks):
                stream = AudioStream(chunks)
                tts = self.service(stream, status, content_type)
                try:
                    frames = [f async for f in tts.run_tts("private text")]
                    errors = [f.error for f in frames if isinstance(f, ErrorFrame)]
                    self.assertEqual(len(errors), 1)
                    self.assertNotIn("munsit-secret", errors[0])
                    self.assertNotIn("private text", errors[0])
                    self.assertFalse(any(isinstance(f, TTSAudioRawFrame) for f in frames))
                    self.assertEqual(len(self.requests), 1)
                    self.assertTrue(stream.closed)
                    self.assertIsInstance(frames[-1], TTSStoppedFrame)
                finally:
                    await tts.close()

    async def test_interruption_closes_response_and_next_turn_works(self):
        stream = AudioStream([bytes(480)], block=True)
        tts = self.service(stream)
        async def consume():
            return [f async for f in tts.run_tts("first")]
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(stream.waiting.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stream.closed)
            self.assertFalse(tts._active)
            stream.block = False
            frames = [f async for f in tts.run_tts("second")]
            self.assertTrue(any(isinstance(f, TTSAudioRawFrame) for f in frames))
            self.assertEqual(len(self.requests), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await tts.close()
            await tts.close()

    async def test_timeout_is_sanitized_and_not_retried(self):
        calls = []
        def fail(request):
            calls.append(request)
            raise httpx.ReadTimeout("munsit-secret private text")
        tts = StreamingMunsitTTS(client=httpx.AsyncClient(transport=httpx.MockTransport(fail)))
        try:
            frames = [f async for f in tts.run_tts("private text")]
            self.assertEqual(len(calls), 1)
            self.assertEqual([f.error for f in frames if isinstance(f, ErrorFrame)], ["Munsit speech synthesis failed"])
        finally:
            await tts.close()

    async def test_factory_agent_preferences_and_closed_service(self):
        from voice_agent.agents.models import VoicePreferences
        tts = create_tts()
        self.assertIsInstance(tts, StreamingMunsitTTS)
        await tts.close()
        self.assertTrue(any(isinstance(f, ErrorFrame) for f in [f async for f in tts.run_tts("closed")]))
        with patch.dict(os.environ, {"TTS_PROVIDER": "azure"}):
            tts = create_tts(language="ar", preferences=VoicePreferences(provider="munsit", voice_id="agent-voice"))
        try:
            self.assertIsInstance(tts, StreamingMunsitTTS)
            self.assertEqual(tts._config["voice_id"], "agent-voice")
        finally:
            await tts.close()


class TTSConfigTests(unittest.TestCase):
    def test_munsit_credentials_validation_and_redaction(self):
        with patch.dict(os.environ, {"TTS_PROVIDER": "munsit", "MUNSIT_API_KEY": "test"}, clear=True), patch("voice_agent.config.load_dotenv"):
            load_settings(service="tts")
            with self.assertRaisesRegex(ValueError, "AZURE_SPEECH_KEY"):
                load_settings()
        with patch.dict(os.environ, {"TTS_PROVIDER": "munsit"}, clear=True), patch("voice_agent.config.load_dotenv"):
            with self.assertRaisesRegex(ValueError, "MUNSIT_API_KEY"):
                load_settings(service="tts")
        for name, value in (("MUNSIT_SPEED", "fast"), ("MUNSIT_SPEED", "nan"), ("MUNSIT_SPEED", "1.3"),
                            ("MUNSIT_SPEED", "0.6"), ("MUNSIT_STABILITY", "inf"),
                            ("MUNSIT_STABILITY", "-0.1"), ("MUNSIT_VOICE_ID", "")):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}, clear=True):
                with self.assertRaises(ValueError):
                    munsit_settings()
        with patch.dict(os.environ, {"MUNSIT_API_KEY": "secret-munsit"}):
            self.assertNotIn("secret-munsit", safe_diagnostic("failed secret-munsit"))

    def test_selected_provider_credentials_only_for_tts_check(self):
        with patch.dict(os.environ, {"TTS_PROVIDER": "cartesia", "CARTESIA_API_KEY": "test"}, clear=True), patch("voice_agent.config.load_dotenv"):
            load_settings(service="tts")
            with self.assertRaisesRegex(ValueError, "AZURE_SPEECH_KEY"):
                load_settings()
        with patch.dict(os.environ, {"TTS_PROVIDER": "cartesia"}, clear=True), patch("voice_agent.config.load_dotenv"):
            with self.assertRaisesRegex(ValueError, "CARTESIA_API_KEY"):
                load_settings(service="tts")
        with patch.dict(os.environ, {"TTS_PROVIDER": "azure"}, clear=True), patch("voice_agent.config.load_dotenv"):
            with self.assertRaisesRegex(ValueError, "AZURE_SPEECH_KEY"):
                load_settings(service="tts")

    def test_invalid_provider_and_generation_settings(self):
        with patch.dict(os.environ, {"TTS_PROVIDER": "invalid"}):
            with self.assertRaises(ValueError):
                tts_provider()
        for name, value in (("CARTESIA_SPEED", "fast"), ("CARTESIA_SPEED", "nan"), ("CARTESIA_SPEED", "1.6"), ("CARTESIA_VOLUME", "0.4"), ("CARTESIA_VOLUME", "inf"), ("CARTESIA_VOICE_ID", "")):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}, clear=True):
                with self.assertRaises(ValueError):
                    cartesia_settings()

    def test_redact_cartesia_key(self):
        with patch.dict(os.environ, {"CARTESIA_API_KEY": "secret-cartesia"}):
            self.assertNotIn("secret-cartesia", safe_diagnostic("failed secret-cartesia"))
