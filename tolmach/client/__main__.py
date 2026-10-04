"""Console runner for the dictation client: hotkey only, gateway started by hand."""
from __future__ import annotations

import logging
import time

from tolmach import config, logsetup
from tolmach.client.app import ClientApp


def main() -> None:
    log = logsetup.setup("tray")
    logging.getLogger("tolmach").addHandler(logging.StreamHandler())  # a console run also shows the log
    loaded = config.load()
    for problem in loaded.problems:
        log.warning("config: %s: %s", problem.where, problem.message)
    app = ClientApp(on_state=lambda state: log.info("state: %s", state))
    app.start()
    log.info("Tolmach client is running: %s toggles dictation (registered: %s), Ctrl+C exits",
             loaded.config.client.hotkey, app.hotkey_registered)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


if __name__ == "__main__":
    main()
