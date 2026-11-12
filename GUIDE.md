# ASSIST player guide

How to survey a planetary mining POI with ASSIST, from setting the game up to sharing the results.
To install ASSIST, see [README.md](README.md#setting-it-up).

## Before the first flight

**Game settings.** Set Options → Graphics → Display → Fullscreen to **Borderless**, so ASSIST's overlay can sit on
top of the game. Real fullscreen hides it.

**Monitors.** Two monitors work best: the game on one, ASSIST's main window (tables, buttons, forms) on the other.
With one monitor the overlay and hotkeys still do the flying part. Use the main window between flights.

**The overlay** is a transparent, click-through layer on the game's monitor. It never takes the mouse or keyboard.
Turn it on with the **Overlay** checkbox in the status bar. **Overlay...** sets the monitor, its position and its
text size, with a live sample. It hides itself in supercruise, in the galaxy and system maps and other full-screen
views, when there's no active survey, and when another window is in front on the game's monitor.

**Hotkeys** all ship **unbound**, so ASSIST never clashes with your game bindings until you choose keys. Open
**Hotkeys...**, click a box and press a key (Ctrl, Shift and Alt combinations work, as do numpad keys). The x
clears it. A bound key stops reaching the game, so pick keys the game doesn't use. The keys only work while the game
is the window in front.

| Hotkey | What it does | Worth binding? |
|---|---|---|
| Next point / Previous point | Change the target survey point | Yes |
| Show / hide overlay | Toggle the overlay | Yes |
| Remote Mark Deposit | Mark a deposit seen on the scanner (see below) | Yes, if you mark deposits |
| Set POI radius | Open the radius form | Yes |
| Start survey | Start a survey here without deploying | Rarely needed |
| Manual ping | Log a scan by hand | Only with scan detection off, or for extra pings |

Every hotkey action also has a button in the main window.

**Scan detection.** The checkbox at the top of Hotkeys..., on by default, logs the first scan of each SRV
deployment by itself. The game reports the first trigger pull of a deployment, so it works as long as the scanner is
the first tool you fire. Turn it off if you'd rather log every scan yourself with Manual ping.

**Target ores.** Click **Define Target Ores** and tick the ores you're looking for. They're the choices offered when
you mark a deposit, numbered so you can pick one with a key press.

## Surveying a POI

ASSIST plans 19 deployment points: the center, 6 points at 3.4 km and 12 at 5.5 km. Each scan covers 2 km, so
together they cover the POI. You fly the ship from point to point, drop the Rhino at each one, scan, and board again.

1. **Target the mining signal** ("Planetary Mining Location Signal") from orbit. ASSIST uses its name for the POI,
   and targeting one you left unfinished resumes that survey.
2. **Fly to the POI's center**, hover at 30 m or lower, and deploy the Rhino. The first deployment starts the
   survey, and that spot becomes its center.
3. **Scan once** with the deposit scanner, without driving. ASSIST logs the scan at the SRV's position.
4. **Board the ship.** ASSIST picks the nearest point not yet done. Click a row in the Survey points table, or use
   Next / Previous point, to choose another.
5. **Follow the overlay** to the point:
   - row 1: the target and its distance;
   - row 2: turn arrows with the compass bearing between them. An arrow turns green when you should turn that way;
   - row 3: PITCH, the angle down to the target from the horizon;
   - row 4: **DEPLOY READY** (green) inside the 500 m landing circle, or **MISS** (amber) when you're hovering low
     outside it but close enough that deploying still counts.

   Near the point, a fine-tune cross can help with the last few meters (Overlay..., below 150 m).
6. **Repeat** until the overlay says "All points done or skipped", then click **Close survey**.

**Set the POI radius early.** Drive the SRV to the golden ring, read the range to the POI center from the HUD, and
press Set POI radius (or the button). Until then ASSIST assumes 7 km. With the radius known, it skips points that
would add less than 1% coverage, which saves whole trips on small POIs. You can skip or unskip any point yourself
with **Skip / unskip target**. ASSIST never overrides your choice.

**Extra scans.** Use Manual ping. **Undo last ping** and **Delete selected** fix mistakes. **Assign to target** makes
a selected ping count for the current point, however far off it landed.

**Leaving and coming back.** Supercruise, another body, or flying more than 10 km away archives the survey as
unfinished. Deploy near it again, or target its mining signal, and it carries on. Logging out or losing the SRV
doesn't end a survey.

**Custom destination.** Custom destination... (or right-click the map) guides you to any latitude and longitude, with
a dashed circle on the map showing what a scan there would cover. It's a navigation aid only: nothing is recorded.

## Deposits

**Marking one from a distance.** When the scanner shows a deposit, stop with the handbrake, pointing the SRV at it.
Press Remote Mark Deposit: ASSIST takes your position and heading at that moment. Then:
1. press the ore's number;
2. press H, M or L for the density (optional);
3. type the range shown in the game, such as `1.51 km` or `41 m`, and press Enter.

ASSIST works out the deposit's estimated position. The same ore within 150 m of an existing deposit is treated as
that deposit, and its position is averaged. Between 150 and 400 m you get a warning, since real deposits are always
more than 400 m apart.

**Later**, select the deposit and press **Navigate to selected** to be guided to it. Once there, **Update size /
location** records its size (1-6) and the position read off the HUD, which then counts as measured. Right-click a
deposit for the same two actions. **Manual Mark Deposit** adds a deposit by typed latitude and longitude, with no
duplicate check, for deposits close together.

## Maps and past surveys

**Map...** draws the survey: the golden ring, scanner coverage with the percentage covered, the planned points,
pings, deposits and a latitude and longitude grid. It updates live. Right-click it to navigate somewhere. **Save
image...** writes a PNG to `Documents\ASSIST\maps`.

**Surveys...** lists every archived survey, closed and unfinished, newest first, with filters by body and system.
From there you can re-open a closed survey, close an unfinished one, open its map, and fill in deposit sizes and
positions from anywhere.

## Sharing surveys

Sharing is copying files. Your surveys are in `Documents\ASSIST\archive`, one `.json` file per POI, and your saved
maps are in `Documents\ASSIST\maps`. Send them any way you like. To use a survey you were sent, put its `.json` file
in your own `archive` folder. It shows up the next time Surveys... opens. If it's unfinished, it resumes when you
deploy near its center.

A survey file holds only game data: the POI, the points, pings and deposits, and their game times (UTC). Nothing
about you or your computer.

## Reporting a bug

Open an issue on the project's GitHub page, under **Issues**. Say what you did, what you expected and what happened,
and which ASSIST version (it's in the window title).

**Safe to attach:**
- survey files (`Documents\ASSIST\archive\*.json`, `active.json`) and saved map images;
- a recording made by `record_status.py` **without** `--full`. To make one, from the ASSIST folder run
  `.venv\Scripts\python.exe record_status.py`, reproduce the problem in the game, then press Ctrl+C. The file is in
  `Documents\ASSIST\recordings`. It holds only the fields ASSIST reads, and its name is in UTC.

**Don't attach:**
- recordings ending in **`_full.jsonl`**. They hold your credit balance, cargo, fuel and legal state;
- anything from the game's own folder (`Saved Games\Frontier Developments`). Journal files name your commander.

**Before pasting or uploading:**
- **Error text:** if ASSIST crashes, Python's error text names the folder ASSIST is in. If that's under your user
  folder, it includes your Windows user name: replace it with `%USERPROFILE%`.
- **Screenshots:** ASSIST's tables show times in your local time, which, next to the game's UTC clock, tells
  people your time zone. Crop them if that matters to you.
