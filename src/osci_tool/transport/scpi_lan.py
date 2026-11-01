import socket
from typing import Self


class ScpiLanTransport:
    def __init__(self, host: str, port: int = 5024, timeout: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = bytearray()

    def connect(self) -> None:
        self._sock = socket.create_connection(
            (self._host, self._port), timeout=self._timeout
        )
        self._buffer.clear()
        self._drain_banner()

    def _drain_banner(self) -> None:
        """Drain unsolicited welcome banner sent immediately by Siglent scope on TCP connect."""
        if self._sock is None:
            return
        get_timeout_fn = getattr(self._sock, "gettimeout", None)
        set_timeout_fn = getattr(self._sock, "settimeout", None)
        if not get_timeout_fn or not set_timeout_fn:
            return  # Not a real socket (e.g. test FakeSocket)

        orig_timeout = get_timeout_fn()
        set_timeout_fn(0.3)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                # The banner terminates with ">>" or "\r\n>>"
                if b">>" in chunk:
                    break
        except (TimeoutError, OSError):
            pass
        finally:
            if orig_timeout is not None:
                set_timeout_fn(orig_timeout)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._buffer.clear()

    def write(self, command: str) -> None:
        self._send(command)

    def query(self, command: str) -> str:
        self._send(command)
        return self._receive()

    def query_binary_block(self, command: str) -> bytes:
        self._send(command)
        return self._receive_binary_block()

    def _receive_binary_block(self) -> bytes:
        response = bytearray()
        while True:
            byte = self._recv_exact(1)
            response.extend(byte)
            if byte == b"#":
                break
        digit_count_byte = self._recv_exact(1)
        response.extend(digit_count_byte)
        digit_count = int(digit_count_byte)
        length_bytes = self._recv_exact(digit_count)
        response.extend(length_bytes)
        length = int(length_bytes)
        payload = self._recv_exact(length)
        response.extend(payload)
        terminator = self._recv_exact(2)
        response.extend(terminator)
        return bytes(response)

    def _recv_exact(self, count: int) -> bytes:
        if self._sock is None:
            raise RuntimeError("Not connected")
        while len(self._buffer) < count:
            chunk = self._sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise ConnectionError("Socket closed by remote host")
            self._buffer.extend(chunk)
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    def _send(self, command: str) -> None:
        if self._sock is None:
            raise RuntimeError("Not connected")
        self._sock.sendall(command.encode("ascii") + b"\n")

    def _receive(self) -> str:
        if self._sock is None:
            raise RuntimeError("Not connected")
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Socket closed by remote host")
            self._buffer.extend(chunk)
        newline_idx = self._buffer.index(b"\n")
        line = bytes(self._buffer[:newline_idx])
        del self._buffer[: newline_idx + 1]
        return line.decode("ascii").strip()

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
