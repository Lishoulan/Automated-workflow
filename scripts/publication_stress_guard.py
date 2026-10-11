"""Pytest-only isolation: no real transports, browsers or publishing commands."""
from pathlib import Path
import socket
import subprocess

import pytest


@pytest.fixture(autouse=True)
def isolate_stress_transports(monkeypatch):
    # SDK telemetry may spawn slow Windows machine-info commands; this test
    # exercises transport deadlines, not platform metadata discovery.
    monkeypatch.setattr("openai._base_client.get_platform", lambda: "isolated-stress-test")
    def deny(*args, **kwargs):
        raise RuntimeError("Real network access is disabled during publication stress tests")
    def loopback(address):
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}
    for method in ("connect", "connect_ex"):
        original_connect = getattr(socket.socket, method)
        def guarded(sock, address, _original=original_connect):
            if sock.family == getattr(socket, "AF_UNIX", None) or loopback(address):
                return _original(sock, address)
            return deny()
        monkeypatch.setattr(socket.socket, method, guarded)
    original_connection = socket.create_connection
    def local_connection(address, *args, **kwargs):
        if not loopback(address):
            return deny()
        return original_connection(address, *args, **kwargs)
    monkeypatch.setattr(socket, "create_connection", local_connection)
    original = subprocess.Popen
    def local_worker_only(command, *args, **kwargs):
        executable = Path(command[0]).name.lower() if isinstance(command, (list, tuple)) else ""
        if not (executable.startswith("python") or executable == "taskkill" or executable == "taskkill.exe"):
            raise RuntimeError("External commands are disabled during publication stress tests")
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", local_worker_only)
