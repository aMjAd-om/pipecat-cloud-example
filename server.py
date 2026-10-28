"""Same-origin signaling and bounded, isolated native SmallWebRTC sessions."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from pipecat.transports.network.webrtc_connection import SmallWebRTCConnection, IceServer

from bot import main as run_bot, configure_logging
from ice_lifecycle import install_ice_lifecycle_guard


def configured_ice_servers():
    servers = json.loads(os.getenv("ICE_SERVERS", "[]"))
    if not isinstance(servers, list):
        raise ValueError("ICE_SERVERS must be a JSON array")
    # Browser needs these short-lived TURN credentials, never Azure credentials.
    if os.getenv("TURN_URLS"):
        secret = os.environ["TURN_SECRET"]
        username = f"{int(time.time()) + 3600}:{secrets.token_hex(8)}"
        credential = base64.b64encode(hmac.new(
            secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        servers.append({"urls": os.environ["TURN_URLS"].split(","),
                        "username": username, "credential": credential})
    for server in servers:
        IceServer(**server)  # Validate against the installed aiortc API.
    return servers


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=30, max_length=120000)
    type: Literal["offer"]
    pc_id: str | None = Field(default=None, max_length=100)
    restart_pc: bool = False


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


def create_app(manager=None, *, validate_settings=True):
    sessions = manager or Sessions()
    origins = [s.strip() for s in os.getenv("FRONTEND_ORIGINS",
               "http://localhost:7860,http://127.0.0.1:7860").split(",") if s.strip()]

    @asynccontextmanager
    async def lifespan(app):
        if validate_settings:
            configure_logging()
            configured_ice_servers()
        janitor = asyncio.create_task(sessions.reap())
        try:
            from telephony_server import telephone_listener
            async with telephone_listener():
                yield
        finally:
            janitor.cancel()
            with suppress(asyncio.CancelledError):
                await janitor
            await sessions.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions = sessions
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def guard(request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in origins:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            if request.method == "POST":
                length = request.headers.get("content-length", "")
                if not length.isdigit() or int(length) > 128000:
                    return JSONResponse({"detail": "Request body missing or too large"}, status_code=413)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"detail": "Expected JSON"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _exc):
        return JSONResponse({"detail": "Invalid signaling request"}, status_code=422)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "sessions": len(sessions.items)}

    @app.post("/api/sessions")
    async def create(request: Request, response: Response):
        session = sessions.create()
        response.set_cookie("voice_session", session.token, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict",
                            max_age=sessions.max_age, path=f"/api/sessions/{session.id}")
        return {"id": session.id, "iceServers": session.ice}

    @app.post("/api/sessions/{sid}/offer")
    async def offer(sid: str, payload: Offer, request: Request):
        session = sessions.owned(sid, request.cookies.get("voice_session"))
        try:
            return await sessions.offer(session, payload)
        except HTTPException:
            raise
        except Exception:
            await sessions.close(sid)
            raise HTTPException(400, "WebRTC negotiation failed. Check ICE/network configuration.")

    @app.delete("/api/sessions/{sid}")
    async def stop(sid: str, request: Request):
        # Peer-close cleanup can win the race with the browser's Stop request.
        if sid not in sessions.items or sessions.items[sid].closing:
            return Response(status_code=204)
        sessions.owned(sid, request.cookies.get("voice_session"))
        await sessions.close(sid)
        response = Response(status_code=204)
        response.delete_cookie("voice_session", path=f"/api/sessions/{sid}")
        return response

    static = Path(__file__).parent / "frontend" / "dist"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app


def run():
    # Load .env before app construction so origins/limits/ICE settings apply.
    from config import load_settings
    load_settings()
    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "7860")), workers=1, access_log=False,
                timeout_graceful_shutdown=25)


if __name__ == "__main__":
    run()
