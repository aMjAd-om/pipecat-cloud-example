"""Real selector UDP and STUN retry regression for the reported Linux traceback."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aioice import ice, stun
from voice_agent.transports.ice_lifecycle import (
    ClosingSafeStunProtocol, _OriginalStunProtocol, install_ice_lifecycle_guard,
)


class IceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        # Reproduce the Linux selector transport path even on Windows.
        self._asyncioRunner = asyncio.Runner(debug=True, loop_factory=asyncio.SelectorEventLoop)

    async def exercise_closed_socket(self, protocol_type, *, unexpected_loss=False):
        loop = asyncio.get_running_loop()
        failures = []
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: failures.append(context))
        receiver = Mock()
        transport = blackhole = pending = None
        try:
            blackhole, _ = await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: protocol_type(receiver), local_addr=("127.0.0.1", 0))
            with patch.object(stun, "RETRY_RTO", .01):
                pending = asyncio.create_task(protocol.request(
                    stun.Message(stun.Method.BINDING, stun.Class.REQUEST),
                    blackhole.get_extra_info("sockname")))
                await asyncio.sleep(0)  # First request sent, retry is scheduled.
                self.assertTrue(protocol.transactions)
                if unexpected_loss:
                    transport.close()
                else:
                    await protocol.close()
                await asyncio.sleep(.05)  # Past the retry deadline.
            if protocol_type is ClosingSafeStunProtocol:
                self.assertTrue(pending.done())
                with self.assertRaises(stun.TransactionError):
                    await pending
                self.assertFalse(protocol.transactions)
                with self.assertRaises(ConnectionError):
                    await protocol.send_data(b"late audio", ("127.0.0.1", 9))
            return failures
        finally:
            if pending:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if transport:
                transport.close()
            if blackhole:
                blackhole.close()
            await asyncio.sleep(0)
            loop.set_exception_handler(original_handler)

    async def test_reproduces_original_report_and_prevents_it(self):
        original = await self.exercise_closed_socket(_OriginalStunProtocol)
        self.assertTrue(original, "Unpatched dependency must reproduce the failure")
        self.assertTrue(any(isinstance(c.get("exception"), AttributeError) for c in original))
        self.assertEqual(await self.exercise_closed_socket(ClosingSafeStunProtocol), [])

    async def test_unexpected_socket_loss_finishes_transactions(self):
        self.assertEqual(await self.exercise_closed_socket(
            ClosingSafeStunProtocol, unexpected_loss=True), [])

    async def test_closing_transport_before_connection_lost_does_not_send(self):
        protocol = ClosingSafeStunProtocol(Mock())
        protocol.transport = Mock()
        protocol.transport.is_closing.return_value = True
        with self.assertRaises(stun.TransactionError):
            await protocol.request(stun.Message(stun.Method.BINDING, stun.Class.REQUEST),
                                   ("127.0.0.1", 9))
        protocol.transport.sendto.assert_not_called()
        self.assertFalse(protocol.transactions)

    async def test_successful_request_and_live_data_unchanged(self):
        loop = asyncio.get_running_loop()
        class Responder(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport
            def datagram_received(self, data, addr):
                if data == b"audio":
                    return
                request = stun.parse_message(data)
                self.transport.sendto(bytes(stun.Message(stun.Method.BINDING,
                    stun.Class.RESPONSE, transaction_id=request.transaction_id)), addr)
        peer, _ = await loop.create_datagram_endpoint(Responder, local_addr=("127.0.0.1", 0))
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: ClosingSafeStunProtocol(Mock()), local_addr=("127.0.0.1", 0))
        try:
            response, _ = await asyncio.wait_for(protocol.request(
                stun.Message(stun.Method.BINDING, stun.Class.REQUEST),
                peer.get_extra_info("sockname")), 1)
            self.assertEqual(response.message_class, stun.Class.RESPONSE)
            await protocol.send_data(b"audio", peer.get_extra_info("sockname"))
            self.assertFalse(protocol.transactions)
            await protocol.close()
            await protocol.close()  # Idempotent teardown.
        finally:
            transport.close()
            peer.close()
            await asyncio.sleep(0)

    async def test_guard_installs_for_actual_candidate_factories(self):
        install_ice_lifecycle_guard()
        install_ice_lifecycle_guard()
        self.assertIs(ice.StunProtocol, ClosingSafeStunProtocol)
        connection = ice.Connection(ice_controlling=True, use_ipv6=False)
        try:
            await connection.get_component_candidates(1, ["127.0.0.1"])
            self.assertTrue(connection._protocols)
            self.assertTrue(all(isinstance(p, ClosingSafeStunProtocol) for p in connection._protocols))
        finally:
            await connection.close()

    async def test_turn_adapter_without_is_closing_remains_compatible(self):
        from aioice.turn import TurnTransport
        inner = Mock()
        inner.send_data = AsyncMock()
        transport = TurnTransport(inner)
        self.assertFalse(hasattr(transport, "is_closing"))
        protocol = ClosingSafeStunProtocol(Mock())
        protocol.connection_made(transport)
        message = stun.Message(stun.Method.BINDING, stun.Class.REQUEST)
        protocol.send_stun(message, ("127.0.0.1", 1234))
        await asyncio.sleep(0)
        inner.send_data.assert_awaited_once_with(bytes(message), ("127.0.0.1", 1234))
        protocol.connection_lost(None)
        protocol.send_stun(message, ("127.0.0.1", 1234))
        await asyncio.sleep(0)
        self.assertEqual(inner.send_data.await_count, 1)
