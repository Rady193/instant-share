# Release notes

## 1.0.0 — 15 November 2026

The first release.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Text-Share-1.0.0-portable.exe` | 10.5 MB | `B6B79B9FEDFF78080E4E4AE3DED6D0D1DD064A2DC1BDF8D312FF852981A46B83` |
| `textshare.py` | 44 KB | `315DEEED670014A8BEB46B7ED1C65A8EA751D56547AF9DBAD4138C432F9857FA` |

The `.exe` is for 64-bit Windows 10 and 11 and is not code-signed, so Windows SmartScreen may ask for confirmation on
first run. `textshare.py` is the same program for Mac and Linux (`python3 textshare.py`).

### Features

- **Share text across your network.** Run it on one computer; the others use any browser, with nothing to install.
  New items reach every open page within about two seconds.
- **History.** Search it, copy any item with one click, reopen an item in the editor to change and share it again,
  delete with a two-click confirm. Long items show their start, with **Show all** for the rest.
- **Offline.** No internet, no accounts, no outside fonts or scripts. It answers only computers on its own network.
- **Nothing lost.** Unsent text is kept in the browser, and the page tells you when the computer running Text Share
  can't be reached, then reconnects by itself.
- **Computer names.** Each computer gets a name (filled in from the network, changeable) shown next to what it shares.
- **Right-to-left text.** Arabic lines read right to left, line by line.
- **Start with Windows.** `--autostart on` / `off`.
- **One file.** The `.exe` carries its own Python. Starting it a second time just opens the running one. The page's
  **Get the app file** link hands out the program to other computers on the network.

### Tested

- On Windows 11 with Edge, as two computers (the one running Text Share, and another reaching it over the network by
  IP address): sharing both ways, copy (also over plain http), edit, delete reaching the other computer, search,
  a 1,500-line item, Arabic text and computer names, a draft surviving a reload, the computer running Text Share
  going off and coming back, and a narrow phone-sized window.
- Reaching it by computer name, which Windows answers over IPv6.
- The `.exe`: file properties (publisher SCAFFApps, version 1.0.0), starting twice on the same port, a port taken by
  another program, `--autostart on` (into a test folder), and the download link handing out the identical file.
- Microsoft Defender finds no threats in the `.exe`.

### Not tested

- Windows 10, and other computers or phones on a real network (the "other computer" in the tests was the same PC, by
  its network address).
- `textshare.py` on Mac or Linux.
- The Windows Firewall prompt on first run, and SmartScreen on a computer that downloaded the file.
