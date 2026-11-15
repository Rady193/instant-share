# Text Share

Text Share passes text between the computers on your own network. Write or paste on one computer and press **Share**,
and every other computer has it at once. Everything shared stays in a history you can search, copy from, reopen and
delete from. It needs no internet and no accounts.

Version 1.0.0 · Windows 10 and 11 (64-bit) · Published by SCAFFApps

## Contents

- [How it works](#how-it-works)
- [Install](#install)
- [Quick start](#quick-start)
- [Using it](#using-it)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Start with Windows](#start-with-windows)
- [Options](#options)
- [Where things are saved](#where-things-are-saved)
- [What it does not do](#what-it-does-not-do)
- [Building from source](#building-from-source)
- [Project layout](#project-layout)
- [Licence](#licence)

## How it works

You run Text Share on **one** computer, ideally one that stays on. The other computers need nothing installed: they
open its address in any browser (Edge, Chrome, Firefox, Safari, including on phones and tablets) on the same Wi-Fi or
wired network.

```
  laptop ──browser──┐
  laptop ──browser──┼──▶  the computer running Text Share  (keeps the history)
  phone  ──browser──┘
```

## Install

Download from the [Releases page](https://github.com/Rady193/text-share/releases/latest).

| File | What it is |
|---|---|
| `Text-Share-1.0.0-portable.exe` | The program for Windows, in one file. Nothing is installed and no administrator permission is needed. Copy it anywhere (a USB stick works too) and double-click it. |
| `textshare.py` | The same program as a Python script, for Mac and Linux: `python3 textshare.py` (needs Python 3.10 or newer, nothing else). |
| `THIRD-PARTY-NOTICES.txt` | The licences of the parts the Windows program carries inside it (Python and the PyInstaller bootloader). |

The release notes list each file's SHA-256 checksum, so you can check a download with `Get-FileHash <file>` in PowerShell.

The program is not code-signed, so the first time you run it Windows SmartScreen may show "Windows protected your PC".
Choose **More info**, then **Run anyway**.

## Quick start

1. On the computer that will keep the history, double-click `Text-Share-1.0.0-portable.exe`. A window opens showing
   the addresses, and Text Share opens in your browser.

   ```
   Text Share 1.0.0 is running on OFFICE-PC.

   On this computer:      http://localhost:8765/
   From other computers:  http://OFFICE-PC:8765/
                      or  http://192.168.1.20:8765/
   ```

2. Windows asks whether to allow the program on networks. Allow it on **Private networks**. That is what lets the
   other computers in.
3. On every other computer, open one of the "From other computers" addresses in a browser and bookmark it.
4. Leave the window open (minimised is fine). Closing it stops Text Share.

If the other computers cannot connect:

- Check that the network is set to **Private** on the computer running Text Share (Settings > Network & internet >
  Wi-Fi or Ethernet > Network profile type).
- If Windows never asked to allow the program, add the rule yourself in an **administrator** PowerShell:

  ```
  netsh advfirewall firewall add rule name="Text Share" dir=in action=allow protocol=TCP localport=8765 profile=private
  ```

- If the computer name doesn't work, use the numbered address (`http://192.168.x.x:8765/`).

## Using it

- **Share.** Write or paste in the editor on the left and press **Share**. Within about two seconds it is at the top of
  the history on every computer that has the page open.
- **Copy.** Every item has a **Copy** button: one click and it is ready to paste.
- **Edit.** Puts an item back in the editor, so you can change it and share it again. The original stays in the history.
- **Delete.** Click **✕**, then **Delete?** to confirm. The item disappears from every computer.
- **Search.** The box above the history finds items by their text or by the computer that shared them.
- **Long text** such as logs is fine, up to 20 MB per item. The history shows the start of it, and **Show all** shows
  the rest.
- **Name each computer.** Click the name at the top right. The name is shown next to everything that computer shares.
- **Nothing lost.** What you are typing is kept in that browser, so a closed tab or a restart doesn't lose it.
- **New items** from other computers glow briefly, and a tab in the background shows the count in its title, e.g.
  `(2) Text Share`.
- **Arabic and other right-to-left text** is shown right to left, line by line.
- The dot at the top shows whether the page can reach Text Share. When it can't (the computer running it is off or
  asleep), your text stays in the editor, and the page reconnects by itself.

## Keyboard shortcuts

| Keys | Does |
|---|---|
| Ctrl+Enter | Share what is in the editor |
| Ctrl+S | Share what is in the editor |

## Start with Windows

To have Text Share start by itself (minimised, without opening the browser) whenever you sign in to Windows:

```
Text-Share-1.0.0-portable.exe --autostart on
```

`--autostart off` undoes it. If you move or rename the program, run `--autostart on` again from its new place.

## Options

```
Text-Share-1.0.0-portable.exe [--no-open] [--port PORT] [--data FOLDER] [--autostart on|off]
```

| Option | Does |
|---|---|
| `--no-open` | Starts without opening the browser. |
| `--port 9000` | Uses another port (default `8765`). The addresses then end in `:9000`. |
| `--data D:\TextShare` | Keeps the history in another folder, e.g. next to the program on a USB stick. |
| `--autostart on` / `off` | Starts with Windows, or stops doing so. |

Starting the program while it is already running just opens the running one in the browser.

## Where things are saved

| What | Where |
|---|---|
| The history | One file on the computer running Text Share: `%LOCALAPPDATA%\TextShare\history.db` (Mac and Linux: `~/.textshare/history.db`). Copy it to back it up. To start over, stop Text Share and delete it. |
| Each computer's name and unsent text | That computer's browser. |
| Start with Windows | `Text Share.cmd` in your Startup folder (`shell:startup`). |

## What it does not do

- **No password and no encryption.** Anyone on the same network who knows the address can read and change the
  history. Use it on networks you trust, such as home or your own office, never on public Wi-Fi. Delete passwords or
  keys once you have pasted them where they belong.
- **Only your own network.** It refuses connections from outside the local network, and it doesn't reach the internet
  for anything.
- **Text only.** It doesn't send files or images.
- **One history.** Every computer sees the same history. There are no private items or folders.
- **The computer running it must be on.** The history lives there. When that computer sleeps, the others can't share.

## Building from source

You need Windows and Python 3.10 or newer (the release is built with Python 3.14).

To run from the source: `py textshare.py`, or double-click `Text Share.cmd`.

To produce the portable program, `textshare.py` and `THIRD-PARTY-NOTICES.txt` in the `release` folder:

```
py build.py
```

The first run makes a build environment in `C:\venvs\textshare-build` with PyInstaller 6.22.3. The build prints each
file's SHA-256 checksum and saves the list in `release\SHA256.txt`.

## Project layout

| Path | What it is |
|---|---|
| `textshare.py` | The whole program: the server, the history (SQLite) and the page. Python standard library only. |
| `Text Share.cmd` | Runs it from the source. |
| `build.py` | Packs it into the portable Windows program. |
| `build/` | The icon (`icon.svg`, and `make_icon.py`, which draws `icon.png` and `icon.ico`). |
| `RELEASE-NOTES.md` | What changed in each version. |

## Licence

Copyright © 2026 SCAFFApps. All rights reserved. The released program is free to download and use, for personal or
business use. The source code is published for reference only. See [LICENSE](LICENSE).
