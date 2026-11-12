# ASSIST

Armchair's Surface Survey & Information Sharing Tool: a helper for surveying planetary mining
locations in Elite Dangerous from the Rhino SRV. It suggests where to deploy and ping, guides the
ship there with an on-screen overlay, records pings and deposits, and draws a map of each survey.

Windows only (64-bit). Python 3.12 and PySide6 (Qt). This is a 0.x pre-release for field testing.

How to use it in the game: **[GUIDE.md](GUIDE.md)**.

## Setting it up

1. Install **Python 3.12 (64-bit)** if you don't have it: with Python's install manager, `py install 3.12`, or the
   Python 3.12 "Windows installer (64-bit)" from python.org. Other Python versions won't do.
2. Get ASSIST: on GitHub, **Code → Download ZIP** and extract it to a folder of your choice, or `git clone` it.
3. Double-click **Setup.cmd** in that folder, once. It makes the project's own Python in a `.venv` folder there and
   installs the pinned Qt. Windows may ask whether to run it because it came from the internet; it's a short text
   file you can read first.
4. Start ASSIST by double-clicking **ASSIST.cmd**. For a desktop shortcut: right-click ASSIST.cmd, then
   Send to, Desktop (create shortcut).

**Setup.cmd's one network step:** pip downloads Qt (PySide6_Essentials and shiboken6 6.12.0) from PyPI, the
Python Package Index. Every file is checked against the sha256 hashes in `requirements.txt` and refused if it
doesn't match. pip isn't upgraded and its version check is off, so nothing else is fetched. Setup.cmd writes only
inside the ASSIST folder: no shortcut, no registry, no PowerShell. ASSIST itself never uses the network.

Setup.cmd does the same as typing these two lines yourself:

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.txt
```

ASSIST.cmd runs `.venv\Scripts\pythonw.exe main.py` and passes any options on, e.g. `ASSIST.cmd --replay <name>`.
Don't start `main.py` itself: Windows may then use another Python, drop options such as `--replay`, and use a
different Qt. ASSIST shows a red banner when that happens. Start the recorder with
`.venv\Scripts\python.exe record_status.py`; it prints a warning if started otherwise.

Tests: `.venv\Scripts\python.exe -m unittest discover -s tests`

### Why there's no .exe

A packaged program would be easier to start, but an unsigned .exe sets off SmartScreen and antivirus warnings,
and you couldn't check what's inside it. As plain Python files, everything ASSIST runs is here to read. A
packaged version may come later.

## What ASSIST reads, writes, and never does

ASSIST only reads files the game already writes for third-party tools, and only keeps what it needs.

**It reads** (from `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous`):
- `Status.json`: position, heading, altitude, body, ship/SRV state flags, the nav target and which
  panel has focus (`assist/status.py`, `RECORDED_FIELDS`). The file also holds your credit balance,
  cargo, fuel and legal state; ASSIST ignores them.
- `Journal.*.log`: only the star system name, from Location, FSDJump and CarrierJump events
  (`assist/journal.py`). Lines without a star system, including the ones with your commander name and
  account details, are skipped without being parsed.

**It writes** only in `Documents\ASSIST`:
- `active.json` and `archive\*.json`: your surveys. Each holds the system, body and POI name, the
  survey's center, its points, pings and deposits with their positions, the game times (UTC) they
  were recorded, and the ASSIST version. Nothing about you or your computer.
- `maps\*.png`: map images, only when you save one. Plain PNGs with no text metadata.
- `recordings\*.jsonl`: only when you run `record_status.py`. Each line is a time and the Status.json
  fields listed above, and the file is named by its start time in UTC. `record_status.py --full` records the
  whole file, balance and cargo included, for development only; those files end in `_full.jsonl` and shouldn't
  be sent to anyone.
- `replay\`: scratch surveys from replaying a recording.

**Settings** (hotkeys, overlay position, target ores, two checkboxes) are kept by Qt in the
registry under `HKCU\Software\ASSIST\ASSIST`.

**No network.** ASSIST never connects to anything: no updates, no telemetry, no online lookups.
None of its code imports a networking module, and `tests/test_privacy.py` checks that loading all
of ASSIST loads none. Sharing surveys means copying the files above yourself.

**Paths on screen** show your home folder as `%USERPROFILE%`, so a screenshot or a pasted log doesn't
show your Windows user name.

### Windows calls

ASSIST uses three small groups of Windows functions through Python's `ctypes`:
- **Hotkeys** (`assist/hotkeys.py`): `RegisterHotKey`, the standard way for a program to own a key
  combination. ASSIST is told only when one of its own bound keys is pressed; it can't see any other
  typing. It isn't a keyboard hook. Keys are registered only while the game is the window in front,
  and every hotkey ships unbound.
- **Overlay** (`assist/overlay.py`): `FindWindowW`, `GetForegroundWindow`, `IsIconic`,
  `GetWindowThreadProcessId`, `MonitorFromWindow` and `GetMonitorInfoW`. They find the game's window
  and its monitor so the overlay appears on it and hides when another window covers the game.
  ASSIST never sends anything to the game or reads its memory.
- **Documents folder** (`assist/paths.py`): `SHGetKnownFolderPath`, which asks Windows where
  Documents is (OneDrive may have moved it).

## Bug reports

Through GitHub Issues. What's safe to attach, and what isn't, is in [GUIDE.md](GUIDE.md#reporting-a-bug).

## Licence

GPL-3.0: see [LICENSE](LICENSE). You may use, change and share ASSIST; if you share a changed version, its source
must be shared under the same licence. Copyright (c) 2026 Armchair.

Elite Dangerous is a trademark of Frontier Developments plc. ASSIST is a fan-made tool, not affiliated with or
endorsed by Frontier.
