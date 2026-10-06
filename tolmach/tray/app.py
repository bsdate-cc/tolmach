"""pystray glue: the icon and the menu. Thin on purpose: decisions live in
gatewayctl and the client controller. Nothing here ever toasts or pops up on its own:
the icon changes and the menu acts."""
from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pystray

from tolmach import APP_NAME, VERSION, config, i18n, keyfile, paths, terms
from tolmach.client import overlay, paste
from tolmach.client.app import ClientApp
from tolmach.client.hotkey import label as hotkey_label
from tolmach.i18n import N_, _
from tolmach.tray import autostart, gatewayctl, icons, relaunch, updater

log = logging.getLogger("tolmach")

POLL_S = 5.0
DEVICES_EVERY = 6   # without device notifications: re-read the microphone list on every 6th poll (30 s)
UPDATE_EVERY = 4320  # polls between two looks at origin: 6 hours
MB_YESNO, MB_YESNOCANCEL, MB_ICONQUESTION, MB_ICONINFORMATION, MB_TOPMOST = 0x0004, 0x0003, 0x0020, 0x0040, 0x00040000
IDYES, IDNO = 6, 7

CONFIG_ERROR = N_("Настройки: ошибка в config.json — см. журнал")
POSITION_NAMES = {
    "top-left": N_("Сверху слева"), "top": N_("Сверху"), "top-right": N_("Сверху справа"),
    "left": N_("Слева"), "center": N_("Посередине"), "right": N_("Справа"),
    "bottom-left": N_("Снизу слева"), "bottom": N_("Снизу"), "bottom-right": N_("Снизу справа"),
}
CONTROL_NAMES = {"auto": N_("Авто: кнопка, если она есть у микрофона"), "button": N_("Кнопка микрофона"),
                 "hotkey": N_("Горячая клавиша")}
# Each language under its own name, whatever the current one is: the way back must be readable.
LANGUAGE_NAMES = {"auto": N_("Авто / Auto"), "ru": N_("Русский"), "en": "English"}
UPDATE_QUESTION = N_("Обновить Толмач до версии {version} и перезапустить?\n\n"
                     "Трей и шлюз перезапустятся; идущая диктовка будет прервана.")
GATEWAY_STUCK = N_("Шлюз не остановился — перезапуск отменён.\n\n"
                   "Остановите его из меню («Остановить шлюз») и повторите, или запустите tolmach.cmd.")
RESTART_FAILED = N_("Не удалось запустить новый трей — перезапуск отменён, работает прежний.\n\n"
                    "Причина — в журнале (tray.log). Перезапустить можно через tolmach.cmd.")
CHECK_UPDATES = N_("Проверить обновления")
EXIT_QUESTION = N_("Остановить шлюз тоже?\n\n"
                   "Да — остановить шлюз и закрыть значок.\n"
                   "Нет — закрыть только значок, шлюз продолжит работать.")


def _title() -> str:
    return f"{_(APP_NAME)} {VERSION}"


def _with_key(text: str, hotkey: str) -> str:
    """A menu row with its hotkey on the right, the way menus show a shortcut; none, no column."""
    return f"{text}\t{hotkey_label(hotkey)}" if hotkey else text


def _menu_action(fn):
    """pystray calls a menu handler inside the icon window's message handling, so a
    handler that blocks there (a dialog, a gateway stop) freezes the icon. Every
    handler therefore runs in its own thread."""

    def run(icon, item):
        try:
            fn(icon, item)
        except Exception:
            log.exception("tray menu action failed")

    def handler(icon, item):
        threading.Thread(target=run, args=(icon, item), daemon=True).start()

    return handler


