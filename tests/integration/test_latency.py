"""Trace the measured boundaries without provider calls or recorded audio."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMTextFrame, TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_transport import TransportParams

from voice_agent.observability.timing import LatencyPlaybackFrame, LatencyProbe, TurnTiming
from voice_agent.transports.asterisk import AsteriskOutput, AsteriskPeer
from voice_agent.voice.language import LanguageLockedResponse


class LatencyTests(unittest.IsolatedAsyncioTestCase):
    def test_final_before_vad_end_preserves_negative_offset(self):
        timing = TurnTiming(origin=0)
        with patch("voice_agent.observability.timing.logger.info") as log:
            timing.mark("final", at=1)
            timing.mark("speech_end", at=1.4)
        metric = next(c for c in log.call_args_list if "stt_final_after_vad_end" in c.args)
        self.assertAlmostEqual(metric.args[-1], -400)

    def test_queued_response_keeps_identity_after_new_turn_and_response(self):
        timing = TurnTiming()
        timing.begin()
        timing.start_response()
        queued = timing.snapshot()
        timing.begin()
        timing.start_response()
        queued.mark("transport_first", at=123)
        self.assertEqual((queued.turn, queued.response), (1, 1))
        self.assertEqual((timing.turn, timing.response), (2, 2))
        self.assertNotIn("transport_first", timing.stamps)
        self.assertEqual(queued.call_id, timing.call_id)

    async def test_model_first_token_is_measured_before_language_buffer(self):
        timing = TurnTiming(origin=0)
        raw = LatencyProbe(timing, "llm")
        guard = LanguageLockedResponse("en")
        ready = LatencyProbe(timing, "language")
        self.addAsyncCleanup(raw.cleanup)
        self.addAsyncCleanup(guard.cleanup)
        self.addAsyncCleanup(ready.cleanup)
        raw.push_frame = AsyncMock(side_effect=guard.process_frame)
        guard.push_frame = AsyncMock(side_effect=ready.process_frame)
        ready.push_frame = AsyncMock()
        direction = FrameDirection.DOWNSTREAM
        with patch("voice_agent.observability.timing.time.monotonic", return_value=1):
            await raw.process_frame(LLMFullResponseStartFrame(), direction)
        with patch("voice_agent.observability.timing.time.monotonic", return_value=2):
            await raw.process_frame(LLMTextFrame("Hello."), direction)
        self.assertEqual(timing.stamps["llm_first"], 2)
        self.assertNotIn("language_ready", timing.stamps)
        with patch("voice_agent.observability.timing.time.monotonic", return_value=4):
            await raw.process_frame(LLMFullResponseEndFrame(), direction)
        self.assertEqual(timing.stamps["llm_end"], 4)
        self.assertEqual(timing.stamps["language_ready"], 4)

    async def test_transport_trace_waits_for_flow_control_and_successful_write(self):
        wire = AsyncMock()
        peer = AsteriskPeer(wire, {}, asyncio.Event())
        timing = peer.timing = TurnTiming()
        timing.begin()
        timing.start_response()
        timing.mark("output")
        queued = timing.snapshot()
        output = AsteriskOutput(peer, TransportParams())
        self.addAsyncCleanup(output.cleanup)
        await output.push_frame(LatencyPlaybackFrame(timing=queued))
        peer.event('{"event":"MEDIA_XOFF"}')
        pending = asyncio.create_task(peer.audio(bytes(640), peer.epoch))
        try:
            await asyncio.sleep(.01)
            self.assertNotIn("transport_first", queued.stamps)
            timing.begin()
            timing.start_response()
            peer.event('{"event":"MEDIA_XON"}')
            await asyncio.wait_for(pending, 1)
            wire.send_bytes.assert_awaited_once()
            self.assertIn("transport_first", queued.stamps)
            self.assertNotIn("transport_first", timing.stamps)
            with patch("pipecat.transports.base_output.BaseOutputTransport.push_frame", new=AsyncMock()):
                await output.push_frame(TTSStoppedFrame())
            self.assertAlmostEqual(queued.stamps["transport_tail"], peer.last_audio_end)
            self.assertEqual(timing.last_audio_end, peer.last_audio_end)
            await peer.flush()
            self.assertIsNone(peer.playback_timing)
            self.assertIsNone(timing.last_audio_end)
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
