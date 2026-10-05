# Tolmach

Dictation for Windows: press the button on your microphone (or a hotkey), speak, and the text is
typed into the window that has the cursor. Speech is recognised on your own computer with
[GigaAM v3](https://github.com/salute-developers/GigaAM), a Russian speech model; nothing is sent
anywhere.

*Tolmach (толмач) is an old Russian word for an interpreter.* The program recognises **Russian**
speech. Its tray menu, dialogs and overlay are available in Russian and English.

**[Читать по-русски](README.ru.md)**

## What it does

- **Starts dictation** with the button of a USB microphone, the hotkey `Shift+Win+Q`, or the tray menu.
- **Live text.** While you speak, the recognised text is shown in a small overlay; when you stop, it
  is typed into the window you were working in. The text is typed as keystrokes, so it also reaches a
  remote desktop window, and the clipboard is left alone.
- **Punctuation and numbers** come from the model itself: "сорок минут" becomes "40 минут".
- **The last text again:** `Shift+Win+Z` types the last recognised text once more; the menu can also
  copy it.
- **A terms dictionary:** `Githab`, `GetHub` and `в гитхабе` are written `GitHub` once the word is
  in your dictionary - matched by sound, not by letters.
- **Mutes other programs** while recording (optional).
- **A tray icon** shows the state: idle, recording, recognising, gateway not answering.
- **Updates** through git: when the repository has a newer version, the menu offers
  "Update to X.Y.Z…". Nothing installs by itself.

## How it works

Two processes. The **tray** holds the icon, records from the microphone, shows the overlay and types
the text. The **gateway** is a local recognition service on `127.0.0.1` that speaks a subset of the
OpenAI Realtime protocol (`WS /v1/realtime`) and accepts files (`POST /v1/audio/transcriptions`); it
is protected by a key and is not reachable from the network. The tray starts the gateway and watches
it. For a file, `response_format=verbose_json` adds the phrases with their start and end times to the
answer. The times are those of the piece of sound handed to the recogniser: a phrase begins up to half a
second before its first word and ends after the pause that closed it.

## Requirements

- Windows 10 or 11, 64-bit.
- [Python 3.12](https://www.python.org/downloads/) (64-bit) and [git](https://git-scm.com/).
- A microphone. A button on it is optional: without one, dictation starts with the hotkey.
- About 350 MB of disk space for the models.

## Installation

```
git clone https://github.com/bsdate-cc/tolmach.git
cd tolmach
tray.cmd
```

`tray.cmd` (a double click works too) creates the `.venv` environment and checks Python, the
libraries, the settings, the models, the microphone and the gateway. Whatever is missing it offers to
install: the libraries with pip, the models by downloading them. Then it starts the tray. Every step
is printed in the window and written to a log.

### Models

The model files are not in the repository. On the first start `tray.cmd` offers to download them
(about 330 MB) into `%USERPROFILE%\.tolmach\models`:

| File | What it is |
|---|---|
| `silero_vad.onnx` | Silero VAD, the pause detector |
| `gigaam-v3\gigaam_v3_e2e_rnnt_encoder_int8.onnx` | GigaAM v3, the `e2e_rnnt` model as ONNX |
| `gigaam-v3\gigaam_v3_e2e_rnnt_decoder.onnx` | |
| `gigaam-v3\gigaam_v3_e2e_rnnt_joint.onnx` | |
| `gigaam-v3\gigaam_v3_e2e_rnnt_tokens.txt` | |

GigaAM v3 is published by SberDevices under the MIT licence
([ai-sage/GigaAM-v3](https://huggingface.co/ai-sage/GigaAM-v3)). The files are downloaded from a
community conversion for [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx),
[Smirnov75/GigaAM-v3-sherpa-onnx](https://huggingface.co/Smirnov75/GigaAM-v3-sherpa-onnx), at one
fixed revision; `silero_vad.onnx` comes from the
[sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models). Each file is
checked against a known SHA-256 before it is put in place. To download them without the launcher:
`.venv\Scripts\python -m tolmach.tray.models`.

**A model of your own.** Any model that sherpa-onnx loads as a NeMo transducer (encoder, decoder,
joiner, tokens) can be used: put its files into the models folder and name them in `config.json`,
section `gateway.model`. The launcher downloads only the files listed above; files of your own are
yours to put in place.

## Usage

1. Put the cursor where the text should go.
2. Press the button on the microphone or `Shift+Win+Q`. The overlay shows "Listening…".
3. Speak. Press again, and the text is typed.

The tray menu: start and stop dictation, copy or insert the last text, choose the microphone, the
overlay position and the language, mute other programs, start with Windows, control the gateway, open
the logs, update.

**The microphone button.** USB microphones whose button reports a press over HID (the Consumer
Control page) are supported. The default is a microphone with `VID 1B3F`, `PID 2008`; for another one
put its identifiers into `client.button` in `config.json`. Such a button usually also mutes the
microphone in hardware, and the program takes that into account.

## Terms dictionary

The model hears Russian, so a developer's vocabulary comes out right in sound and wrong in letters:
`Githab`, `GetHub`, `гитхаб`, `Word 3` for `worktree`. The dictionary fixes that after recognition:
where the recognised words **sound** like a term, the term is written the way the dictionary spells
it.

The menu item "Open the terms dictionary" creates `%USERPROFILE%\.tolmach\terms.txt` with a starter
set and opens it in Notepad. One term per line:

```
GitHub
worktree = ворктри
main = мэйн, мейн
```

A line with only the term is enough when the word is said the way it is written. After `=` come, in
Russian letters, the ways you say it or the ways the model writes it, when it is not. Changes are
picked up at once; an empty file, or no file, switches the dictionary off.

What to expect. Words in Russian letters are ordinary speech, so they are replaced only when they
sound exactly like the term, word for word: `гитхаб` and `в гитхабе` become `GitHub`, and `бетон`
does not become `Python`. Where the model itself wrote Latin letters - its own sign that the word is
not a Russian one - a close sound is enough, and the term may come in pieces: `GetHub`, `Local Host`,
`Whispery`. A short term (`git`, `pip`) is taken only from Latin letters, because `гид` sounds the
same. Nothing is ever added that was not recognised.

If the model keeps writing a term its own way in Russian letters (`Гит Хаб`, `лакафост`), add that
spelling to the term's line: `GitHub = гит хаб`.

What it cannot do: a term shorter than three sounds (`Go`, `C`) needs the way it is said
(`Go = голанг`); a word with a digit inside (`python3`) is left alone; a term cut in two by a pause or
a punctuation mark is not put together. A word that sounds exactly like a term is replaced even when
you meant something else: `питон` always becomes `Python`.

## Settings

`%USERPROFILE%\.tolmach\config.json` (menu item "Open the settings file"). The main ones:

| Key | Default | What it does |
|---|---|---|
| `language` | `auto` | language of the menu, dialogs and overlay: `ru`, `en`, or `auto` (Russian when Windows or its regional format is Russian, English otherwise) |
| `client.microphone` | `""` | microphone name; empty means the microphone with the button, else the system default |
| `client.hotkey` | `shift+win+q` | the dictation hotkey |
| `client.insert_last_hotkey` | `shift+win+z` | types the last recognised text again; `""` switches it off |
| `client.insert_mode` | `type` | `type`: keystrokes; `paste`: the clipboard and Ctrl+V |
| `client.append_space` | `true` | a space after the inserted text |
| `client.overlay_position` | `center` | where the overlay sits: `top-left` … `center` … `bottom-right` |
| `client.mute_other_apps` | `true` | mute other programs while recording |
| `client.silence_autostop_s` | `60` | stop the recording after this many seconds of silence |
| `gateway.port` | `8765` | the gateway's port on `127.0.0.1` |
| `autostart` | `true` | start the tray when you log in to Windows |

All settings and their defaults are in `tolmach/config.py`.

The two hotkeys and the button identifiers (`client.button`) are read when the tray starts; the other
`client` settings take effect with the next dictation.

## Where things are

- `%USERPROFILE%\.tolmach\` holds the settings, the terms dictionary (`terms.txt`), the gateway key,
  the models and the logs
  (`logs\tray.log`, `logs\gateway.log`, `logs\startup.log`). The `TOLMACH_HOME` variable points it
  elsewhere.
- Dictated text and audio are never written to the logs. A recording that the gateway could not
  finish because of a failure is saved to `failed\`, so that what was said is not lost.

## Updating

The tray compares itself with the repository when it starts and every six hours. When there is a
newer version, the menu shows "Update to X.Y.Z…": after a click and a confirmation the code is
updated (git, fast-forward only) and the tray restarts. An update runs code from the repository with
your rights, which is why it is only ever installed on a click. A working copy with changes of its
own is never offered updates.

Version history: [CHANGELOG.md](CHANGELOG.md) (in Russian).

## Development

```
.venv\Scripts\python -m pytest -q
```

The tests need no models; three tests on the real models are skipped when the models or
`tests\data\benchmark.wav` are absent.

## Licence and credits

The code is under the [MIT licence](LICENSE).

- [GigaAM](https://github.com/salute-developers/GigaAM): the Russian speech recognition model by
  SberDevices.
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx): runs the models.
- [Silero VAD](https://github.com/snakers4/silero-vad): pause detection.
- [pystray](https://github.com/moses-palmer/pystray), [pycaw](https://github.com/AndreMiras/pycaw),
  [sounddevice](https://github.com/spatialaudio/python-sounddevice),
  [hidapi](https://github.com/trezor/cython-hidapi), [aiohttp](https://github.com/aio-libs/aiohttp).

The program used to be called SpeechKit; on the first start the settings in
`%USERPROFILE%\.speechkit` are moved to `%USERPROFILE%\.tolmach` automatically.
