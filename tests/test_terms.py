"""The terms dictionary: what sounds like a term is written as the term - and nothing else is touched."""
import logging
import re
import time
from pathlib import Path

import pytest

from tolmach import terms
from tolmach.terms import Dictionary, Table, Term, correct, key, parse

ROOT = Path(__file__).resolve().parents[1]


def table(*lines: str) -> Table:
    found, bad = parse("\n".join(lines))
    assert bad == []
    return Table(found)


# --- how a word sounds


def test_a_consonant_keeps_its_voice():
    assert key("бетон") != key("питон")
    assert key("ритме") != key("ридми")
    assert key("год") != key("код")


def test_speech_takes_the_voice_away_at_the_end_of_a_word_and_before_a_voiceless_sound():
    assert key("гид") == key("гит") == "git"
    assert key("код") == key("кот")
    assert key("гидхаб") == key("гитхаб")


def test_the_back_vowels_are_one_sound_and_the_front_ones_are_two():
    assert key("хаб") == key("хуб") == key("хоб")
    assert key("гит") == key("гыт")
    assert key("гет") != key("гит")
    assert key("хаб") != key("хиб")


def test_latin_is_read_the_russian_way():
    assert key("GitHub") == key("гитхаб")
    assert key("worktree") == key("ворктри")
    assert key("Python") == key("питон")
    assert key("Whisper") == key("виспер")
    assert key("Docker") == key("докер")


def test_a_consonant_said_twice_is_one_sound_and_a_vowel_is_always_a_syllable():
    assert key("паттон") == key("патон")
    assert key("Гит Таб") == key("гитаб")            # across the words of a run too
    assert key("ворктрии") != key("ворктри")
    assert key("пойти есть") != key("пойтесть")


def test_a_small_number_sounds_like_its_word():
    assert key("Word 3") == key("ворд три")


def test_what_is_not_a_letter_has_no_sound():
    assert key("«GitHub»,") == key("GitHub")
    assert key("...") == ""


def test_a_sound_is_one_character_of_the_key():
    assert len(key("цех")) == 3
    assert len(key("щи")) == 2


# --- the dictionary file


def test_a_line_is_a_term_and_the_ways_it_is_said():
    found, bad = parse("GitHub\nmain = мэйн, мейн\n")
    assert bad == []
    assert found == [Term("GitHub", ("GitHub",)), Term("main", ("main", "мэйн", "мейн"))]


def test_comments_and_blank_lines_are_skipped():
    found, bad = parse("# a comment\n\n   \nDocker   # the container thing\n")
    assert bad == [] and found == [Term("Docker", ("Docker",))]


def test_a_line_that_names_nothing_is_reported_by_its_number():
    found, bad = parse("GitHub\n= гитхаб\n+++\nDocker\n")
    assert [term.written for term in found] == ["GitHub", "Docker"]
    assert bad == [2, 3]


def test_the_same_way_of_saying_is_kept_once():
    found, _ = parse("git = гит, гит, git\n")
    assert found == [Term("git", ("git", "гит"))]


def test_a_hash_inside_a_term_is_part_of_the_term():
    found, bad = parse("C# = си шарп   # the language\n#C# = закомментировано\n")
    assert bad == [] and found == [Term("C#", ("си шарп",))]


def test_a_way_shorter_than_three_sounds_is_not_a_way():
    # "C" is one sound and "Go" two: half the language sounds like them.
    found, bad = parse("C# = си шарп\nGo\nGo = голанг\n")
    assert found == [Term("C#", ("си шарп",)), Term("Go", ("голанг",))]
    assert bad == [2]


# --- matching: the rules


DEV = ("GitHub", "worktree = ворктри", ".gitignore = гит игнор", "localhost = локалхост", "Docker",
       "Python = питон", "git", "pip", "JSON = джейсон", "GigaAM = гигаам", "Whisper = виспер")


def fixed(text: str, *lines: str) -> str:
    return correct(text, table(*(lines or DEV)))


def test_what_sounds_exactly_like_a_term_becomes_the_term():
    assert fixed("выложил на гитхаб вчера") == "выложил на GitHub вчера"
    assert fixed("создал ворктри") == "создал worktree"
    assert fixed("Установи Докер и питон") == "Установи Docker и Python"


def test_punctuation_around_the_term_stays():
    assert fixed("Это «Githab», да.") == "Это «GitHub», да."
    assert fixed("Слушает Local Host.") == "Слушает localhost."


