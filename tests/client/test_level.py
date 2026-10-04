import numpy as np

from tolmach.client.level import SILENCE_DBFS, FloorCheck, dbfs


def tone(amplitude: float, n: int = 1600) -> np.ndarray:
    t = np.arange(n)
    return (amplitude * 32767 * np.sin(2 * np.pi * 440 * t / 16000)).astype(np.int16)


QUIET = tone(0.00005)   # about -95 dBFS: below the -70 floor, above digital silence
LOUD = tone(0.05)       # about -29 dBFS


def test_dbfs_of_full_scale_sine_is_about_minus_3():
    assert -3.5 < dbfs(tone(1.0)) < -2.5


def test_dbfs_of_empty_and_zero_input_is_silence():
    assert dbfs(np.zeros(0, dtype=np.int16)) == SILENCE_DBFS
    assert dbfs(np.zeros(1600, dtype=np.int16)) == SILENCE_DBFS


def test_dbfs_ignores_dc_offset():
    assert dbfs(np.full(1600, 1000, dtype=np.int16)) == SILENCE_DBFS


def test_floor_check_reports_muted_after_half_a_second_of_quiet():
    check = FloorCheck(-70.0)
    assert [check.feed(QUIET) for _ in range(5)] == [None, None, None, None, True]


def test_floor_check_reports_live_if_any_chunk_is_loud():
    check = FloorCheck(-70.0)
    results = [check.feed(c) for c in (QUIET, LOUD, QUIET, QUIET, QUIET)]
    assert results == [None, None, None, None, False]


def test_floor_check_decides_only_once():
    check = FloorCheck(-70.0)
    for _ in range(5):
        check.feed(QUIET)
    assert check.feed(QUIET) is None
