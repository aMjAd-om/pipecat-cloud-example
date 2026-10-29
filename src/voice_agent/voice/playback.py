"""Acknowledge synthesized responses after paced audio playback."""
import asyncio
from dataclasses import dataclass, field

from pipecat.frames.frames import ControlFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class PlaybackMarkerFrame(ControlFrame):
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class PlaybackObserver(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, PlaybackMarkerFrame) and direction == FrameDirection.DOWNSTREAM:
            frame.finished.set()
        else:
            await self.push_frame(frame, direction)
