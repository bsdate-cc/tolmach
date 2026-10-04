from tolmach import keyfile, paths


def test_read_key_is_none_until_one_exists():
    assert keyfile.read_key() is None


def test_ensure_key_creates_a_long_random_key():
    key = keyfile.ensure_key()
    assert len(key) >= 32
    assert paths.key_file().read_text(encoding="utf-8") == key


def test_ensure_key_is_stable():
    assert keyfile.ensure_key() == keyfile.ensure_key() == keyfile.read_key()


def test_empty_file_counts_as_no_key():
    paths.key_file().write_text("  \n", encoding="utf-8")
    assert keyfile.read_key() is None
    assert len(keyfile.ensure_key()) >= 32
