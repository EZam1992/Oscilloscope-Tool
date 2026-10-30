"""ScpiLanTransport tests against a scripted fake socket (no network).

The fake socket hands back data in caller-chosen chunks, so we can check that
reads reassemble correctly no matter how TCP fragments the reply.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

import pytest

from osci_tool.transport.scpi_lan import ScpiLanTransport


class FakeSocket:
    """Returns each scripted chunk as its own recv() result (split further if
    bufsize is smaller), then b"" to signal the peer closed the connection."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = [bytearray(c) for c in chunks if c]
        self.sent = bytearray()
        self.closed = False

    def recv(self, bufsize: int) -> bytes:
        if not self._chunks:
            return b""
        head = self._chunks[0]
        data = bytes(head[:bufsize])
        del head[:bufsize]
        if not head:
            self._chunks.pop(0)
        return data

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def close(self) -> None:
        self.closed = True


def _connected(
    monkeypatch: pytest.MonkeyPatch, chunks: list[bytes]
) -> tuple[ScpiLanTransport, FakeSocket]:
    fake = FakeSocket(chunks)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: fake)
    transport = ScpiLanTransport("192.0.2.1")
    transport.connect()
    return transport, fake


def test_query_sends_newline_terminated_ascii(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, fake = _connected(monkeypatch, [b"1\n"])
    transport.query("*OPC?")
    assert bytes(fake.sent) == b"*OPC?\n"


def test_query_reassembles_fragmented_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _ = _connected(monkeypatch, [b"C1:VD", b"IV 5.00E-0", b"1V\n"])
    assert transport.query("C1:VDIV?") == "C1:VDIV 5.00E-01V"


def test_write_sends_without_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, fake = _connected(monkeypatch, [])
    transport.write("TRMD SINGLE")
    assert bytes(fake.sent) == b"TRMD SINGLE\n"


def test_binary_block_returned_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = bytes([0x00, 0x19, 0xE7, 0x7F, 0x80, ord("#"), ord("\n")])
    reply = b"C1:WF DAT2,#17" + payload + b"\n\n"
    transport, _ = _connected(monkeypatch, [reply])
    assert transport.query_binary_block("C1:WF? DAT2") == reply


def test_binary_block_byte_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = b"C2:WF DAT2,#210" + bytes(range(10)) + b"\n\n"
    transport, _ = _connected(monkeypatch, [bytes([b]) for b in reply])
    assert transport.query_binary_block("C2:WF? DAT2") == reply


def test_remote_close_raises_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _ = _connected(monkeypatch, [b"partial reply without newline"])
    with pytest.raises(ConnectionError):
        transport.query("*IDN?")


def test_truncated_binary_block_raises_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, _ = _connected(monkeypatch, [b"C1:WF DAT2,#3100" + bytes(40)])
    with pytest.raises(ConnectionError):
        transport.query_binary_block("C1:WF? DAT2")


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.write("*RST"),
        lambda t: t.query("*IDN?"),
        lambda t: t.query_binary_block("C1:WF? DAT2"),
    ],
)
def test_use_before_connect_raises(call: Callable[[ScpiLanTransport], object]) -> None:
    with pytest.raises(RuntimeError, match="Not connected"):
        call(ScpiLanTransport("192.0.2.1"))


def test_close_releases_socket_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, fake = _connected(monkeypatch, [])
    transport.close()
    transport.close()
    assert fake.closed
    with pytest.raises(RuntimeError):
        transport.write("*RST")