def test_a_term_does_not_reach_across_a_comma_or_a_full_stop():
    assert fixed("там Git, Ignor не нужен") == "там git, Ignor не нужен"
    assert fixed("Это Git. Ignor закрыт.") == "Это git. Ignor закрыт."


def test_a_case_ending_goes_with_the_replaced_word():
    assert fixed("лежит в гитхабе давно") == "лежит в GitHub давно"
    assert fixed("собрал докером образ") == "собрал Docker образ"
    assert fixed("на локалхосте") == "на localhost"
    assert fixed("раньше запускал Whispery") == "раньше запускал Whisper"


def test_an_ending_is_taken_off_a_long_word_only():
    assert fixed("запустил Whisper, а потом ушёл") == "запустил Whisper, а потом ушёл"
    assert fixed("обнови питон и поставь пакеты") == "обнови Python и поставь пакеты"
    assert fixed("данные идут через Pipe дальше") == "данные идут через Pipe дальше"     # not pip with an ending
    assert fixed("подал питание на плату") == "подал питание на плату"                   # not "питон" with one


# words in Russian letters


def test_russian_letters_must_sound_exactly_like_the_term():
    assert fixed("запушил ветку в гетхаб") == "запушил ветку в гетхаб"
    assert fixed("сервер на Лакафосте работает") == "сервер на Лакафосте работает"
    assert fixed("запускал Вистер раньше") == "запускал Вистер раньше"
    assert fixed("новый дизайн готов") == "новый дизайн готов"                     # not JSON
    assert fixed("этот гигант мысли") == "этот гигант мысли"                       # not GigaAM
    assert fixed("залили бетон под столбы") == "залили бетон под столбы"           # not Python


def test_a_capital_says_nothing():
    # a name in the middle of a sentence is as ordinary as any other word
    assert fixed("летом поедем в Японию", "Ubuntu = убунту") == "летом поедем в Японию"
    assert fixed("директором будет Том Джексон") == "директором будет Том Джексон"
    assert fixed("Токен лежит в файле") == "Токен лежит в файле"


def test_russian_words_are_taken_word_for_word():
    # two ordinary words in a row may sound like a term that is said in one
    assert fixed("пить он не будет") == "пить он не будет"
    assert fixed("открыл Гит Хаб и смотрю") == "открыл Гит Хаб и смотрю"
    # ...so the pieces are a way of saying it only when the dictionary says so
    assert fixed("открыл Гит Хаб и смотрю", "GitHub = гит хаб") == "открыл GitHub и смотрю"
    # words run together are still the term
    assert fixed("сделал пулреквест", "pull request = пул реквест") == "сделал pull request"


def test_a_short_term_is_taken_only_from_latin_letters():
    assert fixed("наш гид устал") == "наш гид устал"                               # sounds exactly like git
    assert fixed("обновляется через GID сам") == "обновляется через git сам"
    assert fixed("поставь через PIP пакет") == "поставь через pip пакет"
    assert fixed("птенец сказал пип") == "птенец сказал пип"


# words in Latin letters


def test_a_near_miss_counts_where_the_recogniser_wrote_latin_letters():
    assert fixed("запушил ветку в GetHub вчера") == "запушил ветку в GitHub вчера"
    assert fixed("гарнитура Planctronix лежит", "Plantronics") == "гарнитура Plantronics лежит"


def test_a_term_of_five_sounds_gets_no_near_miss():
    assert fixed("запусти Doker контейнер") == "запусти Docker контейнер"          # the same sounds
    assert fixed("запусти Docter контейнер") == "запусти Docter контейнер"         # one more


def test_a_short_word_that_only_looks_like_the_term_is_another_word():
    assert fixed("отправь GET запрос") == "отправь GET запрос"                     # not git
    assert fixed("оформил по PEP как положено") == "оформил по PEP как положено"   # not pip


def test_a_term_written_in_pieces_is_one_term():
    assert fixed("добавил в Git Ignor папку") == "добавил в .gitignore папку"
    assert fixed("старый Speech Kit помню", "SpeechKit") == "старый SpeechKit помню"


def test_a_term_is_in_one_piece_more_than_its_words_at_most():
    assert fixed("старый Spee Ch Kit помню", *DEV, "SpeechKit") == "старый Spee Ch Kit помню"


