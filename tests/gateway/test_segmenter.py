import numpy as np

from tolmach.gateway.segmenter import HARD_LIMIT_S, PREROLL_WINDOWS, SAMPLE_RATE, Draft, Final, Segmenter
from tests.gateway.fakes import WINDOW, ScriptedVad, tone


def feed_windows(seg, count, value=0.1):
    out = []
    for _ in range(count):
        out.extend(seg.feed(tone(WINDOW, value)))
    return out


def test_silence_yields_nothing_and_stays_bounded():
    seg = Segmenter(ScriptedVad())
    assert feed_windows(seg, 500) == []
    assert len(seg._buf) <= PREROLL_WINDOWS * WINDOW


def test_drafts_come_once_per_interval_of_audio_and_grow():
    vad = ScriptedVad(speech=range(5, 200))
    seg = Segmenter(vad, draft_interval_s=1.0)
    out = feed_windows(seg, 100)
    assert all(isinstance(a, Draft) and a.item == 1 for a in out)
    # speech starts at window 5; 16000 / 512 = 31.25, so a draft is due every 32 windows
    assert len(out) == 2
    assert len(out[0].samples) == 37 * WINDOW          # windows 1..37: pre-roll included
    assert len(out[1].samples) == 69 * WINDOW


def test_preroll_is_capped_before_speech():
    vad = ScriptedVad(speech=range(101, 400))
    seg = Segmenter(vad, draft_interval_s=1.0)
    out = feed_windows(seg, 133)
    assert len(out) == 1
    # 10 windows kept from before window 101, then 33 windows of speech
    assert len(out[0].samples) == (PREROLL_WINDOWS + 33) * WINDOW


def test_closed_segment_becomes_a_final_and_numbering_moves_on():
    first = tone(4000, 0.5)
    vad = ScriptedVad(speech=list(range(5, 60)) + list(range(100, 200)), close_after={80: first})
    seg = Segmenter(vad, draft_interval_s=1.0)
    out = feed_windows(seg, 140)
    kinds = [(type(a).__name__, a.item) for a in out]
    # drafts keep coming through the trailing silence (windows 60..79): the phrase
    # is open until the VAD closes it
    assert kinds == [("Draft", 1), ("Draft", 1), ("Final", 1), ("Draft", 2)]
    # the phrase is cut from our own buffer (windows 1..80), not taken from the VAD
    assert len(out[2].samples) == 80 * WINDOW
    # the second phrase's draft starts from its own pre-roll, not from the first phrase
    assert len(out[3].samples) == (PREROLL_WINDOWS + 33) * WINDOW


def test_no_drafts_between_phrases():
    vad = ScriptedVad(speech=range(5, 40), close_after={60: tone(100)})
    seg = Segmenter(vad, draft_interval_s=1.0)
    out = feed_windows(seg, 300)
    assert [type(a).__name__ for a in out] == ["Draft", "Final"]


def test_hard_limit_closes_a_phrase_that_never_pauses():
    vad = ScriptedVad(speech=range(1, 5000))
    seg = Segmenter(vad, draft_interval_s=1000.0)       # no drafts in this test
    limit_windows = int(HARD_LIMIT_S * SAMPLE_RATE) // WINDOW + 1
    out = feed_windows(seg, limit_windows + 5)
    finals = [a for a in out if isinstance(a, Final)]
    assert len(finals) == 1
    assert finals[0].item == 1
    assert HARD_LIMIT_S * SAMPLE_RATE <= len(finals[0].samples) < HARD_LIMIT_S * SAMPLE_RATE + WINDOW
    assert vad.resets == 1
    # the speech went on after the cut: the rest is the next phrase
    rest = seg.commit()
    assert [f.item for f in rest] == [2]
    assert len(rest[0].samples) == 5 * WINDOW


def test_commit_closes_the_open_phrase_with_its_unfed_tail():
    vad = ScriptedVad(speech=range(5, 100))
    vad.on_flush = [tone(3000, 0.3)]
    seg = Segmenter(vad)
    feed_windows(seg, 20)
    seg.feed(tone(100))                                  # less than a window: the VAD never saw it
    out = seg.commit()
    assert len(out) == 1 and isinstance(out[0], Final)
    assert out[0].item == 1
    assert len(out[0].samples) == 20 * WINDOW + 100
    assert seg.commit() == []                            # a second commit has nothing left


def test_commit_falls_back_to_the_vad_segment_when_speech_was_never_seen():
    short = tone(3000, 0.3)
    vad = ScriptedVad()                                  # is_speech() stays False
    vad.on_flush = [short]
    seg = Segmenter(vad)
    feed_windows(seg, 8)
    out = seg.commit()
    assert [f.item for f in out] == [1]
    np.testing.assert_array_equal(out[0].samples, short)


def test_commit_with_no_audio_at_all():
    assert Segmenter(ScriptedVad()).commit() == []


def test_item_numbers_continue_after_commit():
    vad = ScriptedVad(speech=range(1, 500))
    vad.on_flush = [tone(100)]
    seg = Segmenter(vad)
    feed_windows(seg, 10)
    assert [f.item for f in seg.commit()] == [1]
    vad.on_flush = [tone(100)]
    feed_windows(seg, 10)
    assert [f.item for f in seg.commit()] == [2]


