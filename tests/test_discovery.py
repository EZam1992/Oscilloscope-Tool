"""Discovery tests. No real LAN traffic: the cache path is redirected to a temp
dir, and _probe is exercised against a throwaway server on 127.0.0.1."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from osci_tool import discovery


@pytest.fixture
def cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".osci_tool" / "scope_host.txt"
    monkeypatch.setattr(discovery, "_CACHE_PATH", path)
    return path


class TestResolveScopeHost:
    def test_uses_cached_host_when_it_answers(
        self, cache_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("192.168.1.110\n")
        monkeypatch.setattr(discovery, "_probe", lambda host: host == "192.168.1.110")
        monkeypatch.setattr(discovery, "_scan_local_subnet", _fail_if_called)

        assert discovery.resolve_scope_host() == "192.168.1.110"

    def test_scans_and_caches_when_cached_host_is_dead(
        self, cache_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("192.168.1.110")
        monkeypatch.setattr(discovery, "_probe", lambda host: False)
        monkeypatch.setattr(discovery, "_scan_local_subnet", lambda: "192.168.1.42")

        assert discovery.resolve_scope_host() == "192.168.1.42"
        assert cache_path.read_text() == "192.168.1.42"

    def test_scans_when_no_cache(
        self, cache_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_scan_local_subnet", lambda: "192.168.1.110")
        assert discovery.resolve_scope_host() == "192.168.1.110"
        assert cache_path.read_text() == "192.168.1.110"

    def test_force_rescan_skips_cache(
        self, cache_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("192.168.1.110")
        monkeypatch.setattr(discovery, "_probe", _fail_if_called)
        monkeypatch.setattr(discovery, "_scan_local_subnet", lambda: "192.168.1.7")

        assert discovery.resolve_scope_host(force_rescan=True) == "192.168.1.7"

    def test_raises_when_nothing_found(
        self, cache_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discovery, "_scan_local_subnet", lambda: None)
        with pytest.raises(RuntimeError, match="No SCPI device"):
            discovery.resolve_scope_host()
        assert not cache_path.exists()

    def test_blank_cache_treated_as_missing(
        self, cache_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("  \n")
        monkeypatch.setattr(discovery, "_probe", _fail_if_called)
        monkeypatch.setattr(discovery, "_scan_local_subnet", lambda: "192.168.1.110")
        assert discovery.resolve_scope_host() == "192.168.1.110"


class TestProbe:
    @pytest.fixture
    def server(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[bytes]]:
        """Local one-shot server; append the reply bytes to the yielded list."""
        replies: list[bytes] = []
        listener = socket.create_server(("127.0.0.1", 0))
        monkeypatch.setattr(discovery, "_SCPI_PORT", listener.getsockname()[1])

        def serve() -> None:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                conn.recv(64)
                if replies:
                    conn.sendall(replies[0])

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        yield replies
        listener.close()
        thread.join(timeout=1)

    def test_true_for_siglent_idn(self, server: list[bytes]) -> None:
        server.append(b"Siglent Technologies,SDS1104X-E,SDS00000,8.3.6.1.37R8\n")
        assert discovery._probe("127.0.0.1") is True

    def test_true_when_banner_precedes_idn(self, server: list[bytes]) -> None:
        server.append(b"Welcome to the SCPI instrument 'Siglent SDS1104X-E'\n")
        assert discovery._probe("127.0.0.1") is True

    def test_false_for_other_instrument(self, server: list[bytes]) -> None:
        server.append(b"KEYSIGHT TECHNOLOGIES,DSOX1204G,CN000,1.0\n")
        assert discovery._probe("127.0.0.1") is False

    def test_false_when_nothing_listening(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with socket.create_server(("127.0.0.1", 0)) as s:
            port = s.getsockname()[1]
        monkeypatch.setattr(discovery, "_SCPI_PORT", port)
        assert discovery._probe("127.0.0.1") is False


def _fail_if_called(*args: object) -> None:
    raise AssertionError("should not have been called")
