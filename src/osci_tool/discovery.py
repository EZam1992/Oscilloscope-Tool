"""Find the scope's IP on the LAN without hardcoding it.

The SDS1000X-E is DHCP-assigned and re-leases on power-cycle, so the address
drifts. Strategy: try the last-known-good IP first (fast path), and only fall
back to a subnet sweep on SCPI port 5024 -- verified via *IDN? -- if that
fails. The last-good IP is cached outside the repo (~/.osci_tool), not in
version control, since it's a property of this bench, not the codebase.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_SCPI_PORT = 5024
_IDN_QUERY = b"*IDN?\n"
_IDN_MATCH = "SIGLENT"
_PROBE_TIMEOUT = 0.3
_CACHE_PATH = Path.home() / ".osci_tool" / "scope_host.txt"

logger = logging.getLogger(__name__)


def resolve_scope_host(*, force_rescan: bool = False) -> str:
    """Return a reachable IP for the scope, caching the result.

    Tries the cached IP first unless force_rescan is set, then falls back
    to scanning the local /24. Raises RuntimeError if nothing responds.
    """
    if not force_rescan:
        cached = _read_cache()
        if cached is not None and _probe(cached):
            return cached

    host = _scan_local_subnet()
    if host is None:
        raise RuntimeError(
            "No SCPI device found on the local subnet. "
            "Check the scope is powered on and on the same LAN."
        )
    _write_cache(host)
    return host


def _probe(host: str) -> bool:
    try:
        with socket.create_connection((host, _SCPI_PORT), timeout=_PROBE_TIMEOUT) as sock:
            sock.sendall(_IDN_QUERY)
            response = sock.recv(256).decode("ascii", errors="ignore")
            return _IDN_MATCH in response.upper()
    except OSError:
        return False


def _scan_local_subnet() -> str | None:
    network = _local_network()
    logger.info("Scanning %s for scope on port %d", network, _SCPI_PORT)
    candidates = [str(ip) for ip in network.hosts()]
    with ThreadPoolExecutor(max_workers=64) as pool:
        for host, found in zip(candidates, pool.map(_probe, candidates)):
            if found:
                logger.info("Found scope at %s", host)
                return host
    return None


def _local_network() -> ipaddress.IPv4Network:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
    return ipaddress.IPv4Network(f"{local_ip}/24", strict=False)


def _read_cache() -> str | None:
    try:
        return _CACHE_PATH.read_text().strip() or None
    except FileNotFoundError:
        return None


def _write_cache(host: str) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(host)
