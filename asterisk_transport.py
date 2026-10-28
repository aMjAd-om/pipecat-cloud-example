"""Asterisk chan_websocket JSON control + slin16 media, for Pipecat 0.0.72."""
import asyncio
import audioop
import json
import time
from contextlib import suppress
from uuid import UUID

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import InputAudioRawFrame, StartInterruptionFrame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams


def media_start(text):
    """Validate the negotiated wire format before allocating any Azure services."""
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError("Expected a small MEDIA_START JSON event")
    event = json.loads(text)
    if not isinstance(event, dict) or event.get("event") != "MEDIA_START":
        raise ValueError("Expected MEDIA_START")
    identity = event.get("connection_id")
    if not isinstance(identity, str):
        raise ValueError("Missing connection_id")
    UUID(identity)
    channel = event.get("channel_id")
    if not isinstance(channel, str) or not 1 <= len(channel) <= 200:
        raise ValueError("Missing channel_id")
    if (event.get("format") != "slin16"
            or type(event.get("optimal_frame_size")) is not int
            or event["optimal_frame_size"] != 640
            or event.get("ptime", 20) != 20):
        raise ValueError("Require slin16 mono, 16 kHz, 20 ms / 640 bytes")
    return event


class PCMResampler:
    """Keep filter state across Azure chunks (Python 3.11/3.12, as pinned)."""
    def __init__(self):
        self.state = None
        self.rates = None

    async def resample(self, data, in_rate, out_rate):
        if in_rate == out_rate:
            return data
        if self.rates != (in_rate, out_rate):
            self.state = None
            self.rates = (in_rate, out_rate)
        result, self.state = audioop.ratecv(data, 2, 1, in_rate, out_rate, self.state)
        return result


class AsteriskPeer:
    def __init__(self, websocket, start, stop_event):
        self.ws, self.start, self.stop_event = websocket, start, stop_event
        self.lock = asyncio.Lock()
        self.writable = asyncio.Event()
        self.writable.set()
        self.drained = asyncio.Event()
        self.closed = False
        self.epoch = 0
        self.next_send = 0.0

    async def command(self, command):
        async with self.lock:
            if not self.closed:
                await asyncio.wait_for(self.ws.send_text(json.dumps({"command": command})), 2)

    async def audio(self, data, epoch):
        # Pace locally as well as using Asterisk's queue: bounded latency, and
        # Pipecat's bot-speaking events track playback rather than upload speed.
        await asyncio.wait_for(self.writable.wait(), 5)
        await asyncio.sleep(max(0, self.next_send - time.monotonic()))
        async with self.lock:
            if self.closed or epoch != self.epoch:
                return
            self.drained.clear()
            await asyncio.wait_for(self.ws.send_bytes(data), 2)
            self.next_send = max(self.next_send, time.monotonic()) + len(data) / 32000

    async def flush(self):
        self.epoch += 1
        self.next_send = 0
        self.writable.set()
        await self.command("FLUSH_MEDIA")

    def event(self, text):
        if len(text) > 4096:
            raise ValueError("Oversized media control")
        event = json.loads(text)
        if not isinstance(event, dict):
            raise ValueError("Invalid media control")
        kind = event.get("event")
        if kind == "MEDIA_START":
            raise ValueError("Repeated MEDIA_START")
        if kind == "MEDIA_XOFF":
            self.writable.clear()
        elif kind == "MEDIA_XON":
            self.writable.set()
        elif kind == "QUEUE_DRAINED":
            self.drained.set()
        # DTMF and informational events are not survey answers.

    async def drain(self):
        self.drained.clear()
        await self.command("REPORT_QUEUE_DRAINED")
        await asyncio.wait_for(self.drained.wait(), 3)

    async def close(self):
        async with self.lock:
            if self.closed:
                return
            self.closed = True
            self.epoch += 1
            self.writable.set()
            self.stop_event.set()
            with suppress(Exception):
                await asyncio.wait_for(self.ws.close(), 2)


