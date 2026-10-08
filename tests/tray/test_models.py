"""The model files are downloaded on request, and nothing unverified is ever put in place."""
import hashlib
import http.client
import http.server
import threading

import pytest

from tolmach import config
from tolmach.tray import models
from tolmach.tray.models import ModelFile


class Site:
    """A local web server holding a few files; some of them misbehave."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.halfway: threading.Barrier | None = None    # every answer waits here in the middle of its body
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                site.requests.append(self.path)
                body = site.files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                half = len(body) // 2
                self.wfile.write(body[:half])
                self.wfile.flush()
                if site.halfway is not None:
                    site.halfway.wait()
                self.wfile.write(body[half:])

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def offer(self, path: str, body: bytes, target: str) -> ModelFile:
        self.files[path] = body
        return ModelFile(target, self.url(path), len(body), hashlib.sha256(body).hexdigest())


@pytest.fixture(autouse=True)
def direct(monkeypatch):
    """The test server is on this machine: a proxy from the environment must not be asked for it."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "models"
    path.mkdir()
    return path


@pytest.fixture
def site():
    site = Site()
    yield site
    site.server.shutdown()
    site.server.server_close()


BODY = bytes(range(256)) * 4000        # about 1 MB


def test_a_file_is_downloaded_checked_and_put_in_place(site, folder):
    wanted = site.offer("/encoder.onnx", BODY, "gigaam-v3/encoder.onnx")
    said = []
    models.fetch(wanted, folder, said.append)
    assert (folder / "gigaam-v3" / "encoder.onnx").read_bytes() == BODY
    assert not list(folder.rglob("*.part"))
    assert any("encoder.onnx" in line for line in said)


def test_a_file_with_the_wrong_content_is_never_put_in_place(site, folder):
    wanted = site.offer("/encoder.onnx", BODY, "encoder.onnx")
    site.files["/encoder.onnx"] = BODY[:-1] + b"!"          # same size, another content
    with pytest.raises(models.DownloadError, match="checksum"):
        models.fetch(wanted, folder, lambda line: None)
    assert list(folder.iterdir()) == []


def test_a_file_of_the_wrong_size_is_never_put_in_place(site, folder):
    wanted = site.offer("/encoder.onnx", BODY, "encoder.onnx")
    site.files["/encoder.onnx"] = BODY[:1000]
    with pytest.raises(models.DownloadError):
        models.fetch(wanted, folder, lambda line: None)
    assert list(folder.iterdir()) == []


def test_a_failed_download_into_a_subfolder_leaves_no_empty_folder_and_keeps_the_models_folder(site, folder):
    wanted = ModelFile("gigaam-v3/encoder.onnx", site.url("/nothing"), 10, "0" * 64)
    with pytest.raises(models.DownloadError):
        models.fetch(wanted, folder, lambda line: None)
    assert folder.is_dir() and list(folder.iterdir()) == []


def test_a_file_the_server_does_not_have_is_reported(site, folder):
    wanted = ModelFile("encoder.onnx", site.url("/nothing"), 10, "0" * 64)
    with pytest.raises(models.DownloadError, match="404"):
        models.fetch(wanted, folder, lambda line: None)


def test_a_server_that_is_not_there_is_reported(folder):
    wanted = ModelFile("encoder.onnx", "http://127.0.0.1:9/encoder.onnx", 10, "0" * 64)
    with pytest.raises(models.DownloadError):
        models.fetch(wanted, folder, lambda line: None)
    assert list(folder.iterdir()) == []


def test_two_downloads_of_the_same_file_do_not_spoil_each_other(site, folder):
    # Two launcher windows answered yes. Writing into one unfinished file, the second would cut
    # what the first had written, and a file of the right size with holes in it would be put in place.
    wanted = site.offer("/encoder.onnx", BODY, "encoder.onnx")
    site.halfway = threading.Barrier(2, timeout=10)
    failures = []

    def download():
        try:
            models.fetch(wanted, folder, lambda line: None)
        except Exception as e:
            failures.append(e)

    both = [threading.Thread(target=download) for _ in range(2)]
    for thread in both:
        thread.start()
    for thread in both:
        thread.join(timeout=20)
    assert failures == []
    assert (folder / "encoder.onnx").read_bytes() == BODY
    assert sorted(path.name for path in folder.iterdir()) == ["encoder.onnx"]


def test_the_unfinished_file_of_a_download_still_running_is_left_alone(site, folder):
    wanted = site.offer("/encoder.onnx", BODY, "encoder.onnx")
    theirs = folder / "encoder.onnx.4242.part"
    with open(theirs, "wb") as other:
        other.write(b"half of it")
        other.flush()
        models.fetch(wanted, folder, lambda line: None)
        assert theirs.read_bytes() == b"half of it"
    assert (folder / "encoder.onnx").read_bytes() == BODY


def test_what_an_interrupted_download_left_behind_is_cleared_away(site, folder):
    wanted = site.offer("/encoder.onnx", BODY, "encoder.onnx")
    (folder / "encoder.onnx.4242.part").write_bytes(b"half of it")   # that window was closed
    (folder / "encoder.onnx.part").write_bytes(b"older")
    models.fetch(wanted, folder, lambda line: None)
    assert sorted(path.name for path in folder.iterdir()) == ["encoder.onnx"]


