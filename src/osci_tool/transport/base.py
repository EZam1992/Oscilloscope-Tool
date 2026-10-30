"""Interface every transport must satisfy.

The driver depends on this Protocol, not on a concrete class, so a LAN socket,
a future USB/VISA link, or the offline simulator are interchangeable.
"""

from __future__ import annotations

from typing import Protocol


class Transport(Protocol):
    def connect(self) -> None: ...

    def close(self) -> None: ...

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...

    def query_binary_block(self, command: str) -> bytes: ...
