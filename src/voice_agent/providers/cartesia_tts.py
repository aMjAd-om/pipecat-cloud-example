"""Selectable speech synthesis; transports receive 24 kHz mono PCM."""
import asyncio
import os

import httpx
from loguru import logger
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.services.tts_service import TTSService


def cartesia_settings():
    settings = {
        "model_id": os.getenv("CARTESIA_MODEL", "sonic-3.6").strip(),
        "voice": os.getenv("CARTESIA_VOICE_ID", "47c38ca4-5f35-497b-b1a3-415245fb35e1").strip(),
    }
    for name, default, low, high in (("speed", "1.3", .6, 1.5), ("volume", "1", .5, 2)):
        try:
            value = float(os.getenv("CARTESIA_" + name.upper(), default))
        except ValueError:
            raise ValueError(f"CARTESIA_{name.upper()} must be between {low} and {high}") from None
        if not low <= value <= high:
            raise ValueError(f"CARTESIA_{name.upper()} must be between {low} and {high}")
        settings[name] = value
    if not settings["model_id"] or not settings["voice"]:
        raise ValueError("Set CARTESIA_MODEL and CARTESIA_VOICE_ID")
    return settings


class StreamingCartesiaTTS(TTSService):
    """Stream the selected Cartesia provider without saving speech to disk.

    Pipecat cancels the synthesis coroutine on interruption, closing its HTTP
    response. No automatic retries that could replay partially spoken text.
    """

    def __init__(self, *, timing=None, client=None):
        super().__init__(sample_rate=24000)
        self._config = cartesia_settings()
        self._language = os.getenv("DEFAULT_LANGUAGE", "ar")
        self._key = os.environ["CARTESIA_API_KEY"]
        self.timing = timing
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10), follow_redirects=False)
        self._active = set()
        self._closed = False

    async def run_tts(self, text):
        if self._closed:
            yield ErrorFrame("Cartesia TTS is closed")
            return
        request = {
            "model_id": self._config["model_id"], "voice": self._config["voice"],
            "transcript": text, "language": self._language,
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 24000},
            "generation_config": {k: self._config[k] for k in ("speed", "volume")},
        }
        if self.timing:
            self.timing.mark("tts_start")
        yield TTSStartedFrame()
        try:
            async with asyncio.timeout(90):
                async with self._client.stream("POST", "https://api.cartesia.ai/tts/bytes",
                        headers={"Authorization": "Bearer " + self._key,
                                 "Cartesia-Version": "2026-08-14"}, json=request) as response:
                    self._active.add(response)
                    try:
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";")[0].lower()
                        if content_type and not (content_type.startswith("audio/") or content_type == "application/octet-stream"):
                            raise ValueError("Unexpected audio content type")
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
            # Never log response bodies, transcripts, request headers or keys.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.warning("Cartesia TTS failed: type={} status={}", type(exc).__name__, status)
            yield ErrorFrame("Cartesia speech synthesis failed" + (f" (HTTP {status})" if status else ""))
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