def test_a_folder_that_cannot_be_made_is_reported_not_raised(site, folder):
    wanted = site.offer("/encoder.onnx", BODY, "gigaam-v3/encoder.onnx")
    (folder / "gigaam-v3").write_bytes(b"a file where the folder should be")
    with pytest.raises(models.DownloadError, match="encoder.onnx"):
        models.fetch(wanted, folder, lambda line: None)
    assert site.requests == []


def test_a_connection_cut_in_the_middle_is_reported_not_raised(folder, monkeypatch):
    class Cut:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            raise http.client.IncompleteRead(b"part")

    monkeypatch.setattr(models.urllib.request, "urlopen", lambda request, timeout: Cut())
    wanted = ModelFile("encoder.onnx", "https://example.invalid/encoder.onnx", 10, "0" * 64)
    with pytest.raises(models.DownloadError, match="encoder.onnx"):
        models.fetch(wanted, folder, lambda line: None)
    assert list(folder.iterdir()) == []


def test_only_the_files_the_settings_name_are_this_programs_to_fetch(folder):
    vad, encoder = models.FILES[0], models.FILES[1]
    named = [folder / vad.path, folder / "my-model" / "encoder.onnx"]
    assert models.among(named, folder) == (vad,)
    assert models.among([folder / encoder.path, folder / vad.path], folder) == (vad, encoder)
    assert models.among([], folder) == ()


def test_only_what_is_missing_is_downloaded(site, folder):
    have = site.offer("/vad.onnx", b"vad" * 100, "silero_vad.onnx")
    need = site.offer("/tokens.txt", b"tokens" * 100, "gigaam-v3/tokens.txt")
    (folder / "silero_vad.onnx").write_bytes(b"vad" * 100)
    assert models.missing(folder, (have, need)) == [need]
    assert models.ensure(folder, lambda line: None, (have, need)) is True
    assert site.requests == ["/tokens.txt"]
    assert models.missing(folder, (have, need)) == []


def test_a_file_cut_short_by_an_earlier_attempt_counts_as_missing(site, folder):
    need = site.offer("/tokens.txt", b"tokens" * 100, "tokens.txt")
    (folder / "tokens.txt").write_bytes(b"tok")
    assert models.missing(folder, (need,)) == [need]


def test_a_failed_download_is_said_and_stops_the_rest(site, folder):
    broken = ModelFile("a.onnx", site.url("/nothing"), 10, "0" * 64)
    fine = site.offer("/b.onnx", b"b" * 100, "b.onnx")
    said = []
    assert models.ensure(folder, said.append, (broken, fine)) is False
    assert site.requests == ["/nothing"]
    assert any("a.onnx" in line and "404" in line for line in said)


def test_progress_is_reported_as_the_file_arrives(site, folder, monkeypatch):
    monkeypatch.setattr(models, "CHUNK", 100_000)
    wanted = site.offer("/encoder.onnx", BODY, "encoder.onnx")
    said = []
    models.fetch(wanted, folder, said.append)
    percents = [line for line in said if "%" in line]
    assert len(percents) >= 3 and "100%" in percents[-1]


# --- the list itself


def test_the_list_covers_exactly_what_the_default_settings_ask_for():
    gateway = config.GatewayConfig()
    asked = {gateway.model.encoder, gateway.model.decoder, gateway.model.joiner, gateway.model.tokens,
             gateway.vad.model}
    assert {file.path for file in models.FILES} == asked


def test_every_file_is_pinned_to_a_size_and_a_checksum_and_fetched_over_https():
    for file in models.FILES:
        assert file.url.startswith("https://") and file.size > 0
        assert len(file.sha256) == 64 and int(file.sha256, 16) >= 0


def test_the_recognition_model_comes_from_one_fixed_revision():
    # A branch name in the address would let the files change under the same checksum list.
    for file in models.FILES:
        if "huggingface.co" in file.url:
            assert f"/resolve/{models.GIGAAM_REVISION}/" in file.url
            assert len(models.GIGAAM_REVISION) == 40


# --- the English model: nobody needs it to dictate, so it is fetched only when asked for


def test_the_english_model_is_the_one_the_default_settings_describe():
    english = config.english_model()
    assert {file.path for file in models.ENGLISH} == {english.encoder, english.decoder, english.joiner, english.tokens}
    assert not {file.path for file in models.ENGLISH} & {file.path for file in models.FILES}     # the launcher never offers it
    assert models.ENGLISH_MB == 663


def test_the_english_model_is_pinned_like_the_main_one():
    for file in models.ENGLISH:
        assert file.url.startswith("https://huggingface.co/") and f"/resolve/{models.PARAKEET_REVISION}/" in file.url
        assert file.size > 0 and len(file.sha256) == 64 and int(file.sha256, 16) >= 0
    assert len(models.PARAKEET_REVISION) == 40


def test_whoever_downloads_can_count_the_bytes_as_they_arrive(site, folder, monkeypatch):
    monkeypatch.setattr(models, "CHUNK", 100_000)
    first = site.offer("/a.onnx", BODY, "en/a.onnx")
    second = site.offer("/b.txt", b"tokens" * 10, "en/b.txt")
    counted = []
    assert models.ensure(folder, lambda line: None, (first, second), counted.append) is True
    assert sum(counted) == len(BODY) + 60 and len(counted) >= 10
