# Taipan Studio

A replacement for the Redragon M810 Taipan Pro (M810RGB-PRO) configuration software, with a clear-glass UI
on a pure AMOLED-black background.

Unofficial: not made by or affiliated with Redragon. Redragon and Taipan are their owners' trademarks.

## Download

Get `Taipan-Studio.exe` from the [latest release](../../releases/latest). It is a single file for Windows 10/11
and needs no install. It uses WebView2, which Windows 11 already has.

## Features

- **Performance:** 1–5 DPI stages from 200 to 10 000 DPI, separate X/Y values, per-stage colours, and polling
  rate (125/250/500/1000 Hz).
- **Lighting:** side strips and wheel (Streaming, Breathing, Steady, Off) with colour, speed and brightness, plus
  the DPI indicator LED. The mouse drawing previews the effect live.
- **Buttons:** remap all 8 buttons (and 2 hidden slots) to mouse clicks, double or triple click, a fire key,
  DPI up/down/cycle, any key combo, F13–F24, media keys, macros, a polling-rate switch, lighting on/off, or Off.
- **Macros:** record keys and mouse buttons with real or fixed delays, edit them, and set repeat to once, N times,
  while held, or until pressed again.
- **Shortcuts tab:** hold a mouse button, then scroll or click to run an action in any app. Out of the box, holding
  Forward (MB5) and scrolling changes the volume. Actions include volume and media keys, opening an app, file or
  website, any keyboard shortcut, and locking the PC. You can record a combo by doing it on the mouse, or pause
  shortcuts in chosen apps such as games. A plain tap of the button still does its normal job. Each shortcut is a
  tile with the app's own icon, and Scroll up / down for volume or tracks is one shortcut. A click shortcut runs
  as soon as its last click lands, or when you let go of the button if a longer combo starts the same way.
- **Tray:** closing the window keeps Taipan Studio in the notification area so shortcuts keep working. It can start
  with Windows, and Quit is in the tray menu. Only one copy runs at a time.
- **Live DPI pop-up:** pressing the DPI buttons shows the new value on screen.
- **Profiles:** unlimited presets on this PC, plus JSON export/import and factory settings.
- **Safety:** a full backup of the mouse's settings memory is taken before the first save, and you can make more
  backups or restore one at any time. Every save is read back from the mouse and checked.
- **Windows pointer:** pointer speed, Enhance pointer precision, scroll lines and double-click speed.

## Run

```
pip install -r requirements.txt
python app/taipan.py              # TAIPAN_MOCK=1 runs it against a simulated mouse
python build.py                   # -> dist/Taipan Studio.exe
python tests/test_m810.py         # offline encoder/decoder tests
python tests/test_combos.py       # offline combo state-machine tests
python tests/test_save.py         # save path against the simulated mouse
```

`--tray` starts hidden in the tray. `--quit` asks the running copy to exit, which a rebuild needs because Windows
won't replace an exe that is running. `TAIPAN_DATA=<folder>` keeps settings in another folder, which also makes it a
separate single instance (handy for testing next to your normal copy).

Close the Redragon app (OemDrv.exe) first. Both apps talk through the same receiver and would take each other's
replies. The mouse must be awake (moved recently) for its settings to be read or saved. The 2.4G receiver
answers on its own, so a sleeping mouse shows as "asleep" rather than missing.

## Combos and CPU use

Combos run in the app, not on the mouse, using a low-level mouse hook (`app/combos.py`). The hook is installed
only while a combo is on. Mouse movement, which is nearly all the traffic, is passed straight on by a 40-byte
native filter and never reaches Python. Only clicks and scrolls are looked at. These figures come from a demo-mode
test on the dev PC, a 12-thread CPU:

- **Hidden in the tray, no input:** about 0.5% of one core in total, the same with combos on or off. That is
  WebView2 and the device poll, at the measurement's noise floor.
- **Mouse moving at about 750 events a second:** the hook adds about 1.1% of one core.

## How it talks to the mouse

Recovered from OemDrv.exe 3.1 with REA and Ghidra. The full EEPROM map is in `app/m810.py`.

- The 2.4G receiver is VID `25A7`, PID `FA7E`. Commands are 17-byte feature reports (id `08`) sent to the
  vendor collection with usage page `FF02`: `[08, cmd, status, addrHi, addrLo, len, data×10, chk]`, where
  `chk = 0x55 - sum(bytes 0..15)`.
- Replies come back as input reports (id `09`) on usage page `FF01`, in the same layout. `cmd 0A` is an
  unsolicited event, such as a DPI button press or a lighting toggle.
- Commands: `02` driver on/off, `03` is the mouse linked?, `04` keep-alive, `07` write EEPROM, `08` read EEPROM.
  `01` is a challenge/response the original app uses to check the device is genuine. `09` puts the mouse back to
  its built-in defaults, so never send it after a save: writes take effect as they land and need no commit.
- Replies to `07` and `08` echo the address, so the app matches each reply to its request.
- Settings live in a 0x1B00-byte EEPROM. Most fields carry a check byte so the value plus its check byte equals
  `0x55`.

## License

MIT. See [LICENSE](LICENSE).