class TrayApp:
    def __init__(self, root: Path):
        self._root = root
        self._health = None
        self._devices: list[str] = []
        self._shown = None
        self._menu_shown = None
        self._refresh_lock = threading.Lock()
        self._cfg = config.load().config
        i18n.set_language(self._cfg.language)
        self._config_bad = False
        self._ready = False
        self._started = False
        self._client_stopped = False
        self._stopping = threading.Event()
        self._update = updater.Update("off")   # until the first look at origin says there is one
        self._on_disk = VERSION      # the version of the code on disk; differs after a merge or a pull
        self._requirements = updater.requirements_digest(root)   # the file this tray started with
        self._stale = updater.libraries_stale(root)              # libraries older than requirements.txt
        self._checked: tuple[str, str] | None = None             # (time, result) of the last check by hand
        # Re-entrant: taking an update asks origin again from the same thread.
        self._update_lock = threading.RLock()
        self._client = ClientApp(on_state=lambda state: self.refresh())
        self._button_rules = False   # whether the microphone button starts a dictation now; kept by refresh()
        self.icon = pystray.Icon("Tolmach", icons.image(icons.DOWN), _title(), menu=self._menu())

    # -- menu ---------------------------------------------------------------

    def _menu(self) -> pystray.Menu:
        # Every text is a function: it is put into words when the menu is drawn, in the
        # language of that moment.
        item, menu = pystray.MenuItem, pystray.Menu
        return menu(
            item(lambda i: self._dictation_row(), _menu_action(self._toggle)),
            item(lambda i: _("Скопировать последний текст"), _menu_action(self._copy_last),
                 enabled=lambda i: bool(self._client.last_text)),
            item(lambda i: _with_key(_("Вставить последний текст"), self._client.hotkeys["insert_last"]),
                 _menu_action(self._insert_last),
                 enabled=lambda i: bool(self._client.last_text)),
            menu.SEPARATOR,
            item(lambda i: _("Микрофон"), menu(self._microphones)),
            # Nothing to choose without a button - unless the setting says "button": then the way back.
            item(lambda i: _("Управление диктовкой"), menu(self._controls),
                 visible=lambda i: self._client.button_found or self._cfg.client.control == "button"),
            item(lambda i: _("Положение окошка"), menu(self._positions)),
            item(lambda i: _("Язык / Language"), menu(self._languages)),
            item(lambda i: _("Глушить другие программы при записи"), _menu_action(self._toggle_mute),
                 checked=lambda i: self._cfg.client.mute_other_apps),
            item(lambda i: _("Автозапуск"), _menu_action(self._toggle_autostart),
                 checked=lambda i: self._cfg.autostart),
            item(lambda i: _("Открыть файл настроек"), _menu_action(self._open_config)),
            item(lambda i: _("Открыть словарь терминов"), _menu_action(self._open_terms)),
            menu.SEPARATOR,
            item(lambda i: gatewayctl.status_line(self._health), None, enabled=False),
            item(lambda i: _("Кнопка: найдена") if self._client.button_found else _("Кнопка: не найдена"),
                 None, enabled=False),
            item(lambda i: _(CONFIG_ERROR), None, enabled=False, visible=lambda i: self._config_bad),
            item(lambda i: _("Запустить шлюз"), _menu_action(self._start_gateway),
                 visible=lambda i: self._health is None),
            item(lambda i: _("Перезапустить шлюз"), _menu_action(self._restart_gateway)),
            item(lambda i: _("Остановить шлюз"), _menu_action(self._stop_gateway)),
            item(lambda i: _("Открыть журналы"), _menu_action(self._open_logs)),
            menu.SEPARATOR,
            item(lambda i: self._update_line() or "", _menu_action(self._apply_update),
                 visible=lambda i: self._update_line() is not None),
            item(lambda i: _("Обновления: {note}").format(note=self._update.note), None, enabled=False,
                 visible=lambda i: bool(self._update.note)),
            item(lambda i: self._check_line(), _menu_action(self._check_update_now),
                 visible=lambda i: self._update.kind != "off"),
            item(lambda i: _("Выход…"), _menu_action(self._exit)),
        )

    def _dictation_row(self) -> str:
        """Start or stop, and what does it besides this row: the hotkey, or the microphone button."""
        text = _("Остановить диктовку") if self._client.state == "recording" else _("Начать диктовку")
        if self._button_rules:
            return f"{text}\t{_('Кнопка микрофона')}"
        return _with_key(text, self._client.hotkeys["dictation"])

    def _microphones(self):
        """Submenu rows: rebuilt on every update_menu() from the cached device list."""
        current = self._cfg.client.microphone.lower()
        yield pystray.MenuItem(_("По умолчанию"), _menu_action(self._pick_microphone("")),
                               checked=lambda i: current == "", radio=True)
        for name in self._devices:
            yield pystray.MenuItem(name, _menu_action(self._pick_microphone(name)),
                                   checked=lambda i, name=name: bool(current) and current in name.lower(),
                                   radio=True)

    def _controls(self):
        """Submenu rows: what starts and stops a dictation. Not to be changed while one is
        being recorded: it ends the way it began."""
        current = self._cfg.client.control
        free = self._client.state != "recording"
        for control in config.CONTROLS:
            yield pystray.MenuItem(_(CONTROL_NAMES[control]), _menu_action(self._pick_control(control)),
                                   checked=lambda i, control=control: control == current, radio=True,
                                   enabled=free)

    def _positions(self):
        """Submenu rows: where the overlay sits on the monitor."""
        current = self._cfg.client.overlay_position
        for position in overlay.POSITIONS:
            yield pystray.MenuItem(_(POSITION_NAMES[position]), _menu_action(self._pick_position(position)),
                                   checked=lambda i, position=position: position == current, radio=True)

    def _languages(self):
        """Submenu rows: the language of the menu, the dialogs and the overlay."""
        current = self._cfg.language
        for code in i18n.LANGUAGES:
            yield pystray.MenuItem(LANGUAGE_NAMES[code], _menu_action(self._pick_language(code)),
                                   checked=lambda i, code=code: code == current, radio=True)

    # -- actions ------------------------------------------------------------

    def _toggle(self, icon, item) -> None:
        self._client.toggle("menu")

    def _insert_last(self, icon, item) -> None:
        self._client.insert_last("menu")

    def _copy_last(self, icon, item) -> None:
        paste.copy_text(self._client.last_text)

    def _edit_config(self, change) -> bool:
        """Save a menu change; False if nothing was written. A file that could not be
        parsed is never overwritten (the user's edit would be lost to defaults); one
        with rejected fields is copied to config.json.bak first."""
        path = paths.config_file()
        loaded = config.load_reported(path)
        if config.unparsed(loaded):
            log.warning("config.json could not be parsed: the menu change is not saved")
            self.refresh()
            return False
        if loaded.problems:
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        config.save(change(loaded.config), path)
        self.refresh()
        return True

    def _pick_microphone(self, name: str):
        def pick(icon, item) -> None:
            self._edit_config(lambda cfg: replace(cfg, client=replace(cfg.client, microphone=name)))
        return pick

    def _pick_control(self, control: str):
        def pick(icon, item) -> None:
            self._edit_config(lambda cfg: replace(cfg, client=replace(cfg.client, control=control)))
        return pick

    def _pick_position(self, position: str):
        def pick(icon, item) -> None:
            self._edit_config(lambda cfg: replace(cfg, client=replace(cfg.client, overlay_position=position)))
        return pick

    def _pick_language(self, code: str):
        def pick(icon, item) -> None:
            self._edit_config(lambda cfg: replace(cfg, language=code))
        return pick

    def _toggle_mute(self, icon, item) -> None:
        self._edit_config(lambda cfg: replace(
            cfg, client=replace(cfg.client, mute_other_apps=not cfg.client.mute_other_apps)))

    def _toggle_autostart(self, icon, item) -> None:
        if self._edit_config(lambda cfg: replace(cfg, autostart=not cfg.autostart)):
            autostart.apply(config.load().config.autostart, self._root, gatewayctl.pythonw_path())

    def _open_config(self, icon, item) -> None:
        if not paths.config_file().exists():
            config.save(config.load().config)
        subprocess.Popen(["notepad.exe", str(paths.config_file())])

    def _open_terms(self, icon, item) -> None:
        terms.ensure_file(paths.terms_file())
        subprocess.Popen(["notepad.exe", str(paths.terms_file())])

    def _open_logs(self, icon, item) -> None:
        subprocess.Popen(["explorer.exe", str(paths.logs_dir())])

    def _start_gateway(self, icon, item) -> None:
        gatewayctl.launch(self._root)
        self._await_gateway()

    def _stop_gateway(self, icon, item) -> None:
        gatewayctl.stop(config.load().config.gateway, keyfile.read_key())
        self._check()

    def _restart_gateway(self, icon, item) -> None:
        if gatewayctl.stop(config.load().config.gateway, keyfile.read_key()):
            gatewayctl.launch(self._root)
        self._await_gateway()

    def _exit(self, icon, item) -> None:
        answer = ctypes.windll.user32.MessageBoxW(
            None, _(EXIT_QUESTION), _(APP_NAME), MB_YESNOCANCEL | MB_ICONQUESTION | MB_TOPMOST)
        if answer not in (IDYES, IDNO):
            return
        self._stopping.set()
        self._stop_client()
        if answer == IDYES:
            gatewayctl.stop(config.load().config.gateway, keyfile.read_key())
        self.icon.stop()

    # -- updates -------------------------------------------------

    def _update_line(self) -> str | None:
        return updater.menu_label(self._update, VERSION, self._on_disk, self._stale)

    def _check_line(self) -> str:
        """What the last check BY HAND found, with its own time; the automatic ones are silent."""
        if self._checked is not None and self._update.kind != "behind":
            at, kind = self._checked
            if kind == "unknown":
                return _("Нет связи с origin — проверено в {time}").format(time=at)
            if kind == "none" and self._update.kind == "none":
                return _("Обновлений нет — проверено в {time}").format(time=at)
        return _(CHECK_UPDATES)

    def _ask(self, text: str) -> bool:
        return ctypes.windll.user32.MessageBoxW(
            None, text, _(APP_NAME), MB_YESNO | MB_ICONQUESTION | MB_TOPMOST) == IDYES

    def _say(self, text: str) -> None:
        """An answer to a click, never something that pops up by itself."""
        ctypes.windll.user32.MessageBoxW(None, text, _(APP_NAME), MB_ICONINFORMATION | MB_TOPMOST)

    def _check_update(self) -> str | None:
        """Ask origin (a network call, up to 15 s): never on a thread the icon needs.
        Returns what was found, or None when nothing was asked - an update is being taken
        right now (two git commands at once trip over each other's locks) or git blew up."""
        if not self._update_lock.acquire(blocking=False):
            return None
        try:
            try:
                found = updater.status(self._root)
            except Exception:
                log.exception("could not check for updates")
                return None
            # An origin that did not answer says nothing: an update we already know about stays.
            if found.kind != "unknown" or self._update.kind == "off":
                if found.kind == "behind" and found != self._update:
                    log.info("update available: %s", found.version)
                self._update = found
        finally:
            self._update_lock.release()
        self.refresh()
        return found.kind

    def _check_update_now(self, icon, item) -> None:
        kind = self._check_update()
        if kind is not None:
            self._checked = (time.strftime("%H:%M"), kind)
            self.refresh()

    def _apply_update(self, icon, item) -> None:
        if not self._update_lock.acquire(blocking=False):
            return  # a second click while the first is still asking or pulling
        try:
            update = self._update
            libraries_changed = False
            if update.kind == "behind":
                if not self._ask(_(UPDATE_QUESTION).format(version=update.version or "?")):
                    return
                try:
                    pulled = updater.pull(self._root, update)
                except updater.UpdateError as e:
                    log.warning("update not taken: %s", i18n.english(e.args[0] if e.args else e))
                    self._say(str(e))
                    self._check_update()  # the menu line was out of date: ask origin again
                    return
                log.info("updated %s -> %s", pulled.old[:8], pulled.new[:8])
                libraries_changed = pulled.requirements_changed
            elif self._update_line() is None:
                return
            # The libraries are behind requirements.txt when the record says so (a pin moved,
            # an install failed) or the file differs from the one this tray started with.
            if updater.libraries_stale(self._root) or updater.requirements_digest(self._root) != self._requirements:
                libraries_changed = True
            self._hand_over(libraries_changed or self._stale)
        finally:
            self._update_lock.release()

    def _hand_over(self, install_libraries: bool) -> None:
        """Give way to a new process that runs the code on disk. What cannot be undone
        comes last: a successor that cannot be started leaves this tray working."""
        if install_libraries:
            # pip needs the tray and the gateway gone (they hold the files it replaces) and a
            # window that shows what went wrong: the launcher window does all of that.
            try:
                relaunch.spawn_console(self._root)
            except OSError:
                log.exception("could not open the launcher window")
                self._say(_(RESTART_FAILED))
                return
            log.info("handing over to the launcher window: the libraries changed")
        else:
            if not gatewayctl.stop(config.load().config.gateway, keyfile.read_key()):
                # The new tray would adopt a gateway that still runs the old code.
                log.warning("the gateway did not stop: restart cancelled")
                self._say(_(GATEWAY_STUCK))
                return
            try:
                relaunch.spawn(self._root)
            except OSError:
                log.exception("could not start the new tray")
                try:
                    gatewayctl.launch(self._root)
                except OSError:
                    log.exception("could not start the gateway again")
                self._say(_(RESTART_FAILED))
                return
            log.info("handing over to a new tray process")
        self._stopping.set()
        self._stop_client()
        self.icon.stop()

    # -- state --------------------------------------------------------------

    def refresh(self) -> None:
        """Repaint the icon and rebuild the menu if anything they show changed. Safe from
        any thread (the poll, menu actions and the controller call it); a no-op until the icon is up."""
        if not self._ready or self._stopping.is_set():
            return
        with self._refresh_lock:
            try:
                loaded = config.load_reported()
                self._cfg, self._config_bad = loaded.config, bool(loaded.problems)
                i18n.set_language(self._cfg.language)
                if self.icon.title != _title():
                    self.icon.title = _title()
                self._button_rules = self._client.button_rules()
                state = gatewayctl.glance(self._client.state, self._health)
                if state != self._shown:
                    self._shown = state
                    self.icon.icon = icons.image(state)
                # The menu lambdas read these same values; the native menu is rebuilt only when one changes.
                shown = (self._client.state, bool(self._client.last_text), self._client.button_found,
                         self._cfg.client.microphone, self._cfg.client.mute_other_apps, self._cfg.autostart,
                         self._cfg.client.overlay_position, self._cfg.client.control,
                         self._button_rules, tuple(self._client.hotkeys.items()),
                         self._update_line(), self._update.note, self._check_line(), self._update.kind,
                         self._stale, i18n.language(), self._cfg.language,
                         self._config_bad, gatewayctl.status_line(self._health), self._health is None,
                         tuple(self._devices))
                if shown != self._menu_shown:
                    self.icon.update_menu()
                    self._menu_shown = shown
            except Exception:
                log.exception("could not refresh the tray icon")

    def _check(self, devices: bool = False) -> None:
        """Probe the gateway and take a new microphone list if the client has one: it rebuilds
        the list when Windows reports a change, or - without notifications - when `devices` says so."""
        self._health = gatewayctl.probe(config.load().config.gateway)
        names = self._client.microphones(periodic=devices)
        if names:
            self._devices = names
        self.refresh()

    def _await_gateway(self, seconds: float = 20.0) -> None:
        """After a launch: check every second until the gateway is ready, instead of waiting for the 5 s poll."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stopping.is_set():
            self._check()
            if self._health is not None and self._health.status != "loading":
                return
            time.sleep(1.0)

    def _poll(self) -> None:
        tick = 0
        while not self._stopping.wait(POLL_S):
            tick += 1
            try:
                self._on_disk = updater.disk_version(self._root) or VERSION
                self._stale = updater.libraries_stale(self._root)
                if tick % UPDATE_EVERY == 0:
                    # In its own thread: origin may take 15 s to say nothing, and this
                    # loop is what watches the gateway.
                    threading.Thread(target=self._check_update, name="update-check", daemon=True).start()
                self._check(devices=tick % DEVICES_EVERY == 0)
            except Exception:
                log.exception("health check failed")

    def _setup(self, icon) -> None:
        """Runs in pystray's setup thread once the icon exists."""
        icon.visible = True
        self._ready = True
        self._client.start()
        self._started = True
        try:
            self._check(devices=True)
            if self._health is None:
                try:
                    gatewayctl.launch(self._root)   # once, at tray start; a gateway that dies later is not relaunched
                except OSError:
                    log.exception("could not start the gateway")
            self._await_gateway()
        except Exception:
            log.exception("tray start-up check failed")
        threading.Thread(target=self._poll, name="health", daemon=True).start()
        threading.Thread(target=self._check_update, name="update-check", daemon=True).start()

    def _stop_client(self) -> None:
        """Stop the client at most once, and only if it was started."""
        if not self._started or self._client_stopped:
            return
        self._client_stopped = True
        try:
            self._client.stop()
        except Exception:
            log.exception("could not stop the client")

    def run(self) -> None:
        try:
            self.icon.run(setup=self._setup)   # an exception from the icon loop propagates to main(), which logs it
        finally:
            self._stopping.set()
            self._stop_client()
