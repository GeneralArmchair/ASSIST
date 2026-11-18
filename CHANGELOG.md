# Changelog

What changed in each version of ASSIST, newest first. How to use each feature is in [GUIDE.md](GUIDE.md).

Version numbers are `0.<milestone>.<fix>` during field testing; 1.0.0 comes after it. 0.13.0 was the first public
release. The versions before it were built and tested in the game by the author and are listed so the history is
complete, without dates.

## 0.15.0

- **Preferences...**, a new button next to Hotkeys... in the status bar, opens a dialog with its settings sorted into
  categories, listed on the left. The first one is **Survey Settings**. More categories will follow in later versions.
- The **"Detect the first scan of each SRV deployment automatically"** checkbox has moved from Hotkeys... to
  Preferences..., under Survey Settings. Your choice is kept: it's the same setting, only in a new place.
- This changelog.

## 0.14.0 (2026-11-13)

Changes asked for by field testers.

- **Remote Mark Deposit:** the range can be typed in km or m. A km / m dropdown after the box picks the unit. It's km
  by default and remembers the last unit chosen. Enter still saves before reaching the dropdown, so the keyboard flow
  (hotkey, ore number, H/M/L, range, Enter) is unchanged. A range under 10 m asks you to press Save again ("did you
  mean km?"), and a range over 20 km suggests m.
- **Manual Mark Deposit** in the main window opens already filled in with your current position, and a line under it
  says where you were (SRV, ship or on foot). Saved unchanged, the deposit counts as measured. If you type over the
  numbers, it's saved as an estimate, as before.
- **Within 150 m of a deposit of the same ore,** Manual Mark Deposit asks whether to update that deposit instead
  (probably the same one, first marked from a distance). The choices are Update, Add new deposit (for real twins)
  and Back to the form.
- **Guide corrections:** ranges are typed as a plain number, and a sighting of the same ore within 400 m is never
  saved. Nothing is averaged.

## 0.13.0 (2026-11-12)

First public release, for field testing.

- **Setup.cmd** creates the project's own Python environment and installs the pinned, hash-checked Qt. That
  download is the only time anything goes online. **ASSIST.cmd** starts ASSIST with that environment.
- **GUIDE.md**, the player guide, and a bug report template saying what's safe to attach.
- Recordings made with `record_status.py` are named in UTC, so a shared recording doesn't reveal your time zone.
- Licensed under GPL-3.0.

## 0.12.0

- **Right-click a deposit** in the main window's Deposits table for "Navigate to selected" and "Update size /
  location". The Deposits table in Surveys... offers "Update size / location". There's no Delete in the menus,
  because it would be too easy to hit by mistake.

## 0.11.0

- **Privacy review.** Recordings keep only the Status.json fields ASSIST uses. `--full` records everything, with a
  warning, in a file ending `_full`. Error messages no longer show full paths. The README explains what ASSIST reads,
  writes and never does. Python requirements are pinned by hash.
- **Received survey files:** the archive folder is read again whenever Surveys... opens and when you deploy with no
  active survey. A survey someone sent you then resumes when you deploy near its center. If two files hold the same
  survey, the most recently updated one is used and the log says so.
- **Started the wrong way?** If ASSIST isn't running on the project's own Python, a red banner says so and how to fix
  it.

## 0.10.1

- Removed the **"Count as ping"** button, which caused confusion. A deployment with no detected scan is still logged
  as "no ping detected" and its point is picked again. Redeploying and scanning there replaces the entry.

## 0.10.0

- **System filter in Surveys...:** type or pick a system name to see its surveys without travelling there. An exact
  name shows just that system, and part of a name shows every match ("tascheter" finds the whole sector).
  Right-click a row and choose "Show only <system>" to fill it in.

## 0.9.0

- **Automatic scan detection can be turned off** in Hotkeys..., so that only Manual ping logs scans.
- **Scan circle:** the live map shows the 2 km a scan at your custom destination would cover.
- Recordings moved to `Documents\ASSIST\recordings`, beside the surveys and maps.
- Archived survey files are named like the map images (body, POI, date), so they're easy to find and share.

## 0.8.0

- **Custom destination:** right-click the live map ("Navigate here") or type a latitude and longitude, and ASSIST
  guides you there like any survey point. It's a navigation aid only and isn't saved.

## 0.7.0

- **Map window:** the golden ring, the ground your scans covered, survey points and deposits, with a latitude and
  longitude grid and a legend of every deposit. **Save image** writes a PNG.
- **Coverage percentage,** worked out from your scans and the POI's radius. Until you measure the radius, a 7 km
  radius is assumed, and the percentage says so.
- **Set POI radius** (button and hotkey): type the golden ring's range off the HUD.
- **Automatic skipping:** once the radius is known, survey points that would add under 1% coverage are skipped.
  Points you skip or unskip yourself are left alone.
- **Deposit density** (High, Medium, Low), picked with H, M or L while marking.
- Skipped points are greyed out and struck through in the points table.

## 0.6.0

- **Surveys...** lists every archived survey, with filters for this system or body, ore and minimum size. You can
  fill in deposit sizes and positions or add deposits for any survey, from anywhere.
- **Closed surveys** (formerly "finished") can be **re-opened**, and they resume when you return to the POI.
- Version numbers, shown in the window title.

## Before 0.6.0 (no version numbers)

The first five milestones, built and tested before builds were numbered:

- **Survey pattern and tracking:** a 19-point pattern around the first deployment, in two tiers. Scans are detected
  from the SRV deployment and logged automatically. Each next point is picked on boarding. Surveys are archived on
  leaving and resumed on return.
- **Guidance:** turn direction with the target's compass bearing, distance, and pitch to the target. The deploy cue
  appears inside the 500 m landing circle.
- **Overlay** on the game's monitor: click-through, with the target, turn arrows, pitch, "DEPLOY READY" and "MISS".
  It also has an optional fine-tune cross and a "TIER 1 COMPLETE" notice. Rebindable global hotkeys, all unbound by
  default.
- **Deposits:** Remote Mark Deposit by heading and range, Manual Mark Deposit by latitude and longitude, Target Ores
  with numbered keys, size and measured position, and guidance to any deposit. Duplicates of the same ore within
  400 m are not saved.
