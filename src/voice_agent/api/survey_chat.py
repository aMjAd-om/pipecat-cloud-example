"""Explicitly launched, loopback-only text preview of the production survey logic."""
import asyncio
import json
import os
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from voice_agent.paths import CHAT_ASSETS

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from voice_agent.conversation.language import safe_spoken_text
from voice_agent.conversation import ConversationManager, Questionnaire

ASSETS = CHAT_ASSETS


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=2000)
    turn_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=0)


@dataclass
class ChatSession:
    manager: ConversationManager
    records: deque = field(default_factory=lambda: deque(maxlen=100))
    touched: float = field(default_factory=time.monotonic)
    pending: asyncio.Task | None = None
    closed: bool = False


def view(session):
    manager = session.manager
    return dict(session_id=manager.session_id, version=manager.version,
                current_question=manager.question.model_dump(), answers=dict(manager.answers),
                paused=manager.paused, completed=manager.completed, cancelled=manager.cancelled,
                pending_clarification=manager.pending_clarification,
                pending_confirmation=manager.pending_confirmation,
                skipped=sorted(manager.skipped), language=manager.language,
                busy=session.pending is not None)


def create_app(*, decide=None, port=7862, questionnaire_path=None):
    """Tests inject decisions; normal startup uses the same FoundryDecisionClient."""
    sessions = {}
    token = secrets.token_urlsafe(32)
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    language = os.getenv("DEFAULT_LANGUAGE", "ar")
    model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
    path = questionnaire_path or os.getenv(
        "SURVEY_QUESTIONNAIRE", "questionnaires/customer-satisfaction.ar.json")
    client = None

    async def dispose(session):
        session.closed = True
        session.manager.clear()
        session.records.clear()
        if session.pending and session.pending is not asyncio.current_task():
            session.pending.cancel()
            await asyncio.gather(session.pending, return_exceptions=True)

    async def reap():
        while True:
            await asyncio.sleep(30)
            for sid, session in list(sessions.items()):
                if time.monotonic() - session.touched > 1800 and not session.pending:
                    sessions.pop(sid, None)
                    await dispose(session)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal decide, client
        if decide is None:
            from voice_agent.config import load_settings
            from openai import AsyncOpenAI
            from voice_agent.providers.foundry_llm import FoundryDecisionClient
            load_settings(service="llm")
            client = AsyncOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                                 base_url=os.environ["AZURE_OPENAI_BASE_URL"],
                                 timeout=30, max_retries=0)
            decide = FoundryDecisionClient(client, os.environ["AZURE_OPENAI_DEPLOYMENT"])
        janitor = asyncio.create_task(reap())
        try:
            yield
        finally:
            janitor.cancel()
            with suppress(asyncio.CancelledError):
                await janitor
            await asyncio.gather(*(dispose(s) for s in list(sessions.values())))
            sessions.clear()
            if client:
                await client.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions = sessions

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if (not request.client or request.client.host not in ("127.0.0.1", "::1")
                or request.headers.get("host") not in hosts
                or (request.headers.get("origin") and request.headers["origin"] not in origins)):
            return JSONResponse({"detail": "Local access only"}, status_code=403)
        if request.method not in ("GET", "HEAD"):
            if not secrets.compare_digest(request.headers.get("x-survey-chat-token", ""), token):
                return JSONResponse({"detail": "Reload the chat page"}, status_code=403)
            length = request.headers.get("content-length", "")
            if (not length.isdigit() or int(length) > 16000
                    or request.headers.get("content-type", "").split(";")[0] != "application/json"):
                return JSONResponse({"detail": "Expected a small JSON request"}, status_code=413)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
        })
        return response

    def owned(sid):
        session = sessions.get(sid)
        if session is None or session.closed:
            raise HTTPException(404, "This chat expired. Start a new conversation.")
        session.touched = time.monotonic()
        return session

    @app.get("/")
    async def page():
        return FileResponse(ASSETS / "index.html")

    @app.get("/chat.js")
    async def javascript():
        return FileResponse(ASSETS / "chat.js", media_type="text/javascript")

    @app.get("/chat.css")
    async def stylesheet():
        return FileResponse(ASSETS / "chat.css", media_type="text/css")

    @app.get("/api/settings")
    async def settings():
        return dict(token=token, language=language, model=model)

    @app.post("/api/sessions")
    async def new():
        if len(sessions) >= 8:
            raise HTTPException(429, "Close an existing chat before starting another.")
        # Reload questionnaire edits when starting a new conversation.
        manager = ConversationManager(Questionnaire.load(path), language=language)
        session = ChatSession(manager)
        sessions[manager.session_id] = session
        return dict(state=view(session), spoken_response=safe_spoken_text(manager.opening(), language))

    @app.post("/api/sessions/{sid}/turn")
    async def turn(sid: str, body: Turn):
        session = owned(sid)
        if session.pending:
            raise HTTPException(409, "A reply is already in progress.")
        manager = session.manager
        if body.expected_version != manager.version:
            raise HTTPException(409, "Survey state changed. Start a new conversation if needed.")
        if not body.text.strip():
            raise HTTPException(422, "Type a customer reply.")
        proposal = None

        async def trace(messages):
            nonlocal proposal
            raw, usage = await decide(messages)
            try:
                proposal = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                proposal = {"invalid_model_output": True}
            return raw, usage

        session.pending = asyncio.create_task(
            manager.turn(body.turn_id, body.text, trace, snapshot=manager.snapshot()))
        started = time.monotonic()
        try:
            result = await session.pending
            # Mirrors SurveyConversationProcessor's suppression of stale replies
            # and the final shared language boundary before phone/browser TTS.
            spoken = ("" if result.reason == "stale" else
                      safe_spoken_text(result.spoken_response, language) if result.spoken_response else "")
            metrics = (dict(manager.metrics[-1]) if manager.metrics and proposal is not None else {})
            record = dict(customer=body.text, spoken_response=spoken, proposed_decision=proposal,
                          follow_up=(safe_spoken_text(result.follow_up, language)
                                     if result.reason != "stale" and result.follow_up else ""),
                          pause_seconds=result.pause_seconds,
                          validation=dict(accepted=result.accepted, committed=result.committed,
                                          reason=result.reason),
                          elapsed_ms=round((time.monotonic() - started) * 1000, 1), metrics=metrics)
            session.records.append(record)
        except asyncio.CancelledError:
            if session.closed:
                raise HTTPException(409, "Conversation was reset.") from None
            raise
        finally:
            session.pending = None
        return {**record, "state": view(session)}

    @app.post("/api/sessions/{sid}/interrupt")
    async def interrupt(sid: str):
        session = owned(sid)
        session.manager.interrupt()
        return dict(state=view(session))

    @app.get("/api/sessions/{sid}/export")
    async def export(sid: str):
        session = owned(sid)
        return dict(state=view(session), records=list(session.records), model=model,
                    note="Text simulation; no STT, TTS, RTP or acoustic interruption testing.")

    @app.delete("/api/sessions/{sid}")
    async def close(sid: str):
        session = sessions.pop(sid, None)
        if session:
            await dispose(session)
        return {"closed": True}

    return app


def run():
    from voice_agent.config import load_settings
    import uvicorn
    load_settings(service="llm")
    port = int(os.getenv("SURVEY_CHAT_PORT", "7862"))
    if not 1024 <= port <= 65535:
        raise ValueError("SURVEY_CHAT_PORT must be in 1024..65535")
    uvicorn.run(create_app(port=port), host="127.0.0.1", port=port, access_log=False,
                workers=1, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    run()
