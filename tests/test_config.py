import json

from tolmach import config, paths


def write(text, encoding="utf-8"):
    paths.config_file().write_text(text, encoding=encoding)


def test_missing_file_gives_defaults():
    loaded = config.load()
    assert loaded.problems == []
    c = loaded.config
    assert (c.gateway.host, c.gateway.port, c.gateway.threads) == ("127.0.0.1", 8765, 4)
    assert c.gateway.model.name == "gigaam-v3"
    assert c.gateway.model.type == "nemo_transducer"
    assert c.gateway.vad.min_silence_s == 0.5
    assert c.gateway.draft_interval_s == 1.0
    assert c.gateway.max_phrase_s == 25.0
    assert c.client.hotkey == "shift+win+q"
    assert (c.client.button.vid, c.client.button.pid, c.client.button.mask) == ("1B3F", "2008", 128)
    assert c.client.muted_floor_dbfs == -70.0
    assert c.client.mute_other_apps is True
    assert c.client.overlay_tail_chars == 300
    assert c.autostart is True


def test_partial_file_overrides_only_what_it_names():
    write(json.dumps({"gateway": {"port": 9000, "vad": {"threshold": 0.6}}, "autostart": False}))
    loaded = config.load()
    assert loaded.problems == []
    assert loaded.config.gateway.port == 9000
    assert loaded.config.gateway.vad.threshold == 0.6
    assert loaded.config.gateway.vad.min_silence_s == 0.5
    assert loaded.config.gateway.host == "127.0.0.1"
    assert loaded.config.autostart is False


def test_wrong_type_keeps_default():
    write(json.dumps({"gateway": {"port": "9000", "threads": True}, "client": {"mute_other_apps": 1}}))
    loaded = config.load()
    assert loaded.config.gateway.port == 8765
    assert loaded.config.gateway.threads == 4
    assert loaded.config.client.mute_other_apps is True
    assert sorted(p.where for p in loaded.problems) == ["client.mute_other_apps", "gateway.port", "gateway.threads"]


def test_out_of_range_keeps_default():
    write(json.dumps({"gateway": {"port": 70000, "max_phrase_s": 90}, "client": {"button": {"vid": "xyz"}}}))
    loaded = config.load()
    assert loaded.config.gateway.port == 8765
    assert loaded.config.gateway.max_phrase_s == 25.0
    assert loaded.config.client.button.vid == "1B3F"
    assert len(loaded.problems) == 3


def test_only_a_loopback_host_is_accepted():
    write(json.dumps({"gateway": {"host": "0.0.0.0"}}))
    loaded = config.load()
    assert loaded.config.gateway.host == "127.0.0.1"
    assert [p.where for p in loaded.problems] == ["gateway.host"]


def test_integer_is_accepted_where_a_float_is_expected():
    write(json.dumps({"client": {"silence_autostop_s": 30}}))
    loaded = config.load()
    assert loaded.problems == []
    assert loaded.config.client.silence_autostop_s == 30.0
    assert isinstance(loaded.config.client.silence_autostop_s, float)


def test_null_disables_the_floor_check():
    write(json.dumps({"client": {"muted_floor_dbfs": None}}))
    loaded = config.load()
    assert loaded.problems == []
    assert loaded.config.client.muted_floor_dbfs is None


def test_null_is_refused_where_it_means_nothing():
    write(json.dumps({"gateway": {"port": None}}))
    loaded = config.load()
    assert loaded.config.gateway.port == 8765
    assert [p.where for p in loaded.problems] == ["gateway.port"]


def test_unknown_key_is_reported_and_ignored():
    write(json.dumps({"gateway": {"prot": 1}, "extra": {}}))
    loaded = config.load()
    assert sorted(p.where for p in loaded.problems) == ["extra", "gateway.prot"]
    assert loaded.config.gateway.port == 8765


def test_section_of_wrong_shape_keeps_defaults():
    write(json.dumps({"gateway": [1, 2], "client": "nope"}))
    loaded = config.load()
    assert sorted(p.where for p in loaded.problems) == ["client", "gateway"]
    assert loaded.config.gateway.port == 8765


def test_not_json_gives_defaults_and_one_problem():
    write('{"gateway": {"port": 9000,}}')
    loaded = config.load()
    assert loaded.config.gateway.port == 8765
    assert [p.where for p in loaded.problems] == ["<file>"]