class AsteriskInput(BaseInputTransport):
    def __init__(self, owner, params):
        super().__init__(params)
        self.owner = owner
        self.receiver = None

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        self._audio_in_queue._maxsize = 100
        self.receiver = self.create_task(self.receive())
        await self.owner._call_event_handler("on_client_connected", self.owner.peer)

    async def receive(self):
        peer = self.owner.peer
        try:
            while not peer.closed:
                message = await asyncio.wait_for(peer.ws.receive(), 30)
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    data = message["bytes"]
                    if not data or len(data) > 6400 or len(data) % 2:
                        raise ValueError("Invalid PCM message")
                    # Bound buffering and fail a stalled pipeline instead of
                    # silently dropping speech or blocking control indefinitely.
                    await asyncio.wait_for(self.push_audio_frame(
                        InputAudioRawFrame(data, 16000, 1)), 2)
                elif message.get("text") is not None:
                    peer.event(message["text"])
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log incoming control payloads, audio, or customer speech.
            from loguru import logger
            logger.warning("Telephone media disconnected or invalid")
        finally:
            peer.stop_event.set()

    async def cancel(self, frame):
        await super().cancel(frame)
        if self.receiver:
            await self.cancel_task(self.receiver)
            self.receiver = None

    async def cleanup(self):
        if self.receiver:
            await self.cancel_task(self.receiver)
            self.receiver = None
        await super().cleanup()


class AsteriskOutput(BaseOutputTransport):
    def __init__(self, peer, params):
        super().__init__(params)
        self.peer = peer
        self.interrupting = False

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        for sender in self._media_senders.values():
            sender._audio_queue._maxsize = 100
            sender._resampler = PCMResampler()

    async def pad_tail(self):
        for sender in self._media_senders.values():
            if sender._audio_buffer:
                data = bytes(sender._audio_buffer)
                sender._audio_buffer.clear()
                await sender.handle_audio_frame(TTSAudioRawFrame(
                    data.ljust(640, b"\x00"), 16000, 1))

    async def _handle_frame(self, frame):
        if isinstance(frame, TTSStoppedFrame):
            await self.pad_tail()
        await super()._handle_frame(frame)

    async def process_frame(self, frame, direction):
        if isinstance(frame, StartInterruptionFrame):
            self.interrupting = True
            # Invalidate a writer sleeping or waiting for MEDIA_XON before
            # cancelling Pipecat's sender and flushing the PBX playback queue.
            self.peer.epoch += 1
            try:
                await super().process_frame(frame, direction)
                for sender in self._media_senders.values():
                    sender._audio_buffer.clear()
                    sender._resampler = PCMResampler()
                    sender._audio_queue._maxsize = 100
                await self.peer.flush()
            finally:
                self.interrupting = False
        else:
            await super().process_frame(frame, direction)

    async def write_audio_frame(self, frame):
        if self.interrupting:
            return
        if frame.sample_rate != 16000 or frame.num_channels != 1 or len(frame.audio) != 640:
            raise ValueError("Output must be a 20 ms slin16 PCM frame")
        try:
            await self.peer.audio(frame.audio, self.peer.epoch)
        except Exception:
            self.peer.stop_event.set()

    async def stop(self, frame):
        await self.pad_tail()
        await super().stop(frame)
        # MediaSender has now finished playback-paced writes. Await PBX drain
        # before normal teardown so the final audio is not cut off.
        with suppress(asyncio.TimeoutError):
            await self.peer.drain()


class AsteriskTransport(BaseTransport):
    def __init__(self, peer, *, vad_analyzer=None):
        super().__init__()
        self.peer = peer
        params = TransportParams(
            audio_in_enabled=True, audio_out_enabled=True,
            audio_in_sample_rate=16000, audio_out_sample_rate=16000,
            audio_out_10ms_chunks=2,
            vad_analyzer=vad_analyzer or SileroVADAnalyzer(),
        )
        self._input = AsteriskInput(self, params)
        self._output = AsteriskOutput(peer, params)
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    def input(self):
        return self._input

    def output(self):
        return self._output
