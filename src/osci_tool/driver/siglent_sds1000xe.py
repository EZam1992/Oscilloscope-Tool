from osci_tool.transport.scpi_lan import ScpiLanTransport

_WELCOME_PREFIX = "Welcome to the SCPI instrument"
_PROMPT_PREFIX = ">>"
_VALID_COUPLING = frozenset({"A1M", "A50", "D1M", "D50", "GND"})
_VALID_TIME_DIV = frozenset({
    "1NS", "2NS", "5NS", "10NS", "20NS", "50NS", "100NS", "200NS", "500NS",
    "1US", "2US", "5US", "10US", "20US", "50US", "100US", "200US", "500US",
    "1MS", "2MS", "5MS", "10MS", "20MS", "50MS", "100MS", "200MS", "500MS",
    "1S", "2S", "5S", "10S", "20S", "50S", "100S",
})


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
        self._write(f"C{channel}:TRA {mode}")

    def is_channel_enabled(self, channel: int) -> bool:
        self._validate_channel(channel)
        response = self._query(f"C{channel}:TRA?")
        mode = response.rsplit(" ", 1)[-1]
        return mode == "ON"

    def set_vertical_scale(self, channel: int, volts_per_div: float) -> None:
        self._validate_channel(channel)
        self._write(f"C{channel}:VDIV {volts_per_div}")

    def get_vertical_scale(self, channel: int) -> float:
        self._validate_channel(channel)
        response = self._query(f"C{channel}:VDIV?")
        value_str = response.rsplit(" ", 1)[-1]
        return float(value_str.rstrip("V"))

    def set_offset(self, channel: int, offset_volts: float) -> None:
        self._validate_channel(channel)
        self._write(f"C{channel}:OFST {offset_volts}")

    def get_offset(self, channel: int) -> float:
        self._validate_channel(channel)
        response = self._query(f"C{channel}:OFST?")
        value_str = response.rsplit(" ", 1)[-1]
        return float(value_str.rstrip("V"))

    def set_coupling(self, channel: int, coupling: str) -> None:
        self._validate_channel(channel)
        if coupling not in _VALID_COUPLING:
            raise ValueError(
                f"coupling must be one of {sorted(_VALID_COUPLING)}, got {coupling!r}"
            )
        self._write(f"C{channel}:CPL {coupling}")

    def get_coupling(self, channel: int) -> str:
        self._validate_channel(channel)
        response = self._query(f"C{channel}:CPL?")
        return response.rsplit(" ", 1)[-1]

    def set_horizontal_scale(self, value: str) -> None:
        if value not in _VALID_TIME_DIV:
            raise ValueError(
                f"value must be one of {sorted(_VALID_TIME_DIV)}, got {value!r}"
            )
        self._write(f"TDIV {value}")

    def get_horizontal_scale(self) -> float:
        response = self._query("TDIV?")
        value_str = response.rsplit(" ", 1)[-1]
        return float(value_str.rstrip("S"))

    @staticmethod
    def _validate_channel(channel: int) -> None:
        if channel not in (1, 2, 3, 4):
            raise ValueError(f"channel must be 1-4, got {channel}")

    def _write(self, command: str) -> None:
        self._transport.write(command)
        self._query("*OPC?")

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