def test_root_that_is_not_an_object():
    write("[]")
    loaded = config.load()
    assert [p.where for p in loaded.problems] == ["<root>"]


def test_bom_is_tolerated():
    write(json.dumps({"gateway": {"port": 9000}}), encoding="utf-8-sig")
    loaded = config.load()
    assert loaded.problems == []
    assert loaded.config.gateway.port == 9000


def test_save_then_load_round_trips():
    c = config.Config()
    c.client.microphone = "Микрофон (Usb Audio Device)"
    c.client.muted_floor_dbfs = None
    c.gateway.port = 9100
    config.save(c)
    loaded = config.load()
    assert loaded.problems == []
    assert loaded.config == c
    assert "Микрофон" in paths.config_file().read_text(encoding="utf-8")


def test_resolve_model_path(home, tmp_path):
    assert config.resolve_model_path("gigaam-v3/x.onnx") == home / "models" / "gigaam-v3" / "x.onnx"
    absolute = tmp_path / "elsewhere" / "x.onnx"
    assert config.resolve_model_path(str(absolute)) == absolute


def test_unparsed_means_the_file_could_not_be_read_as_an_object():
    path = paths.config_file()
    path.write_text("{", encoding="utf-8")
    assert config.unparsed(config.load(path))
    path.write_text("[1]", encoding="utf-8")
    assert config.unparsed(config.load(path))
    path.write_text('{"autostart": 3}', encoding="utf-8")
    assert not config.unparsed(config.load(path))
    path.unlink()
    assert not config.unparsed(config.load(path))


def test_problems_are_logged_once_per_change_of_the_file(caplog):
    import os

    path = paths.config_file()
    path.write_text('{"autostart": 3}', encoding="utf-8")
    with caplog.at_level("WARNING", logger="tolmach"):
        config.load_reported(path)
        config.load_reported(path)
        assert [r.getMessage() for r in caplog.records] == ["config: autostart: expected bool, default kept"]
        path.write_text('{"autostart": 4,}', encoding="utf-8")
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
        assert config.load_reported(path).problems
        config.load_reported(path)
        assert len(caplog.records) == 2 and "not JSON" in caplog.records[1].getMessage()


def test_text_is_typed_by_default_and_the_clipboard_way_can_be_chosen():
    assert config.load().config.client.insert_mode == "type"
    write(json.dumps({"client": {"insert_mode": "paste"}}))
    loaded = config.load()
    assert loaded.problems == []
    assert loaded.config.client.insert_mode == "paste"


def test_an_unknown_insert_mode_keeps_the_default():
    write(json.dumps({"client": {"insert_mode": "telepathy"}}))
    loaded = config.load()
    assert loaded.config.client.insert_mode == "type"
    assert [p.where for p in loaded.problems] == ["client.insert_mode"]


def test_the_overlay_sits_in_the_middle_unless_told_otherwise():
    assert config.load().config.client.overlay_position == "center"
    write(json.dumps({"client": {"overlay_position": "bottom-right"}}))
    loaded = config.load()
    assert loaded.problems == []
    assert loaded.config.client.overlay_position == "bottom-right"


def test_an_unknown_overlay_position_keeps_the_default():
    write(json.dumps({"client": {"overlay_position": "ceiling"}}))
    loaded = config.load()
    assert loaded.config.client.overlay_position == "center"
    assert [p.where for p in loaded.problems] == ["client.overlay_position"]


def test_the_config_knows_the_same_overlay_positions_as_the_overlay():
    from tolmach.client import overlay

    assert config.OVERLAY_POSITIONS == overlay.POSITIONS


def test_the_hotkey_for_inserting_the_last_text_has_a_default_and_can_be_switched_off():
    assert config.load().config.client.insert_last_hotkey == "shift+win+z"
    write(json.dumps({"client": {"insert_last_hotkey": ""}}))
    loaded = config.load()
    assert loaded.problems == [] and loaded.config.client.insert_last_hotkey == ""


def test_the_key_that_cancels_a_dictation_is_esc_and_can_be_changed_or_switched_off():
    assert config.load().config.client.cancel_hotkey == "esc"
    write(json.dumps({"client": {"cancel_hotkey": "ctrl+esc"}}))
    assert config.load().config.client.cancel_hotkey == "ctrl+esc"
    write(json.dumps({"client": {"cancel_hotkey": ""}}))
    loaded = config.load()
    assert loaded.problems == [] and loaded.config.client.cancel_hotkey == ""


