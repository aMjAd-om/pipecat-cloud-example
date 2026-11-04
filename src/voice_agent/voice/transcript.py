"""Observe final recognition and playback-queued speech without logging content."""
from pipecat.frames.frames import TranscriptionFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TranscriptObserver(FrameProcessor):
    def __init__(self, publish, role):
        super().__init__()
        self.publish, self.role = publish, role

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        expected = TranscriptionFrame if self.role == "customer" else TTSTextFrame
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, expected):
            self.publish(self.role, frame.text)
        await self.push_frame(frame, direction)