def test_a_small_word_beside_the_term_is_not_swallowed():
    # "в" and the first sound of "Windows" are one sound: the run "в Windows" sounds like the term too
    assert fixed("при входе в Windows", "Windows") == "при входе в Windows"
    assert fixed("лежит в ворктри") == "лежит в worktree"
    # "и AML" sounds exactly like YAML
    assert fixed("проверка и AML обязательны", "YAML") == "проверка и AML обязательны"


def test_a_word_without_a_sound_is_not_a_piece_of_a_term():
    assert fixed("создай Word 3 рядом") == "создай worktree рядом"                 # a small number is a word
    assert fixed("там Git 2024 Ignor лежит") == "там git 2024 Ignor лежит"
    assert fixed("запусти python3 отдельно") == "запусти python3 отдельно"         # a digit inside: a name of its own


# what wins


def test_the_longer_term_wins_over_the_shorter_inside_it():
    assert fixed("в Git Ignor лежит") == "в .gitignore лежит"                      # not "git Ignor"
    compose = ("Docker", "Docker Compose = докер компоуз")
    assert fixed("запусти докер компоуз", *compose) == "запусти Docker Compose"


def test_the_closer_term_wins_over_the_longer_one():
    # "Docker Tab" is one sound away from "Docker Hub", and "Docker" is the term exactly
    assert fixed("запусти Docker Tab сейчас", "Docker", "Docker Hub") == "запусти Docker Tab сейчас"


def test_of_two_terms_that_sound_the_same_the_earlier_line_wins():
    assert fixed("выложил на гитхаб", "GitHub", "GitHab") == "выложил на GitHub"
    assert fixed("выложил на гитхаб", "GitHab", "GitHub") == "выложил на GitHab"


def test_a_term_glued_to_other_text_is_left_alone():
    # one word for the recogniser is one word here: no guessing where a term ends inside it
    assert fixed("через GID-репозиторий сам") == "через GID-репозиторий сам"
    assert fixed("ссылка https://githab.example/page там") == "ссылка https://githab.example/page там"


def test_a_big_dictionary_and_a_long_phrase_take_a_moment_not_seconds():
    lines = [f"Term{i:03d} = {a}{b}{c}ер" for i, (a, b, c) in enumerate(
        (a, b, c) for a in ("ква", "бро", "сти", "мну", "фле", "гри") for b in ("зор", "мит", "нек", "дуп", "валь")
        for c in ("ка", "то", "ли", "ну", "ра", "бе", "со", "ми", "ду", "фа"))]
    big = table(*lines)
    assert len(lines) == 300
    phrase = " ".join(["Мы обсуждали план на завтра, потом Githab и ещё много разных слов подряд"] * 5)
    started = time.perf_counter()
    assert correct(phrase, big) == phrase
    assert time.perf_counter() - started < 1.0


def test_a_term_already_written_right_stays_as_it_is():
    text = "В GitHub лежит worktree, рядом .gitignore и Docker."
    assert fixed(text) == text


def test_correcting_twice_changes_nothing_more():
    once = fixed("выложил на Githab и в Git Hub, потом в гитхабе")
    assert once == "выложил на GitHub и в GitHub, потом в GitHub"
    assert fixed(once) == once


def test_no_terms_no_changes():
    assert correct("выложил на Githab", Table()) == "выложил на Githab"
    assert correct("", table(*DEV)) == ""


def test_the_spacing_of_an_untouched_text_is_kept():
    assert fixed("два  пробела   и\tтаб") == "два  пробела   и\tтаб"


# --- matching: what the recogniser really wrote (the misheard word in a neutral sentence)


OWN = ("Plantronics", "SpeechKit = спичкит", "hotkey = хоткей", "AWS", "Толмач = талмач", "event = ивент",
       "docs = докс", "PowerPoint", "Visual Studio Code = вижуал студио код")

MISHEARD = [
    ("Выложил проект на Githab.", "GitHub"),
    ("Выложен через Githab давно.", "GitHub"),
    ("Запушил ветку в GetHub.", "GitHub"),
    ("Работаю в другом ворктрии.", "worktree"),
    ("Создай новую Word 3 рядом.", "worktree"),
    ("Добавил папку в Git Ignor.", ".gitignore"),
    ("Раньше запускал Whispery.", "Whisper"),
    ("Сервер слушает только Local Host.", "localhost"),
    ("Обновляется через GID сам.", "git"),
    ("Хорошая гарнитура Planctronix.", "Plantronics"),
    ("Первая версия называлась Speech Hit.", "SpeechKit"),
    ("Вставляю через Hotkay.", "hotkey"),
    ("Развернул это в AVS.", "AWS"),
    ("Назвали программу Talmach.", "Толмач"),
    ("Отправляет ivent в систему.", "event"),
    ("Это лежит в папке Docks.", "docs"),
    ("Показал слайды в Power Point.", "PowerPoint"),
    ("Открой проект Visul Studio Cote.", "Visual Studio Code"),
    ("Обнови Pithon до новой версии.", "Python"),
]


