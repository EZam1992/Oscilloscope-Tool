from osci_tool.transport.scpi_lan import ScpiLanTransport

_WELCOME_PREFIX = "Welcome to the SCPI instrument"
_PROMPT_PREFIX = ">>"


class SiglentSDS1000XE:
    def __init__(self, transport: ScpiLanTransport) -> None:
        self._transport = transport

    def connect(self) -> None:
        self._transport.connect()

    def close(self) -> None:
        self._transport.close()

    def identify(self) -> str:
        return self._query("*IDN?")

    def set_channel(self, channel: int, enabled: bool) -> None:
        self._validate_channel(channel)
        mode = "ON" if enabled else "OFF"
        self._transport.write(f"C{channel}:TRA {mode}")

    def is_channel_enabled(self, channel: int) -> bool:
        self._validate_channel(channel)
        response = self._query(f"C{channel}:TRA?")
        mode = response.rsplit(" ", 1)[-1]
        return mode == "ON"

    @staticmethod
    def _validate_channel(channel: int) -> None:
        if channel not in (1, 2, 3, 4):
            raise ValueError(f"channel must be 1-4, got {channel}")

    def _query(self, command: str) -> str:
        raw = self._transport.query(command)
        return self._strip_framing(raw)

    @staticmethod
    def _strip_framing(raw: str) -> str:
        lines = [line for line in raw.splitlines() if line.strip()]
        lines = [line for line in lines if not line.startswith(_WELCOME_PREFIX)]
        if not lines:
            return ""
        last = lines[-1]
        if last.startswith(_PROMPT_PREFIX):
            last = last[len(_PROMPT_PREFIX):]
        return last.lstrip("\x00").strip()

    def __enter__(self) -> "SiglentSDS1000XE":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
