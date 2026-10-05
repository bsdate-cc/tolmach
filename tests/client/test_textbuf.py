from tolmach.client.textbuf import TextBuffer, tail


def test_display_joins_phrases_and_draft():
    buf = TextBuffer()
    buf.add_phrase("Привет.")
    buf.set_draft("как дела")
    assert buf.display() == "Привет. как дела"


def test_phrase_clears_draft_and_final_excludes_draft():
    buf = TextBuffer()
    buf.set_draft("прив")
    buf.add_phrase(" Привет. ")
    buf.set_draft("как")
    assert buf.final() == "Привет."


def test_blank_phrase_is_ignored():
    buf = TextBuffer()
    buf.add_phrase("   ")
    assert buf.final() == ""
    assert buf.display() == ""


def test_tail_keeps_short_text():
    assert tail("один два", 300) == "один два"


def test_tail_cuts_at_a_word_boundary():
    assert tail("один два три четыре", 10) == "…три четыре"
    assert tail("один два три четыре", 8) == "…четыре"


def test_tail_of_a_single_long_word_cuts_by_characters():
    assert tail("абвгдежзик", 4) == "…жзик"


def test_phrase_after_an_unfinished_one_continues_the_sentence():
    # A long stretch without pauses is cut by force, and the recogniser starts every piece with a capital.
    buf = TextBuffer()
    buf.add_phrase("К ужину пришли")
    buf.add_phrase("Соседи принесли пирог.")
    assert buf.final() == "К ужину пришли соседи принесли пирог."


def test_phrase_after_a_finished_sentence_keeps_its_capital():
    for end in (".", "!", "?", "…", ".»", "?)"):
        buf = TextBuffer()
        buf.add_phrase("Привет" + end)
        buf.add_phrase("Как дела")
        assert buf.final() == f"Привет{end} Как дела"


def test_an_abbreviation_at_the_seam_keeps_its_capitals():
    buf = TextBuffer()
    buf.add_phrase("Я подключил,")
    buf.add_phrase("USB микрофон.")
    assert buf.final() == "Я подключил, USB микрофон."


def test_a_one_letter_word_at_the_seam_is_lowered_too():
    buf = TextBuffer()
    buf.add_phrase("И тогда")
    buf.add_phrase("Я пошёл домой.")
    assert buf.final() == "И тогда я пошёл домой."


def test_draft_after_an_unfinished_phrase_is_shown_as_its_continuation():
    buf = TextBuffer()
    buf.add_phrase("К ужину пришли")
    buf.set_draft("Соседи принес")
    assert buf.display() == "К ужину пришли соседи принес"


def test_a_word_in_latin_letters_at_the_seam_keeps_its_capital():
    # A name or a term is written its own way wherever the cut fell.
    for word in ("GitHub", "Windows", "Docker"):
        buf = TextBuffer()
        buf.add_phrase("Я выложил это на")
        buf.add_phrase(f"{word} вчера вечером.")
        assert buf.final() == f"Я выложил это на {word} вчера вечером."