def test_what_starts_a_dictation_is_decided_by_the_microphone_unless_chosen():
    assert config.load().config.client.control == "auto"
    for choice in ("button", "hotkey", "auto"):
        write(json.dumps({"client": {"control": choice}}))
        loaded = config.load()
        assert loaded.problems == [] and loaded.config.client.control == choice
    write(json.dumps({"client": {"control": "voice"}}))
    loaded = config.load()
    assert loaded.config.client.control == "auto"
    assert [p.where for p in loaded.problems] == ["client.control"]


ENGLISH = ("parakeet-unified-en/encoder.int8.onnx", "parakeet-unified-en/decoder.int8.onnx",
           "parakeet-unified-en/joiner.int8.onnx", "parakeet-unified-en/tokens.txt")
SECOND = {"name": "second", "language": "de", "encoder": "my/e.onnx", "decoder": "my/d.onnx", "joiner": "my/j.onnx",
          "tokens": "my/t.txt"}


def test_beside_the_main_model_there_is_an_english_one_by_default():
    gateway = config.load().config.gateway
    assert gateway.model.language == "ru" and gateway.extra_idle_minutes == 10.0
    english, = gateway.extra_models
    assert (english.name, english.language, english.type) == ("parakeet-unified-en", "en", "nemo_transducer")
    assert (english.encoder, english.decoder, english.joiner, english.tokens) == ENGLISH


def test_the_models_beside_the_main_one_are_those_the_file_lists():
    write(json.dumps({"gateway": {"extra_idle_minutes": 0, "extra_models": [SECOND]}}))
    loaded = config.load()
    assert loaded.problems == [] and loaded.config.gateway.extra_idle_minutes == 0.0
    mine, = loaded.config.gateway.extra_models
    assert (mine.name, mine.language, mine.type, mine.encoder, mine.tokens) == ("second", "de", "nemo_transducer", "my/e.onnx", "my/t.txt")
    write(json.dumps({"gateway": {"extra_models": []}}))
    assert config.load().config.gateway.extra_models == []              # none at all, if the file says so
    config.save(loaded.config)                                           # and what was read is written back whole
    assert [m.name for m in config.load().config.gateway.extra_models] == ["second"]


def test_a_model_that_is_not_described_whole_is_left_out_and_the_reason_is_said():
    write(json.dumps({"gateway": {"extra_models": [
        SECOND, {**SECOND, "name": ""}, {**SECOND, "name": "gigaam-v3"}, dict(SECOND), {**SECOND, "name": "third", "tokens": ""},
        {**SECOND, "name": "fourth", "encoder": 5}, "fifth", {**SECOND, "name": "sixth", "colour": "red"}]}}))
    loaded = config.load()
    assert [m.name for m in loaded.config.gateway.extra_models] == ["second", "sixth"]
    assert [(p.where, p.message) for p in loaded.problems] == [
        ("gateway.extra_models[1].name", "a model needs a name, left out"),
        ("gateway.extra_models[2].name", "this is the name of the main model, left out"),
        ("gateway.extra_models[3].name", "this name is used already, left out"),
        ("gateway.extra_models[4].tokens", "the model needs this file, left out"),
        ("gateway.extra_models[5].encoder", "expected str, default kept"),
        ("gateway.extra_models[5].encoder", "the model needs this file, left out"),
        ("gateway.extra_models[6]", "expected an object, left out"),
        ("gateway.extra_models[7].colour", "unknown key, ignored")]


def test_what_is_not_a_list_of_models_keeps_the_english_one():
    write(json.dumps({"gateway": {"extra_models": {"name": "second"}, "extra_idle_minutes": -1}}))
    loaded = config.load()
    assert [m.name for m in loaded.config.gateway.extra_models] == ["parakeet-unified-en"]
    assert loaded.config.gateway.extra_idle_minutes == 10.0
    assert [(p.where, p.message) for p in loaded.problems] == [
        ("gateway.extra_models", "expected a list, default kept"), ("gateway.extra_idle_minutes", "must be >= 0, default kept")]
