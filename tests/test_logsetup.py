import logging
from logging.handlers import RotatingFileHandler

from tolmach import logsetup, paths


def test_setup_writes_to_the_named_file():
    log = logsetup.setup("gateway")
    log.info("hello")
    for h in log.handlers:
        h.flush()
    assert "hello" in (paths.logs_dir() / "gateway.log").read_text(encoding="utf-8")


def test_setup_twice_adds_one_handler():
    logsetup.setup("gateway")
    log = logsetup.setup("gateway")
    assert len([h for h in log.handlers if isinstance(h, RotatingFileHandler)]) == 1


def test_rotation_limits():
    log = logsetup.setup("gateway")
    handler = next(h for h in log.handlers if isinstance(h, RotatingFileHandler))
    assert handler.maxBytes == 1_000_000
    assert handler.backupCount == 2


def test_child_loggers_reach_the_file():
    logsetup.setup("gateway")
    logging.getLogger("tolmach.gateway.worker").warning("from child")
    for h in logging.getLogger("tolmach").handlers:
        h.flush()
    assert "from child" in (paths.logs_dir() / "gateway.log").read_text(encoding="utf-8")


def test_server_and_loop_errors_reach_the_file_but_requests_do_not():
    logsetup.setup("gateway")
    logging.getLogger("aiohttp.server").error("handler blew up")
    logging.getLogger("asyncio").warning("task exception was never retrieved")
    logging.getLogger("aiohttp.access").info("GET /v1/realtime 101")
    for h in logging.getLogger("tolmach").handlers:
        h.flush()
    text = (paths.logs_dir() / "gateway.log").read_text(encoding="utf-8")
    assert "handler blew up" in text
    assert "never retrieved" in text
    assert "GET /v1/realtime" not in text
