"""Offline protocol and lifecycle checks; no Cartesia requests leave this process."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStoppedFrame
from config import load_settings
from diagnostics import safe_diagnostic
from tts_providers import StreamingCartesiaTTS, cartesia_settings, create_tts, tts_provider


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
        with patch("azure_services.StreamingAzureTTS") as azure:
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


class TTSConfigTests(unittest.TestCase):
    def test_selected_provider_credentials_only_for_tts_check(self):
        with patch.dict(os.environ, {"TTS_PROVIDER": "cartesia", "CARTESIA_API_KEY": "test"}, clear=True), patch("config.load_dotenv"):
            load_settings(service="tts")
            with self.assertRaisesRegex(ValueError, "AZURE_SPEECH_KEY"):
                load_settings()
        with patch.dict(os.environ, {"TTS_PROVIDER": "cartesia"}, clear=True), patch("config.load_dotenv"):
            with self.assertRaisesRegex(ValueError, "CARTESIA_API_KEY"):
                load_settings(service="tts")
        with patch.dict(os.environ, {"TTS_PROVIDER": "azure"}, clear=True), patch("config.load_dotenv"):
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
