"""Pickup timing, speech detection, consent boundaries, and silence hangup."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    LLMTextFrame, TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from voice_agent.voice.pickup import (
    PickupGreetingProcessor, PickupOpeningFrame, PickupPlaybackFrame, PickupPlaybackObserver,
)


class PickupTests(unittest.IsolatedAsyncioTestCase):
    def make_processor(self, *, autoplay=True, pickup_pause=.01, response_pause=.03):
        stop = asyncio.Event()
        opening = "معك مساعد ذكي من شركة داتامايننج، هل تسمح لنا نبدأ الاستبيان؟"
        processor = PickupGreetingProcessor(
            SimpleNamespace(language="ar", opening=lambda: opening), stop,
            pickup_pause=pickup_pause, response_pause=response_pause, playback_timeout=.3,
        )
        frames, texts, markers = [], [], []

        async def output(frame, direction=FrameDirection.DOWNSTREAM):
            frames.append(frame)
            if isinstance(frame, LLMTextFrame):
                texts.append(frame.text)
            elif isinstance(frame, PickupPlaybackFrame):
                markers.append(frame)
                if autoplay:
                    frame.finished.set()

        processor.push_frame = AsyncMock(side_effect=output)
        self.addAsyncCleanup(processor.cleanup)
        return processor, stop, frames, texts, markers

    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait(), 1)

    async def start(self, processor):
        await processor.process_frame(PickupOpeningFrame(), FrameDirection.DOWNSTREAM)

    async def test_silence_repeats_greeting_then_hello_then_ends_call(self):
        processor, stop, frames, texts, markers = self.make_processor(pickup_pause=.03)
        await self.start(processor)
        self.assertEqual(texts, [])
        await asyncio.wait_for(stop.wait(), 1)
        self.assertEqual(texts, ["السلام عليكم", "السلام عليكم", "مرحبا"])
        self.assertFalse(processor._ready)

    async def test_pause_starts_after_playback_not_synthesis(self):
        processor, stop, frames, texts, markers = self.make_processor(autoplay=False)
        await self.start(processor)
        await self.wait_until(lambda: len(markers) == 1)
        await asyncio.sleep(.06)
        self.assertEqual(texts, ["السلام عليكم"])
        self.assertFalse(stop.is_set())
        markers[0].finished.set()
        await self.wait_until(lambda: len(texts) == 2)

    async def test_customer_reply_at_each_attempt_introduces_once_without_consent(self):
        for attempt in (1, 2, 3):
            with self.subTest(attempt=attempt):
                processor, stop, frames, texts, markers = self.make_processor()
                await self.start(processor)
                await self.wait_until(lambda: len(texts) == attempt)
                greeting_reply = TranscriptionFrame("نعم", "caller", "pickup")
                await processor.process_frame(greeting_reply, FrameDirection.DOWNSTREAM)
                await self.wait_until(lambda: processor._ready)
                self.assertEqual(len(texts), attempt + 1)
                self.assertIn("شركة داتامايننج", texts[-1])
                self.assertNotIn(greeting_reply, frames)
                consent = TranscriptionFrame("نعم", "caller", "consent")
                await processor.process_frame(consent, FrameDirection.DOWNSTREAM)
                self.assertIn(consent, frames)
                await asyncio.sleep(.05)
                self.assertFalse(stop.is_set())
                self.assertEqual(len(texts), attempt + 1)

    async def test_vad_cancels_retries_before_transcription_and_waits_for_user(self):
        processor, stop, frames, texts, markers = self.make_processor()
        await self.start(processor)
        await self.wait_until(lambda: len(texts) == 1)
        await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(.11)
        self.assertEqual(texts, ["السلام عليكم"])
        self.assertFalse(stop.is_set())
        await processor.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await self.wait_until(lambda: processor._ready)
        self.assertEqual(len(texts), 2)

    async def test_empty_transcription_does_not_prevent_silence_hangup(self):
        processor, stop, frames, texts, markers = self.make_processor()
        await self.start(processor)
        await processor.process_frame(TranscriptionFrame("  ", "caller", "empty"),
                                      FrameDirection.DOWNSTREAM)
        await asyncio.wait_for(stop.wait(), 1)
        self.assertEqual(len(texts), 3)

    async def test_delayed_pickup_transcript_during_introduction_is_not_consent(self):
        processor, stop, frames, texts, markers = self.make_processor(autoplay=False)
        await self.start(processor)
        await self.wait_until(lambda: len(markers) == 1)
        await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await processor.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await self.wait_until(lambda: len(markers) == 2)
        late_reply = TranscriptionFrame("نعم", "caller", "late-pickup")
        await processor.process_frame(late_reply, FrameDirection.DOWNSTREAM)
        self.assertNotIn(late_reply, frames)
        self.assertFalse(processor._ready)
        markers[-1].finished.set()
        await self.wait_until(lambda: processor._ready)
        self.assertFalse(stop.is_set())

    async def test_early_customer_speech_skips_retries_and_is_not_a_survey_answer(self):
        processor, stop, frames, texts, markers = self.make_processor(pickup_pause=.1)
        await self.start(processor)
        reply = TranscriptionFrame("ألو", "caller", "early")
        await processor.process_frame(reply, FrameDirection.DOWNSTREAM)
        await self.wait_until(lambda: processor._ready)
        self.assertEqual(texts, [processor.manager.opening()])
        self.assertNotIn(reply, frames)

    async def test_disconnect_cancels_pending_greetings(self):
        processor, stop, frames, texts, markers = self.make_processor(pickup_pause=.05)
        await self.start(processor)
        await processor.cleanup()
        await asyncio.sleep(.07)
        self.assertEqual(texts, [])
        self.assertFalse(stop.is_set())

    async def test_duplicate_ready_event_does_not_start_second_sequence(self):
        processor, stop, frames, texts, markers = self.make_processor()
        await self.start(processor)
        await self.start(processor)
        await asyncio.wait_for(stop.wait(), 1)
        self.assertEqual(texts, ["السلام عليكم", "السلام عليكم", "مرحبا"])

    async def test_playback_failure_ends_call_without_unbounded_wait(self):
        processor, stop, frames, texts, markers = self.make_processor(autoplay=False)
        processor.playback_timeout = .03
        await self.start(processor)
        await asyncio.wait_for(stop.wait(), 1)
        self.assertEqual(texts, ["السلام عليكم"])

    async def test_output_observer_acknowledges_only_played_marker(self):
        observer = PickupPlaybackObserver()
        observer.push_frame = AsyncMock()
        marker = PickupPlaybackFrame()
        self.assertFalse(marker.finished.is_set())
        await observer.process_frame(marker, FrameDirection.DOWNSTREAM)
        self.assertTrue(marker.finished.is_set())
        observer.push_frame.assert_not_awaited()
        await observer.cleanup()