@pytest.mark.parametrize("heard, wanted", MISHEARD)
def test_a_misheard_term_is_written_right(heard, wanted):
    assert wanted in fixed(heard, *DEV, *OWN)


# --- matching: ordinary text is left alone


TRAPS = [
    "Гид провёл нас по старому городу.",
    "Новый дизайн сайта готов, макеты лежат в папке.",
    "Данные идут по локальной сети без задержек.",
    "Токен лежит рядом с конфигом.",
    "Этот гигант мысли опять опоздал.",
    "Он дал мне ключ и ушёл в главный офис.",
    "Надо выспаться, а потом толкать машину до гаража.",
    "Привет, как дела, что нового в гараже?",
    "Мы обсуждали план на завтра, потом пошли обедать.",
    "Птенец сказал пип и замолчал.",
    "Завтра утром поеду менять масло и фильтр.",
    "Вижу, что студия закрыта, код двери не знаю.",
    # everyday words that sound close to a term
    "Залили бетон под столбы забора, бетона хватило.",
    "Работаем в таком ритме уже месяц, эти ритмы выматывают.",
    "Не клади это на стол. Гляди, какая погода!",
    "Летом поедем в Японию, Япония красива весной.",
    "Новым директором компании будет Том Джексон.",
    "Здесь холодно весь год, и весь код лежит в одной папке.",
    "Он выглядел так, будто он ничего не ел.",
    "Все дети имеют право на питание, а блок питания сгорел.",
    "Зачем Вы сюда пришли? Что-то Вы нас совсем забыли.",
    "Не бросайте мусор в подъезде! Пора пойти есть.",
    "Купи редис и зелень на рынке, привези бидон молока.",
    "Купил жене Редми, а себе взял пылесос Дайсон.",
    "Яблоки были поданы на десерт, такую сумму я не потяну.",
    "Отправь GET запрос, потом проверь AML и PEP.",
]


@pytest.mark.parametrize("text", TRAPS)
def test_a_sentence_without_terms_is_not_touched(text):
    assert fixed(text, *DEV, *OWN) == text


# --- the starter dictionary


def starter() -> Table:
    found, bad = parse(terms.STARTER.read_text(encoding="utf-8"))
    assert bad == []
    return Table(found)


def test_the_starter_dictionary_is_made_of_terms_that_can_be_heard():
    found, bad = parse(terms.STARTER.read_text(encoding="utf-8"))
    assert bad == [] and len(found) >= 25


@pytest.mark.parametrize("text", TRAPS)
def test_the_starter_dictionary_leaves_an_ordinary_sentence_alone(text):
    assert correct(text, starter()) == text


@pytest.mark.parametrize("heard, wanted", [
    ("Выложил проект на Githab.", "Выложил проект на GitHub."),
    ("Открыл Гит Хаб и смотрю.", "Открыл GitHub и смотрю."),
    ("Лежит в гитхабе давно.", "Лежит в GitHub давно."),
    ("Поставь Линукс и питон.", "Поставь Linux и Python."),
    ("Открой это в ВЭ ЭС код.", "Открой это в VS Code."),
])
def test_the_starter_dictionary_writes_its_terms(heard, wanted):
    assert correct(heard, starter()) == wanted


def prose() -> list[str]:
    """Every sentence of the Russian texts in this repository, code spans left out."""
    sentences = []
    for name in ("README.ru.md", "CHANGELOG.md"):
        text = re.sub(r"```.*?```", " ", (ROOT / name).read_text(encoding="utf-8"), flags=re.S)
        text = re.sub(r"`[^`]*`", " ", text)
        for line in re.split(r"(?<=[.!?])\s+|\n+", text):
            line = line.strip(" -*#|>")
            if len(re.findall(r"[А-Яа-яЁё]{2,}", line)) >= 3:
                sentences.append(line)
    return sentences


def test_the_starter_dictionary_changes_nothing_in_ordinary_prose():
    sentences = prose()
    assert len(sentences) > 150
    ready = starter()
    changed = [(s, correct(s, ready)) for s in sentences if correct(s, ready) != s]
    assert changed == []


