# Release notes

## 1.0.0 — 15 November 2026

The first release.

### Downloads

| File | Size | SHA-256 |
|---|---|---|
| `Text-Share-1.0.0-portable.exe` | 16.4 MB | `7322F1185BE6F0297B9A9D16ACD520BE9CF2CA60EE281022045BA5FCD2F5AF67` |
| `Text-Share-Handbook-1.0.0.pdf` | 862 KB | `AC675BF4DCEF5A7252C730418B4AF916350BAD834DCB42DAB200C0A3889F8D92` |
| `textshare.py` | 75 KB | `53FB2A03B281CC6942CB2396AF4C513AEC5CFA75060C956CDA02026DE95C5968` |

The `.exe` is for 64-bit Windows 10 and 11 and is not code-signed, so Windows SmartScreen may ask for confirmation on
first run. `textshare.py` is Text Share as a plain server for Mac and Linux (`python3 textshare.py`), used from a
browser.

### Features

- **A Windows app**, one portable file, with its own window and a tray icon. No console window.
- **Setup in one question**: keep the history on this computer, or connect to the one that does. The others are found
  on the network by themselves, and followed if their address changes.
- **Share text, photos and PDFs.** Text up to 20 MB; photos (PNG, JPEG, GIF, WebP) and PDFs up to 1 MB, checked by
  their contents. Add a file with the File button, drag and drop, or paste a picture.
- **Who should see it**: everyone, or only the devices you tick, from a list showing who is online.
- **Pop-ups by the clock** when another device shares with this one: who, a preview, Copy (text or photo) or Save
  (PDF), Open. They come while the window is closed and never take the keyboard.
- **History**: Copy, Copy photo, Save to Downloads, Edit, Delete for everyone, Show all, search (text, file names,
  devices), photos full size.
- **Dark and light appearance**, as in Multi Screens; dark by default; the title bar follows.
- **Tray icon**: click to open; Start with Windows, Change setup, Quit. Closing the window keeps Text Share running
  there; starting it again brings the window back.
- **Phones and browsers** use the same address, without the app.
- **Offline**: no internet, no accounts; it answers only devices on its own network.

### Tested

- On Windows 11, two copies of the app on one PC (one keeping the history, one connecting) and a browser:
  - the first-start setup on both, the second finding the first by itself;
  - starting hidden in the tray, closing to the tray, opening from the tray icon, a second start bringing the window
    back;
  - the device list showing the other app online;
  - a share for one device only: the pop-up on that device alone, its Copy putting the text on the clipboard, and the
    browser not seeing it;
  - a photo shared from the browser: shown in the history, and copied as a picture from the pop-up;
  - files too big or of the wrong kind refused;
  - the light appearance kept.
- The server on its own (27 checks): an earlier history upgraded in place; private items hidden from others, also by
  number and for deleting; photos and PDFs stored and served unchanged; a web page named `.png`, an SVG, files over
  1 MB and broken uploads refused; search by file name.
- The tray menu, opened once and looked at.
- The `.exe`: publisher SCAFFApps and version 1.0.0 in its file properties. Microsoft Defender finds no threats.

### Not tested

- Two separate computers on a real network, and the Windows Firewall question on first start.
- Windows 10.
- Real phones and tablets, and Safari.
- Save (to Downloads) from the pop-up and from the history.
- The tray menu's items when clicked by hand, and Start with Windows at a real sign-in.
- `textshare.py` on Mac or Linux.
- SmartScreen on a computer that downloaded the file.
