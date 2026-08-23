import socket


class ScpiLanTransport:
    def __init__(self, host: str, port: int = 5024, timeout: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        self._sock = socket.create_connection(
            (self._host, self._port), timeout=self._timeout
        )

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

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
        data = bytearray()
        while len(data) < count:
            chunk = self._sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("Socket closed by remote host")
            data.extend(chunk)
        return bytes(data)

    def _send(self, command: str) -> None:
        if self._sock is None:
            raise RuntimeError("Not connected")
        self._sock.sendall(command.encode("ascii") + b"\n")

    def _receive(self) -> str:
        if self._sock is None:
            raise RuntimeError("Not connected")
        buffer = bytearray()
        while not buffer.endswith(b"\n"):
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Socket closed by remote host")
            buffer.extend(chunk)
        return buffer.decode("ascii").strip()

    def __enter__(self) -> "ScpiLanTransport":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
