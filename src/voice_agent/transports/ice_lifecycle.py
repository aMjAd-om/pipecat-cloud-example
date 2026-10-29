"""Compatibility guard for aioice 0.10.2 STUN retries after UDP shutdown.

The pinned StunProtocol does not finish pending transactions on connection_lost.
Complete them through aioice's existing TransactionError path so Transaction.run
executes its finally block and cancels the retransmission timer. Do not suppress
event-loop errors or alter successful ICE packets, candidates or transport setup.
"""
from aioice import ice, stun


_OriginalStunProtocol = ice.StunProtocol


class ClosingSafeStunProtocol(_OriginalStunProtocol):
    def __init__(self, receiver):
        super().__init__(receiver)
        self._local_closing = False

    def _transport_closed(self):
        # aioice's TurnTransport is a datagram-like adapter without is_closing.
        # Its closure is tracked by close()/connection_lost() above and below.
        return (self._local_closing or self.transport is None or
                getattr(self.transport, "is_closing", lambda: False)())

    def _fail_transactions(self):
        # Local failure, not a packet sent to or received from the remote peer.
        for transaction_id, transaction in list(self.transactions.items()):
            transaction.response_received(stun.Message(
                message_method=stun.Method.BINDING,
                message_class=stun.Class.ERROR,
                transaction_id=transaction_id,
                attributes={"ERROR-CODE": (500, "Local ICE transport closed")},
            ), ("0.0.0.0", 0))

    def connection_lost(self, exc):
        self._local_closing = True
        self._fail_transactions()
        super().connection_lost(exc)

    async def close(self):
        self._local_closing = True
        self._fail_transactions()
        if self.transport is None:
            self.connection_lost(None)
            return
        await super().close()

    def send_stun(self, message, addr):
        # A ready retry callback can run before a failed request's finally block.
        # Never touch a closing socket, including before connection_lost arrives.
        if self._transport_closed():
            self._fail_transactions()
            return
        super().send_stun(message, addr)

    async def send_data(self, data, addr):
        if self._transport_closed():
            raise ConnectionError("Local ICE transport closed")
        await super().send_data(data, addr)


def install_ice_lifecycle_guard():
    """Install before creating peers, including the peer used on ICE restart.

    aioice's host and TURN protocol factories resolve this module attribute when
    constructing a protocol. Keep the dependency files untouched. The regression
    tests exercise the installed library APIs and must accompany version upgrades.
    """
    if ice.StunProtocol is _OriginalStunProtocol:
        ice.StunProtocol = ClosingSafeStunProtocol
