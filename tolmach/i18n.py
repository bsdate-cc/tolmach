"""What the user reads in the tray menu, the dialogs and the overlay - in Russian or English.

Russian is the source language: the code says `_("Слушаю…")`, and the English text is
looked up here. A text without a translation is shown as it is - a forgotten entry is
a Russian line in an English menu, never a crash; a test checks that there is none.

Not translated on purpose: the launcher window (always English) and the logs (always
English). Standard library only: the launcher imports modules that import this one.
"""
from __future__ import annotations

import ctypes

LANGUAGES = ("auto", "ru", "en")
LANG_RUSSIAN = 0x19  # the primary language identifier of Windows for Russian

EN = {
    "Толмач": "Tolmach",
    # the overlay
    "Слушаю…": "Listening…",
    "Вставляю…": "Typing…",
    "Шлюз не отвечает": "The gateway is not answering",
    "Связь потеряна, запись сохранена": "Connection lost, the recording was saved",
    "Микрофон не найден": "Microphone not found",
    "Микрофон выключен — нажмите кнопку ещё раз": "The microphone is off — press the button again",
    "Ничего не распознано": "Nothing was recognised",
    "Не удалось вставить": "Could not insert the text",
    "Вставлять пока нечего": "Nothing to insert yet",
    "Отменено": "Cancelled",
    "Диктовкой управляет кнопка микрофона": "The microphone button starts and stops dictation",
    # the menu
    "Начать диктовку": "Start dictation",
    "Остановить диктовку": "Stop dictation",
    "Скопировать последний текст": "Copy the last text",
    "Вставить последний текст": "Insert the last text",
    "Микрофон": "Microphone",
    "По умолчанию": "Default",
    "Управление диктовкой": "Dictation control",
    "Авто: кнопка, если она есть у микрофона": "Auto: the button if the microphone has one",
    "Кнопка микрофона": "Microphone button",
    "Горячая клавиша": "Hotkey",
    "Положение окошка": "Overlay position",
    "Язык / Language": "Язык / Language",
    "Авто / Auto": "Авто / Auto",
    "Русский": "Русский",
    "Сверху слева": "Top left",
    "Сверху": "Top",
    "Сверху справа": "Top right",
    "Слева": "Left",
    "Посередине": "Centre",
    "Справа": "Right",
    "Снизу слева": "Bottom left",
    "Снизу": "Bottom",
    "Снизу справа": "Bottom right",
    "Глушить другие программы при записи": "Mute other programs while recording",
    "Автозапуск": "Start with Windows",
    "Открыть файл настроек": "Open the settings file",
    "Открыть словарь терминов": "Open the terms dictionary",
    "Кнопка: найдена": "Button: found",
    "Кнопка: не найдена": "Button: not found",
    "Настройки: ошибка в config.json — см. журнал": "Settings: an error in config.json — see the log",
    "Запустить шлюз": "Start the gateway",
    "Перезапустить шлюз": "Restart the gateway",
    "Остановить шлюз": "Stop the gateway",
    "Открыть журналы": "Open the logs",
    "Выход…": "Exit…",
    "Шлюз: не отвечает": "Gateway: not answering",
    "Шлюз: работает ({model})": "Gateway: running ({model})",
    "Шлюз: запускается": "Gateway: starting",
    "Шлюз: ошибка: {error}": "Gateway: error: {error}",
    # updates
    "Проверить обновления": "Check for updates",
    "Обновления: {note}": "Updates: {note}",
    "Нет связи с origin — проверено в {time}": "No answer from origin — checked at {time}",
    "Обновлений нет — проверено в {time}": "No updates — checked at {time}",
    "Обновить до {version}…": "Update to {version}…",
    "Обновить…": "Update…",
    "Перезапустить: на диске версия {version}": "Restart: version {version} is on disk",
    "Поставить библиотеки: requirements.txt изменился": "Install the libraries: requirements.txt has changed",
    "ветка {branch} — обновления выключены": "branch {branch} — updates are off",
    "без ветки — обновления выключены": "no branch — updates are off",
    "есть свои коммиты — обновления выключены": "local commits — updates are off",
    "история разошлась с origin — обновления выключены": "history diverged from origin — updates are off",
    "git не отвечает — обновление не выполнено": "git does not answer — the update was not taken",
    "в папке программы есть изменённые файлы — обновление не выполнено":
        "there are modified files in the program folder — the update was not taken",
    "нет связи с origin — обновление не выполнено": "no answer from origin — the update was not taken",
    "обновлять нечего: установлена последняя версия": "nothing to update: the latest version is installed",
    "на origin уже другое обновление (версия {version}) — нажмите «Обновить» ещё раз":
        "origin has another update by now (version {version}) — click Update again",
    "git merge не выполнен: {reason}": "git merge failed: {reason}",
    "неизвестная ошибка": "unknown error",
    # dialogs
    "Обновить Толмач до версии {version} и перезапустить?\n\n"
    "Трей и шлюз перезапустятся; идущая диктовка будет прервана.":
        "Update Tolmach to version {version} and restart?\n\n"
        "The tray and the gateway will restart; a dictation in progress will be cut off.",
    "Шлюз не остановился — перезапуск отменён.\n\n"
    "Остановите его из меню («Остановить шлюз») и повторите, или запустите tolmach.cmd.":
        "The gateway did not stop — the restart was cancelled.\n\n"
        "Stop it from the menu (Stop the gateway) and try again, or run tolmach.cmd.",
    "Не удалось запустить новый трей — перезапуск отменён, работает прежний.\n\n"
    "Причина — в журнале (tray.log). Перезапустить можно через tolmach.cmd.":
        "The new tray could not be started — the restart was cancelled, the old one keeps running.\n\n"
        "The reason is in the log (tray.log). You can restart with tolmach.cmd.",
    "Остановить шлюз тоже?\n\n"
    "Да — остановить шлюз и закрыть значок.\n"
    "Нет — закрыть только значок, шлюз продолжит работать.":
        "Stop the gateway too?\n\n"
        "Yes — stop the gateway and close the icon.\n"
        "No — close only the icon; the gateway keeps running.",
}

