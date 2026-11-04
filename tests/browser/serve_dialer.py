"""Browser-test fixture only: simulated calls, no PBX/network/provider connection."""
import asyncio
import os
import uvicorn

from tests.integration.test_dialer import ENV
from voice_agent.api.app import create_app
from voice_agent.api.dialer import Dialer


class SimulatedControl:
    connected = True
    async def connect(self):
        self.connected = True
    async def close(self):
        self.connected = False
    async def action(self, action, **fields):
        if action == "Originate":
            identity = fields["ChannelId"]
            self.on_event(dict(Event="Newchannel", Uniqueid=identity, Channel="Local/simulated;1"))
            asyncio.create_task(self.conversation(identity))
        elif action == "Hangup":
            for call in self.dialer.calls.values():
                if call.channel == fields["Channel"]:
                    self.on_event(dict(Event="Hangup", Uniqueid=call.id, Cause="16"))
        return {"Response": "Success"}

    async def conversation(self, identity):
        self.on_event(dict(Event="Newstate", Uniqueid=identity, ChannelStateDesc="Ringing"))
        await asyncio.sleep(.8)
        if self.dialer.get(identity).cancel_requested:
            return
        self.on_event(dict(Event="Newstate", Uniqueid=identity, ChannelStateDesc="Up"))
        self.dialer.attach(identity, "simulated-media", asyncio.Event())
        for role, text in [("agent", "السلام عليكم"), ("customer", "وعليكم السلام، تفضل"),
                           ("agent", "معك من شركة داتامايننج، هل يناسبك نتحدث قليلاً؟"),
                           ("customer", "نعم، تفضل"),
                           ("customer", '<img src=x onerror="window.injected=true">')]:
            if self.dialer.get(identity).cancel_requested:
                return
            self.dialer.transcript(identity, role, text)
            await asyncio.sleep(.4)


def run():
    os.environ.update(ENV)
    # Prevent opening a real media listener in this explicit test fixture.
    from contextlib import asynccontextmanager
    from unittest.mock import patch
    @asynccontextmanager
    async def no_media(**kwargs):
        yield
    control = SimulatedControl()
    dialer = Dialer(client=control)
    control.dialer = dialer
    with patch("voice_agent.api.telephony.telephone_listener", no_media):
        uvicorn.run(create_app(dialer, validate_settings=False), host="127.0.0.1", port=17860,
                    access_log=False, log_level="warning")


if __name__ == "__main__":
    run()
