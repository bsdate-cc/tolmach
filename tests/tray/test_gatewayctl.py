import json
import socket
from dataclasses import replace
from pathlib import Path

from tolmach.config import GatewayConfig
from tolmach.tray import gatewayctl as g

OK = g.Health("ok", "gigaam-v3", "", 4242)


def test_parse_health_ok():
    body = json.dumps({"status": "ok", "model": "gigaam-v3", "error": None, "version": "0.1.0", "pid": 4242}).encode()
    assert g.parse_health(body) == OK


def test_parse_health_error_and_loading():
    body = json.dumps({"status": "error", "model": "gigaam-v3", "error": "encoder not found", "pid": 1}).encode()
    assert g.parse_health(body) == g.Health("error", "gigaam-v3", "encoder not found", 1)
    assert g.parse_health(b'{"status": "loading"}') == g.Health("loading", "", "", None)


def test_parse_health_rejects_garbage():
    assert g.parse_health(b"<html>") is None
    assert g.parse_health(b"[]") is None
    assert g.parse_health(b'{"status": "weird"}') is None
    assert g.parse_health(None) is None


def test_status_lines():
    assert g.status_line(None) == "Шлюз: не отвечает"
    assert g.status_line(OK) == "Шлюз: работает (gigaam-v3)"
    assert g.status_line(g.Health("loading", "", "", None)) == "Шлюз: запускается"
    assert g.status_line(g.Health("error", "", "порт занят", None)) == "Шлюз: ошибка: порт занят"


def test_glance_recording_and_finishing_outrank_the_gateway():
    assert g.glance("recording", None) == "recording"
    assert g.glance("finishing", None) == "finishing"


def test_glance_shows_the_ring_unless_the_gateway_is_ready():
    assert g.glance("idle", None) == "down"
    assert g.glance("idle", g.Health("loading", "", "", None)) == "down"
    assert g.glance("error", g.Health("error", "", "x", None)) == "down"


def test_glance_passes_client_state_when_the_gateway_is_ready():
    assert g.glance("idle", OK) == "idle"
    assert g.glance("error", OK) == "error"


def test_probe_of_a_closed_port_is_none():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert g.probe(replace(GatewayConfig(), port=port), timeout=0.5) is None


def test_launch_argv_prefers_pythonw(tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    argv, env = g.launch_argv(Path("C:/projects/tolmach"), str(tmp_path / "python.exe"))
    assert argv == [str(tmp_path / "pythonw.exe"), "-m", "tolmach.gateway"]
    assert env == {"PYTHONPATH": str(Path("C:/projects/tolmach"))}


def test_launch_argv_falls_back_to_the_given_interpreter(tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    argv, _ = g.launch_argv(Path("C:/x"), str(tmp_path / "python.exe"))
    assert argv[0] == str(tmp_path / "python.exe")


def test_stop_of_a_gateway_that_is_not_running_is_true():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert g.stop(replace(GatewayConfig(), port=port), "key") is True


def test_probe_treats_a_broken_http_response_as_not_answering(monkeypatch):
    import http.client

    class Opener:
        def open(self, *a, **k):
            raise http.client.BadStatusLine("x")

    monkeypatch.setattr(g, "_opener", Opener())
    assert g.probe(GatewayConfig()) is None


def test_stop_survives_a_broken_shutdown_response(monkeypatch):
    import http.client

    class Opener:
        def __init__(self):
            self.up = True

        def open(self, request, timeout=None):
            if getattr(request, "method", None) == "POST":
                self.up = False
                raise http.client.BadStatusLine("x")
            if not self.up:
                raise OSError("down")
            raise_ok = type("R", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None,
                                      "read": lambda s: b'{"status": "ok", "pid": 1}'})()
            return raise_ok

    monkeypatch.setattr(g, "_opener", Opener())
    assert g.stop(GatewayConfig(), "key", wait_s=2.0) is True
