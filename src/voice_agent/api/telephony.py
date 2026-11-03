"""Private, opt-in Asterisk media listener. No browser routing or public port."""
import asyncio
import os
import json
from pathlib import Path
from contextlib import asynccontextmanager, nullcontext, suppress

from fastapi import FastAPI, WebSocket
from loguru import logger

from voice_agent.transports.asterisk import AsteriskPeer, AsteriskTransport, MediaProtocolError, media_start
from voice_agent.agents.models import SessionSelection
from voice_agent.agents.runtime import create_runtime


def selection_for_call(channel_id):
    """Only operator-controlled configuration can bind a call to an agent/customer."""
    configured = os.getenv("TELEPHONY_CALL_CONFIG")
    routes = {}
    if configured:
        path = Path(configured)
        if path.stat().st_size > 64000:
            raise ValueError("call configuration too large")
        routes = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(routes, dict):
            raise ValueError("invalid call configuration")
    selection = SessionSelection.model_validate(routes.get(channel_id, {}))
    if selection.agent_id is None and os.getenv("TELEPHONY_AGENT_ID"):
        selection = selection.model_copy(update={"agent_id": os.environ["TELEPHONY_AGENT_ID"]})
    if selection.mock_customer_id and os.getenv("DEMO_TESTING_ENABLED", "false").lower() != "true":
        raise ValueError("demo customer selection is disabled")
    return selection


def create_telephony_app(*, pipeline=None):
    if pipeline is None:
        from voice_agent.voice.pipeline import main
        pipeline = main
    limit = int(os.getenv("TELEPHONY_MAX_CALLS", "2"))
    duration = int(os.getenv("TELEPHONY_MAX_SECONDS", "900"))
    if not 1 <= limit <= 32 or not 30 <= duration <= 3600:
        raise ValueError("Invalid telephone call limits")
    calls = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        for peer in list(calls.values()):
            peer.stop_event.set()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.calls = calls

    @app.get("/health")
    async def health():
        return {"status": "ok", "calls": len(calls)}

    @app.websocket("/telephony/media")
    async def media(ws: WebSocket):
        # The listener also binds loopback. Reject remote clients and browser
        # origins; this is a trusted local PBX interface, not a public API.
        if (not ws.client or ws.client.host not in ("127.0.0.1", "::1")
                or ws.headers.get("origin")
                or "media" not in ws.scope.get("subprotocols", [])):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="media")
        peer = None
        key = None
        stage = "handshake"
        try:
            text = await asyncio.wait_for(ws.receive_text(), 5)
            start = media_start(text)
            # Channel ID prevents two socket connections owning one call.
            key = start["channel_id"]
            # Outbound calls all share a configured connection_id ("pipecat").
            # The Asterisk channel_id, not the client config name, owns the call.
            if key in calls or len(calls) >= limit:
                await ws.close(code=1013)
                return
            stop = asyncio.Event()
            selection = selection_for_call(key)
            runtime = (create_runtime(selection, session_id=key)
                       if os.getenv("AGENT_ID") or any(selection.model_dump().values()) else None)
            peer = AsteriskPeer(ws, start, stop)
            calls[key] = peer
            logger.info("Telephone call started; active={}", len(calls))
            stage = "transport_startup"
            transport = AsteriskTransport(peer)
            timer = asyncio.get_running_loop().call_later(duration, stop.set)
            try:
                stage = "pipeline"
                options = {"runtime": runtime} if runtime else {}
                await pipeline(None, stop_event=stop, transport=transport, **options)
            finally:
                timer.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = str(exc) if isinstance(exc, MediaProtocolError) else "see_stage"
            logger.warning("Telephone call rejected or failed; stage={} type={} reason={}",
                           stage, type(exc).__name__, reason)
        finally:
            if peer:
                try:
                    await peer.close()
                finally:
                    if calls.get(key) is peer:
                        del calls[key]
                    logger.info("Telephone call ended; active={}", len(calls))
            else:
                with suppress(Exception):
                    await ws.close(code=1008)

    return app


@asynccontextmanager
async def telephone_listener():
    """Run an additional loopback Uvicorn socket under the main app lifecycle."""
    if os.getenv("TELEPHONY_ENABLED", "false").lower() != "true":
        yield
        return
    import socket
    import uvicorn

    port = int(os.getenv("TELEPHONY_PORT", "7861"))
    if not 1024 <= port <= 65535 or port == int(os.getenv("PORT", "7860")):
        raise ValueError("TELEPHONY_PORT must be a separate port in 1024..65535")
    app = create_telephony_app()
    # Bind synchronously so a port conflict fails startup, rather than leaving
    # the browser healthy while telephone calls fail silently.
    sock = socket.socket()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, access_log=False,
        timeout_graceful_shutdown=10, ws_max_size=65500,
    ))
    # Uvicorn 0.34 uses capture_signals(), not install_signal_handlers().
    # Only the outer HTTP server owns process signals.
    server.capture_signals = nullcontext
    serving = None
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(32)
        sock.setblocking(False)
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if serving.done():
                await serving
                raise RuntimeError("Telephone listener did not start")
            await asyncio.sleep(0.01)
        logger.info("Telephone media listening on 127.0.0.1:{}", port)
        yield
    finally:
        for peer in list(app.state.calls.values()):
            peer.stop_event.set()
        server.should_exit = True
        if serving:
            try:
                await asyncio.wait_for(asyncio.shield(serving), 15)
            except asyncio.TimeoutError:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
        sock.close()