def test_unaligned_chunks_lose_nothing():
    vad = ScriptedVad(speech=range(1, 10_000))
    seg = Segmenter(vad, draft_interval_s=1000.0)
    total = 0
    for _ in range(37):
        seg.feed(tone(1600))                             # 100 ms chunks, not a multiple of 512
        total += 1600
    assert vad.n == total // WINDOW
    assert len(seg._buf) == total                        # speech is on: everything is kept
    assert seg._fed == vad.n * WINDOW


def test_two_segments_in_one_feed():
    a, b = tone(1000, 0.2), tone(2000, 0.4)
    vad = ScriptedVad(speech=list(range(1, 20)) + list(range(40, 60)), close_after={30: a, 70: b})
    seg = Segmenter(vad, draft_interval_s=1000.0)
    out = seg.feed(tone(80 * WINDOW + 100))              # one big chunk, 5 seconds at once
    assert [(type(x).__name__, x.item) for x in out] == [("Final", 1), ("Final", 2)]
    assert len(out[0].samples) == 30 * WINDOW            # windows 1..30
    assert len(out[1].samples) == 40 * WINDOW            # windows 31..70
    assert len(seg._buf) <= PREROLL_WINDOWS * WINDOW + 100


def test_input_is_converted_to_float32():
    vad = ScriptedVad(speech=range(1, 100))
    seg = Segmenter(vad, draft_interval_s=0.5)
    out = seg.feed(np.full(20 * WINDOW, 0.1, dtype=np.float64))
    assert out and all(a.samples.dtype == np.float32 for a in out)


# --- where a phrase lies in the audio


def test_a_final_knows_where_it_lies_in_the_stream():
    closed = tone(4000, 0.5)
    vad = ScriptedVad(speech=list(range(5, 60)) + list(range(200, 260)), close_after={80: closed, 300: closed})
    seg = Segmenter(vad, draft_interval_s=1000.0)
    finals = [a for a in feed_windows(seg, 320) if isinstance(a, Final)]
    # the first phrase is windows 1..80; the second begins with its pre-roll, before window 200
    assert [(f.start, f.end) for f in finals] == [(0, 80 * WINDOW),
                                                  ((199 - PREROLL_WINDOWS) * WINDOW, 300 * WINDOW)]
    assert all(f.end - f.start == len(f.samples) for f in finals)


def test_the_pieces_of_a_phrase_cut_by_the_hard_limit_lie_end_to_end():
    vad = ScriptedVad(speech=range(1, 5000))
    seg = Segmenter(vad, draft_interval_s=1000.0)
    limit_windows = int(HARD_LIMIT_S * SAMPLE_RATE) // WINDOW + 1
    cut = [a for a in feed_windows(seg, limit_windows + 5) if isinstance(a, Final)][0]
    rest = seg.commit()[0]
    assert (cut.start, cut.end) == (0, limit_windows * WINDOW)
    assert (rest.start, rest.end) == (limit_windows * WINDOW, (limit_windows + 5) * WINDOW)


def test_the_tail_closed_by_commit_ends_where_the_audio_ends():
    vad = ScriptedVad(speech=range(5, 100))
    seg = Segmenter(vad)
    feed_windows(seg, 20)
    seg.feed(tone(100))                                  # less than a window: the VAD never saw it
    tail = seg.commit()[0]
    assert (tail.start, tail.end) == (0, 20 * WINDOW + 100)


def test_a_segment_only_the_vad_saw_is_placed_at_the_end_of_what_was_fed():
    short = tone(3000, 0.3)
    vad = ScriptedVad()                                  # is_speech() stays False
    vad.on_flush = [short]
    seg = Segmenter(vad)
    feed_windows(seg, 8)
    only = seg.commit()[0]
    assert (only.start, only.end) == (8 * WINDOW - 3000, 8 * WINDOW)


def test_positions_go_on_after_a_commit():
    vad = ScriptedVad(speech=range(1, 500))
    seg = Segmenter(vad)
    feed_windows(seg, 10)
    first = seg.commit()[0]
    feed_windows(seg, 10)
    second = seg.commit()[0]
    assert (first.start, first.end) == (0, 10 * WINDOW)
    assert (second.start, second.end) == (10 * WINDOW, 20 * WINDOW)


def test_a_segment_only_the_vad_saw_never_begins_before_the_phrase_before_it():
    # windows 1..30 are a phrase; then the detector closes a long segment of its own, speech never reported
    vad = ScriptedVad(speech=range(1, 20), close_after={30: tone(100), 50: tone(40 * WINDOW, 0.3)})
    seg = Segmenter(vad, draft_interval_s=1000.0)
    finals = [a for a in feed_windows(seg, 60) if isinstance(a, Final)]
    assert [(f.start, f.end) for f in finals] == [(0, 30 * WINDOW), (30 * WINDOW, 50 * WINDOW)]


def test_a_segment_longer_than_everything_fed_begins_at_the_beginning():
    vad = ScriptedVad()
    vad.on_flush = [tone(10 * WINDOW)]
    seg = Segmenter(vad)
    feed_windows(seg, 8)
    only = seg.commit()[0]
    assert (only.start, only.end) == (0, 8 * WINDOW)


def test_positions_go_on_after_a_commit_that_took_an_unfed_tail():
    vad = ScriptedVad(speech=range(1, 500))
    seg = Segmenter(vad)
    feed_windows(seg, 20)
    seg.feed(tone(100))                                  # less than a window: the VAD never saw it
    first = seg.commit()[0]
    feed_windows(seg, 10)
    second = seg.commit()[0]
    assert (first.start, first.end) == (0, 20 * WINDOW + 100)
    assert (second.start, second.end) == (20 * WINDOW + 100, 30 * WINDOW + 100)
