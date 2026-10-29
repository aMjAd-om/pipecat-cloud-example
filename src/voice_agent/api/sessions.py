"""Bounded session ownership and WebRTC connection lifecycle."""
import asyncio
import os
import secrets
import time
from dataclasses import dataclass, field
from fastapi import HTTPException
from loguru import logger
from pipecat.transports.network.webrtc_connection import SmallWebRTCConnection, IceServer
from voice_agent.voice.pipeline import main as run_bot
from voice_agent.transports.ice_lifecycle import install_ice_lifecycle_guard
from voice_agent.api.signaling import configured_ice_servers


@dataclass
class Session:
    id: str
    token: str
    ice: list
    created: float = field(default_factory=time.monotonic)
    last_live: float = field(default_factory=time.monotonic)
    connected: bool = False
    closing: bool = False
    connection: object = None
    job: asyncio.Task | None = None
    original_sdp: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stop: asyncio.Event = field(default_factory=asyncio.Event)


class Sessions:
    def __init__(self, *, pipeline=run_bot, connection_factory=SmallWebRTCConnection):
        install_ice_lifecycle_guard()
        self.items = {}
        self.limit = int(os.getenv("MAX_SESSIONS", "4"))
        self.max_age = int(os.getenv("SESSION_MAX_SECONDS", "900"))
        if not 1 <= self.limit <= 32 or not 30 <= self.max_age <= 3600:
            raise ValueError("Use MAX_SESSIONS=1..32 and SESSION_MAX_SECONDS=30..3600")
        self.pipeline = pipeline
        self.connection_factory = connection_factory
        self.cleanups = set()

    def create(self):
        if len(self.items) >= self.limit:
            raise HTTPException(429, "All conversation slots are occupied. Try again shortly.")
        session = Session(secrets.token_urlsafe(24), secrets.token_urlsafe(32), configured_ice_servers())
        self.items[session.id] = session
        return session

    def owned(self, sid, token):
        session = self.items.get(sid)
        if not session or session.closing or not secrets.compare_digest(session.token, token or ""):
            raise HTTPException(404, "Session not found. Click Start to create a new conversation.")
        return session

    def schedule_close(self, sid):
        job = asyncio.create_task(self.close(sid))
        self.cleanups.add(job)
        job.add_done_callback(self.cleanups.discard)

    async def offer(self, session, offer):
        async with session.lock:
            if session.closing:
                raise HTTPException(410, "Session has ended")
            if session.connection:
                if offer.pc_id is None:
                    if offer.sdp == session.original_sdp:
                        return session.connection.get_answer()
                    raise HTTPException(409, "An offer already exists for this session")
                if offer.pc_id != session.connection.pc_id:
                    raise HTTPException(404, "Peer connection not found")
                await asyncio.wait_for(session.connection.renegotiate(
                    sdp=offer.sdp, type=offer.type, restart_pc=offer.restart_pc), 25)
            else:
                if offer.pc_id is not None:
                    raise HTTPException(404, "Peer connection not found")
                connection = self.connection_factory(ice_servers=[IceServer(**s) for s in session.ice])
                session.connection = connection
                for event in ("closed", "failed"):
                    async def ended(_connection, sid=session.id):
                        self.schedule_close(sid)
                    connection.add_event_handler(event, ended)
                async def connected(_connection):
                    if not session.connected:
                        logger.info("WebRTC connected ms={:.1f}",
                                    (time.monotonic() - session.created) * 1000)
                    session.connected = True
                    session.last_live = time.monotonic()
                connection.add_event_handler("connected", connected)
                await asyncio.wait_for(connection.initialize(sdp=offer.sdp, type=offer.type), 25)
                session.original_sdp = offer.sdp
                session.job = asyncio.create_task(self.pipeline(connection, stop_event=session.stop))
                def finished(job):
                    if not job.cancelled() and job.exception():
                        logger.error("Session pipeline failed ({})", type(job.exception()).__name__)
                    self.schedule_close(session.id)
                session.job.add_done_callback(finished)
            return session.connection.get_answer()

    async def close(self, sid):
        session = self.items.get(sid)
        if not session or session.closing:
            return
        session.closing = True
        try:
            async with session.lock:
                # Let the owner call PipelineTask.cancel before interrupting its runner.
                if session.job and not session.job.done():
                    session.stop.set()
                    await asyncio.gather(session.job, return_exceptions=True)
                if session.connection:
                    await session.connection.disconnect()
        except Exception as exc:
            logger.warning("Session cleanup failed ({})", type(exc).__name__)
        finally:
            self.items.pop(sid, None)

    async def reap(self):
        while True:
            await asyncio.sleep(2)
            now = time.monotonic()
            for session in list(self.items.values()):
                if session.connection and session.connection.is_connected():
                    session.last_live = now
                expired = now - session.created > self.max_age
                abandoned = not session.connected and now - session.created > 30
                lost = session.connected and now - session.last_live > 10
                if expired or abandoned or lost:
                    self.schedule_close(session.id)

    async def shutdown(self):
        await asyncio.gather(*(self.close(sid) for sid in list(self.items)))
        if self.cleanups:
            await asyncio.gather(*list(self.cleanups), return_exceptions=True)