# --- the file on disk


def test_the_file_is_created_with_the_starter_set_only_when_there_is_none(tmp_path):
    path = tmp_path / "terms.txt"
    terms.ensure_file(path)
    assert "GitHub" in path.read_text(encoding="utf-8")
    path.write_text("Docker\n", encoding="utf-8")
    terms.ensure_file(path)
    assert path.read_text(encoding="utf-8") == "Docker\n"


def test_no_file_means_no_corrections(tmp_path):
    assert Dictionary(tmp_path / "terms.txt").apply("выложил на Githab") == "выложил на Githab"


def test_the_file_is_read_again_when_it_changes(tmp_path):
    path = tmp_path / "terms.txt"
    dictionary = Dictionary(path)
    assert dictionary.apply("на Githab") == "на Githab"
    path.write_text("GitHub\n", encoding="utf-8")
    assert dictionary.apply("на Githab") == "на GitHub"
    path.write_text("# GitHub\nDocker = докер\n", encoding="utf-8")
    assert dictionary.apply("на Githab в докере") == "на Githab в Docker"
    path.unlink()
    assert dictionary.apply("на Githab в докере") == "на Githab в докере"


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text("GitHub\n", encoding="utf-8-sig")
    assert Dictionary(path).apply("на Githab") == "на GitHub"


def test_a_line_that_is_not_understood_goes_to_the_log_by_number_and_the_rest_works(tmp_path, caplog):
    path = tmp_path / "terms.txt"
    path.write_text("GitHub\n= потерялось\n", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="tolmach"):
        assert Dictionary(path).apply("на Githab") == "на GitHub"
    messages = [record.getMessage() for record in caplog.records]
    assert any("1 loaded" in m for m in messages)
    assert any("line 2" in m for m in messages)


def test_a_file_that_cannot_be_read_keeps_the_terms_read_before(tmp_path, caplog):
    path = tmp_path / "terms.txt"
    path.write_text("GitHub\n", encoding="utf-8")
    dictionary = Dictionary(path)
    assert dictionary.apply("на Githab") == "на GitHub"
    path.write_bytes(b"\xff\xfe\xff not text")
    with caplog.at_level(logging.WARNING, logger="tolmach"):
        assert dictionary.apply("на Githab") == "на GitHub"
    assert any("cannot be read" in record.getMessage() for record in caplog.records)


def held_by_another_program(monkeypatch, path):
    """Make reading `path` fail the way a file held by an editor or an antivirus does; returns
    the list of attempts and the switch that lets the reading through again."""
    attempts, held = [], [True]
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == path:
            attempts.append(1)
            if held[0]:
                raise PermissionError("held by another program")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    return attempts, held


def test_a_file_that_could_not_be_read_is_tried_again_without_being_saved_again(tmp_path, monkeypatch, caplog):
    path = tmp_path / "terms.txt"
    path.write_text("GitHub\n", encoding="utf-8")
    attempts, held = held_by_another_program(monkeypatch, path)
    monkeypatch.setattr(terms, "RETRY", 0.0)
    dictionary = Dictionary(path)
    with caplog.at_level(logging.WARNING, logger="tolmach"):
        assert dictionary.apply("на Githab") == "на Githab"
        assert dictionary.apply("на Githab") == "на Githab"
    assert [record.getMessage() for record in caplog.records] == ["terms: terms.txt cannot be read (PermissionError)"]
    held[0] = False
    assert dictionary.apply("на Githab") == "на GitHub"


def test_a_file_that_cannot_be_read_is_not_tried_for_every_phrase(tmp_path, monkeypatch):
    path = tmp_path / "terms.txt"
    path.write_text("GitHub\n", encoding="utf-8")
    attempts, held = held_by_another_program(monkeypatch, path)
    dictionary = Dictionary(path)
    for _ in range(5):
        assert dictionary.apply("на Githab") == "на Githab"
    assert len(attempts) == 1


def test_a_failure_inside_never_loses_the_text_and_never_logs_it(tmp_path, monkeypatch, caplog):
    path = tmp_path / "terms.txt"
    path.write_text("GitHub\n", encoding="utf-8")

    def boom(text, table):
        raise RuntimeError("broken")

    monkeypatch.setattr(terms, "correct", boom)
    with caplog.at_level(logging.DEBUG, logger="tolmach"):
        assert Dictionary(path).apply("секретная фраза на Githab") == "секретная фраза на Githab"
    assert "секретная" not in caplog.text
