"""Small asynchronous AMI client. Credentials and raw AMI packets are never logged."""
import asyncio
from contextlib import suppress
from uuid import uuid4


class AMIError(RuntimeError):
    pass


class AMIClient:
    def __init__(self, username, secret, *, host="127.0.0.1", port=5038,
                 on_event=lambda event: None, on_disconnect=lambda: None):
        if host not in ("127.0.0.1", "::1"):
            raise ValueError("AMI must use loopback")
        if not 1 <= port <= 65535 or any(char in username + secret for char in "\r\n\x00"):
            raise ValueError("Invalid AMI settings")
        self.host, self.port = host, port
        self.username, self.secret = username, secret
        self.on_event, self.on_disconnect = on_event, on_disconnect
        self.connected = False
        self.writer = self.reader_task = None
        self.pending = {}
        self.write_lock = asyncio.Lock()

    async def connect(self):
        await self.close()
        reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, limit=131072), 5)
        greeting = await asyncio.wait_for(reader.readline(), 5)
        if not greeting.startswith(b"Asterisk Call Manager/"):
            await self.close()
            raise AMIError("Invalid AMI greeting")
        self.reader_task = asyncio.create_task(self._read(reader))
        try:
            await self.action("Login", Username=self.username, Secret=self.secret, Events="on")
            self.connected = True
        except BaseException:
            await self.close()
            raise

    async def action(self, action, *, action_id=None, **fields):
        if self.writer is None or self.writer.is_closing():
            raise AMIError("Asterisk control connection unavailable")
        identity = action_id or uuid4().hex
        packet = [("Action", action), ("ActionID", identity), *fields.items()]
        lines = []
        for key, value in packet:
            for item in value if isinstance(value, (list, tuple)) else [value]:
                if any(c in str(key) + str(item) for c in "\r\n\x00"):
                    raise ValueError("Invalid AMI field")
                lines.append(f"{key}: {item}\r\n")
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            async with self.write_lock:
                self.writer.write(("".join(lines) + "\r\n").encode())
                await asyncio.wait_for(self.writer.drain(), 5)
            response = await asyncio.wait_for(future, 8)
            if response.get("Response") != "Success":
                raise AMIError("Asterisk rejected the control action")
            return response
        finally:
            self.pending.pop(identity, None)

    async def _read(self, reader):
        try:
            while True:
                raw = await reader.readuntil(b"\r\n\r\n")
                event = {}
                for line in raw.decode("utf-8", errors="replace").split("\r\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        event[key] = value.lstrip()
                if "Event" in event:
                    self.on_event(event)
                elif "Response" in event:
                    future = self.pending.get(event.get("ActionID"))
                    if future and not future.done():
                        future.set_result(event)
        except asyncio.CancelledError:
            raise
        except (OSError, EOFError, ValueError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            pass
        finally:
            self.connected = False
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(AMIError("Asterisk control connection lost"))
            self.on_disconnect()

    async def close(self):
        self.connected = False
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
            self.reader_task = None
        if self.writer:
            self.writer.close()
            with suppress(OSError):
                await self.writer.wait_closed()
            self.writer = None