_language = "ru"


def pick_language(ui: int, regional: int) -> str:
    """'ru' when Windows itself or its regional format is Russian, else 'en'. The regional
    format counts because a Russian speaker on an English Windows is common among the
    people who run a Russian dictation tool."""
    return "ru" if LANG_RUSSIAN in (ui & 0x3FF, regional & 0x3FF) else "en"


def system_language() -> str:
    try:
        kernel32 = ctypes.windll.kernel32
        return pick_language(kernel32.GetUserDefaultUILanguage(), kernel32.GetUserDefaultLCID())
    except Exception:
        return "en"


def set_language(setting: str) -> None:
    """'ru' or 'en'; anything else ('auto') follows Windows (see pick_language)."""
    global _language
    _language = setting if setting in ("ru", "en") else system_language()


def language() -> str:
    return _language


def _(text: str) -> str:
    """The text in the current language."""
    return EN.get(text, text) if _language == "en" else text


def english(message) -> str:
    """A message in English whatever the language is - for the log, which is always English."""
    if isinstance(message, Text):
        return EN.get(message.template, message.template).format(**message.values)
    return EN.get(str(message), str(message))


def N_(text: str) -> str:
    """Marks a text for translation where it is defined; it is translated where it is shown."""
    return text


class Text:
    """A message with values in it, put into words when it is shown - so it follows the
    language of that moment, not of the moment it was made."""

    def __init__(self, template: str, **values):
        self.template = template
        self.values = values

    def __str__(self) -> str:
        return _(self.template).format(**self.values)

    def __repr__(self) -> str:
        return f"Text({self.template!r}, {self.values!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Text) and (self.template, self.values) == (other.template, other.values)

    def __hash__(self) -> int:
        return hash((self.template, tuple(sorted(self.values.items()))))
