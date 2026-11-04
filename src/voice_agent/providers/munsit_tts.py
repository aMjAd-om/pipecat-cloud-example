"""Munsit Faseeh speech streamed as 24 kHz mono PCM16 for Pipecat."""
import asyncio
import os
from email.message import Message

import httpx
from loguru import logger
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.services.tts_service import TTSService


def munsit_settings():
    settings = {
        "voice_id": os.getenv("MUNSIT_VOICE_ID", "SyU41Fvh3zAI9ZRf81uuTYQ4").strip(),
    }
    if not settings["voice_id"]:
        raise ValueError("Set MUNSIT_VOICE_ID")
    for name, default, low, high in (("speed", "1.0", .7, 1.2), ("stability", "0.5", 0, 1)):
        try:
            value = float(os.getenv("MUNSIT_" + name.upper(), default))
        except ValueError:
            raise ValueError(f"MUNSIT_{name.upper()} must be between {low} and {high}") from None
        if not low <= value <= high:
            raise ValueError(f"MUNSIT_{name.upper()} must be between {low} and {high}")
        settings[name] = value
    return settings


class StreamingMunsitTTS(TTSService):
    """Reuse an async HTTP client; cancellation closes only the current response.

    No automatic retries: replaying a partially spoken response would repeat speech.
    Language is accepted for the shared factory; Faseeh infers it from the text.
    """

    def __init__(self, *, timing=None, client=None, language=None, voice_id=None):
        super().__init__(sample_rate=24000)
        self._config = munsit_settings()
        if voice_id:
            self._config["voice_id"] = voice_id
        self._key = os.environ["MUNSIT_API_KEY"]
        self.timing = timing
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10), follow_redirects=False)
        self._active = set()
        self._closed = False

    async def run_tts(self, text):
        if self._closed:
            yield ErrorFrame("Munsit TTS is closed")
            return
        request = {**self._config, "text": text, "streaming": True, "sample_rate": 24000}
        if self.timing:
            self.timing.mark("tts_start")
        yield TTSStartedFrame()
        try:
            async with asyncio.timeout(90):
                async with self._client.stream(
                    "POST", "https://api.munsit.com/api/v1/text-to-speech/faseeh-v1-preview",
                    headers={"x-api-key": self._key}, json=request,
                ) as response:
                    self._active.add(response)
                    try:
                        response.raise_for_status()
                        # A WAV header or JSON error must never be played as raw PCM.
                        media = Message()
                        media["content-type"] = response.headers.get("content-type", "application/octet-stream")
                        if media.get_content_type() not in ("audio/raw", "audio/pcm", "application/octet-stream"):
                            raise ValueError("Expected raw PCM audio")
                        for name, expected in (("codec", "pcm16"), ("rate", "24000"), ("channels", "1")):
                            value = media.get_param(name)
                            if value is not None and value != expected:
                                raise ValueError("Unexpected PCM format")
                        pending = b""
                        received = False
                        async for chunk in response.aiter_bytes():
                            pending += chunk
                            size = len(pending) // 2 * 2
                            if size:
                                if self.timing and not received:
                                    self.timing.mark("tts_first")
                                received = True
                                yield TTSAudioRawFrame(pending[:size], 24000, 1)
                                pending = pending[size:]
                        if pending or not received:
                            raise ValueError("Empty or incomplete PCM audio")
                    finally:
                        self._active.discard(response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Log neither keys, request text, response bodies nor exception messages.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.warning("Munsit TTS failed: type={} status={}", type(exc).__name__, status)
            yield ErrorFrame("Munsit speech synthesis failed" + (f" (HTTP {status})" if status else ""))
        yield TTSStoppedFrame()

    async def close(self):
        self._closed = True
        for response in tuple(self._active):
            await response.aclose()
        await self._client.aclose()

    async def stop(self, frame):
        await self.close()
        await super().stop(frame)

    async def cancel(self, frame):
        await self.close()
        await super().cancel(frame)
