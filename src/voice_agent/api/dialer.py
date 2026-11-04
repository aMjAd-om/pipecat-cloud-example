"""Operator-owned outbound calls and bounded, in-memory live transcripts."""
import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from voice_agent.transports.ami import AMIClient, AMIError

TERMINAL = {"completed", "cancelled", "busy", "no_answer", "rejected", "failed"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[+0-9 ()-]{8,24}", value):
        raise ValueError("Enter an eight-digit Oman phone number")
    value = re.sub(r"[ ()-]", "", value)
    if value.startswith("+968"):
        value = value[4:]
    elif value.startswith("00968"):
        value = value[5:]
    if not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError("Enter an eight-digit Oman number, optionally prefixed with +968")
    if value.startswith("211308"):
        raise ValueError("The PBX's own number block cannot be called from this page")
    return value


class CallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    number: str = Field(min_length=8, max_length=24)
    request_id: str = Field(pattern=r"^[a-f0-9-]{36}$")


@dataclass
class Call:
    id: str
    number: str
    request_id: str
    status: str = "dialing"
    detail: str = "Calling the customer"
    created_at: str = field(default_factory=utc_now)
    answered_at: str | None = None
    ended_at: str | None = None
    touched: float = field(default_factory=time.monotonic)
    channel: str | None = None
    media_channel: str | None = None
    stop_event: object = None
    cancel_requested: bool = False
    messages: list = field(default_factory=list)
    transcript_chars: int = 0
    version: int = 0

    def view(self):
        return {key: getattr(self, key) for key in (
            "id", "number", "status", "detail", "created_at", "answered_at",
            "ended_at", "messages", "version")}


class Dialer:
    def __init__(self, *, client=None):
        self.enabled = os.getenv("OUTBOUND_ENABLED", "false").lower() == "true"
        self.limit = int(os.getenv("TELEPHONY_MAX_CALLS", "2"))
        self.duration = int(os.getenv("TELEPHONY_MAX_SECONDS", "900"))
        if not 1 <= self.limit <= 32 or not 30 <= self.duration <= 3600:
            raise ValueError("Invalid telephone call limits")
        allowed = os.getenv("OUTBOUND_ALLOWED_NUMBERS", "").strip()
        self.allowed = {normalize_number(number.strip()) for number in allowed.split(",") if number.strip()}
        self.calls = {}
        self.media = {}  # All telephone calls, including inbound calls.
        self.lock = asyncio.Lock()
        self.jobs = set()
        self.monitor = None
        self.stopping = False
        self.client = client or AMIClient(
            os.getenv("ASTERISK_AMI_USERNAME", "web-dialer"),
            os.getenv("ASTERISK_AMI_SECRET", ""),
            port=int(os.getenv("ASTERISK_AMI_PORT", "5038")),
            on_event=self.event, on_disconnect=self.disconnected)
        if client is not None:
            client.on_event, client.on_disconnect = self.event, self.disconnected

    def spawn(self, coroutine):
        job = asyncio.create_task(coroutine)
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)
        return job

    async def start(self):
        if self.enabled:
            self.monitor = asyncio.create_task(self._monitor())

    async def _monitor(self):
        while True:
            self.prune()
            try:
                if not self.client.connected:
                    await self.client.connect()
                    await self.client.action("Status")
                    for call in list(self.calls.values()):
                        if call.cancel_requested:
                            await self._hangup(call)
                await self.client.action("Ping")
                # A lost event must never leave a call running indefinitely.
                for call in list(self.calls.values()):
                    age = time.monotonic() - call.touched
                    deadline = self.duration + 45 if call.answered_at else 45
                    if call.status not in TERMINAL and age > deadline:
                        call.cancel_requested = True
                        if call.stop_event:
                            call.stop_event.set()
                        await self._hangup(call)
                        self.finish(call, "failed", "Call timed out")
                self.prune()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except (OSError, AMIError, asyncio.TimeoutError):
                logger.warning("Asterisk control unavailable; reconnecting")
                self.disconnected()
                await self.client.close()
                await asyncio.sleep(3)

    def settings(self):
        ready = self.enabled and self.client.connected and not self.stopping
        detail = ("Ready to call" if ready else "Outbound calling is disabled" if not self.enabled
                  else "Connecting to Asterisk")
        return dict(enabled=self.enabled, ready=ready, detail=detail,
                    caller_id="21130800", max_calls=self.limit,
                    calls=[call.view() for call in self.calls.values() if call.status not in TERMINAL])

    def get(self, identity):
        self.prune()
        call = self.calls.get(identity)
        if call is None:
            raise HTTPException(404, "Call not found or expired")
        return call

    def prune(self):
        now = time.monotonic()
        ended = [call for call in self.calls.values() if call.status in TERMINAL]
        for index, call in enumerate(ended):
            if now - call.touched > 3600 or index < len(ended) - 20:
                self.calls.pop(call.id, None)

    def update(self, call, status, detail):
        if call.status in TERMINAL:
            return
        call.status, call.detail = status, detail
        call.version += 1

    def finish(self, call, status, detail):
        if call.status in TERMINAL:
            return
        self.update(call, status, detail)
        call.ended_at = utc_now()
        call.touched = time.monotonic()
        if call.stop_event:
            call.stop_event.set()

    async def originate(self, request):
        try:
            number = normalize_number(request.number)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        async with self.lock:
            self.prune()
            previous = next((call for call in self.calls.values() if call.request_id == request.request_id), None)
            if previous:
                if previous.number != number:
                    raise HTTPException(409, "This request already belongs to another number")
                return previous.view()
            if not self.settings()["ready"]:
                raise HTTPException(503, self.settings()["detail"])
            if self.allowed and number not in self.allowed:
                raise HTTPException(403, "This number is not in the configured test destinations")
            active = [call for call in self.calls.values() if call.status not in TERMINAL]
            incoming = sum(not identity for identity in self.media.values())
            if len(active) + incoming >= self.limit:
                raise HTTPException(429, "All telephone lines are in use")
            if any(call.number == number for call in active):
                raise HTTPException(409, "A call to this number is already active")
            call = Call(uuid4().hex, number, request.request_id)
            self.calls[call.id] = call
        # The response is polled even if the HTTP caller disconnects. No retries
        # of an originate action: a lost acknowledgement may still place a call.
        self.spawn(self._originate(call))
        return call.view()

    async def _originate(self, call):
        try:
            await self.client.action(
                "Originate", action_id=call.id, Channel=f"Local/{call.number}@web-dialer/n",
                Context="from-web-agent", Exten="s", Priority="1",
                CallerID="21130800", Timeout="35000", Async="true",
                ChannelId=call.id, OtherChannelId=call.id + "-peer",
                Variable=[f"__WEB_CALL_ID={call.id}", f"__WEB_MAX_SECONDS={self.duration}"],
                Codecs="alaw")
        except (AMIError, OSError, asyncio.TimeoutError):
            # The PBX might have received the action. Reconcile/hang up its
            # exact channel when Newchannel arrives, without redialling.
            call.cancel_requested = True
            self.update(call, "cancelling", "Call could not be confirmed; stopping it")
            await self._hangup(call)

    async def _hangup(self, call):
        if call.channel and self.client.connected:
            try:
                await self.client.action("Hangup", Channel=call.channel, Cause="16")
            except (AMIError, OSError, asyncio.TimeoutError):
                pass  # Hangup may have won the race; the PBX timeout also applies.

    async def hangup(self, identity):
        call = self.get(identity)
        if call.status not in TERMINAL:
            call.cancel_requested = True
            self.update(call, "cancelling", "Ending the call")
            if call.stop_event:
                call.stop_event.set()
            await self._hangup(call)
        return call.view()

    def event(self, event):
        # Dial() runs on the second Local leg. Its final status arrives before
        # the owner leg hangs up and preserves busy/no-answer distinctions.
        if event.get("Event") in ("DialEnd", "DialState"):
            identity = event.get("Uniqueid", "")
            call = self.calls.get(identity[:-5]) if identity.endswith("-peer") else None
            if call:
                result = event.get("DialStatus")
                if result == "RINGING" and not call.cancel_requested:
                    self.update(call, "ringing", "The customer's phone is ringing")
                elif event["Event"] == "DialEnd" and result != "ANSWER":
                    status = "cancelled" if call.cancel_requested else {
                        "BUSY": "busy", "NOANSWER": "no_answer", "CANCEL": "no_answer",
                        "DONTCALL": "rejected"}.get(result, "failed")
                    self.finish(call, status, {"cancelled": "Call cancelled", "busy": "Customer is busy",
                                "no_answer": "No answer", "rejected": "Call declined",
                                "failed": "The call could not be placed"}[status])
            return
        call = self.calls.get(event.get("Uniqueid"))
        if call is None and event.get("Event") == "OriginateResponse":
            call = self.calls.get(event.get("ActionID"))
        if call is None:
            return
        if event.get("Channel"):
            call.channel = event["Channel"]
            if call.cancel_requested and event.get("Event") != "Hangup":
                self.spawn(self._hangup(call))
        kind = event.get("Event")
        if kind == "Newstate" and not call.cancel_requested:
            state = event.get("ChannelStateDesc")
            if state in ("Ring", "Ringing") and not call.answered_at:
                self.update(call, "ringing", "The customer's phone is ringing")
            elif state == "Up" and not call.answered_at:
                call.answered_at = utc_now()
                self.update(call, "connecting", "Answered; connecting the agent")
        elif kind == "OriginateResponse" and event.get("Response") == "Failure":
            reason = event.get("Reason")
            status = "busy" if reason == "5" else "no_answer" if reason in ("0", "1", "3") else "failed"
            self.finish(call, "cancelled" if call.cancel_requested else status,
                        "Call cancelled" if call.cancel_requested else "Call was not answered" if status == "no_answer"
                        else "Customer is busy" if status == "busy" else "The call could not be placed")
        elif kind == "Hangup":
            cause = event.get("Cause")
            status = ("cancelled" if call.cancel_requested else "completed" if call.answered_at
                      else {"17": "busy", "18": "no_answer", "19": "no_answer", "21": "rejected"}.get(cause, "failed"))
            self.finish(call, status, {"completed": "Call ended", "cancelled": "Call cancelled",
                        "busy": "Customer is busy", "no_answer": "No answer", "rejected": "Call declined",
                        "failed": "Call failed; check Asterisk and the trunk"}[status])

    def disconnected(self):
        for call in self.calls.values():
            if call.status not in TERMINAL:
                call.cancel_requested = True
                if call.stop_event:
                    call.stop_event.set()
                self.update(call, "cancelling", "Asterisk connection lost; stopping the call")

    def attach(self, identity, media_channel, stop_event):
        call = self.get(identity)
        if call.status in TERMINAL or call.cancel_requested or call.media_channel:
            raise ValueError("Call is no longer accepting media")
        call.media_channel, call.stop_event = media_channel, stop_event
        call.answered_at = call.answered_at or utc_now()
        self.update(call, "connected", "Agent and customer are connected")
        return call

    def transcript(self, identity, role, text):
        call = self.calls.get(identity)
        if not call or call.status in TERMINAL or role not in ("agent", "customer") or not text.strip():
            return
        if len(call.messages) >= 1000 or call.transcript_chars >= 160000:
            return
        text = text.strip()[:min(6000, 160000 - call.transcript_chars)]
        call.transcript_chars += len(text)
        call.messages.append(dict(id=len(call.messages) + 1, role=role,
                                  text=text, at=utc_now()))
        call.version += 1

    async def close(self):
        self.stopping = True
        for call in list(self.calls.values()):
            if call.status not in TERMINAL:
                await self.hangup(call.id)
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        for job in list(self.jobs):
            job.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)
        await self.client.close()


def register_dialer_routes(app, dialer):
    @app.get("/api/settings")
    async def settings():
        return dialer.settings()

    @app.post("/api/calls", status_code=202)
    async def call(request: CallRequest):
        return await dialer.originate(request)

    @app.get("/api/calls/{identity}")
    async def status(identity: str):
        return dialer.get(identity).view()

    @app.delete("/api/calls/{identity}")
    async def hangup(identity: str):
        return await dialer.hangup(identity)
