# Security review

2026-09-27, updated 2026-10-06 for everything added since (see [Update 2026-10-06](#update-2026-10-06)). All
findings are fixed (see the Status column). Scope: the whole repository — the ride bridge (`bridge/`), the tools
(`tools/`), the launchers and setup script, the CI workflow and the git history. Method: threat modelling of
every untrusted input, fuzzing, verified exploit attempts where it was safe to try, a dependency audit, and a
search of the history for secrets and personal data.

**Threat model.** A local Windows hobby tool. It opens no network ports and needs no accounts or secrets.
Its untrusted inputs are: Bluetooth advertisements and packets from any device in range (about 10 m),
text read off the screen (OCR, testing tools only), the local `settings.json`, `logs/active-ride.txt` and
`logs/ridebook.json`, the game's own saved settings (read-only, for the road surface), and the command
line. What it can affect: a virtual Xbox controller, the mouse cursor and wheel, **the trainer's resistance**
(since 2026-10-05), and files in the project folder.

## Findings

| # | Severity | Finding | Where | Status |
| --- | --- | --- | --- | --- |
| 1 | Medium | **Trainer spoofing / no device pinning.** The bridge connects to the first device that advertises the FTMS service or has "KICKR" in its name, with no pairing (FTMS is unauthenticated by design). Anyone in Bluetooth range can advertise first and drive the car. Verified bounds: the largest valid power (32 767 W) gives a 588 km/h target, which is clamped to the 125 mph limit with throttle 1.0; it never brakes or reverses. A neighbour's real KICKR could also be picked by mistake. | `bridge/ftms.py` | **Fixed.** The first connection pairs and saves the address to `settings.json`; after that only that address is accepted (`trainer_filter`). Pairing requires the FTMS service UUID; name-only matching needs an explicit `--name`. `--trainer pair` re-pairs, `--trainer AA:BB:…` sets one. The probe tools use the paired trainer too. Tests: `test_security.py` (an imposter with FTMS and the right name is rejected) |
| 2 | Medium | **Mouse-wheel scroll can land in the wrong window.** Before scrolling, the bridge checks only that the *foreground* window is titled exactly "Slow Roads", then scrolls at a fixed point (250, 600). Windows sends wheel events to the window *under the cursor* (`MouseWheelRouting = 2` on the test PC), so an overlay or always-on-top window there receives the scrolls, and the bridge's limit count goes out of sync. Any window can also use the title "Slow Roads". | `bridge/gamewin.py`, `bridge/limiter.py` | **Fixed.** The game window is found by process (`slowroads.exe`), not title. Each notch is sent only if the foreground window is the game and `WindowFromPoint` → `GetAncestor(GA_ROOT)` at the scroll point is the game. Six candidate points are tried, and if all are covered nothing is sent. A notch counts only if delivered; interrupted homing re-homes later. Verified live: with File Explorer over the old fixed point, no safe point was found (the old code would have scrolled Explorer). In-game sim: limit matched in 11/11 samples. Tests: covered window, focus lost, interrupted homing |
| 3 | Low | **Path traversal through the ride marker.** The recorder reads the ride stamp from `logs/active-ride.txt` and uses it in file paths unchecked. Verified: the stamp `..\..\..\escaped` wrote `escaped.csv` outside `logs/`; the screenshot folder and the copy of the game's settings (`copytree`) follow the same path. It needs local write access to the project folder first. | `tools/ride_recorder.py` | **Fixed.** The stamp must match `^\d{8}-\d{6}$` (read at most 64 chars); anything else and the recorder exits. Tests: traversal strings rejected |
| 4 | Low (privacy) | **The recorder captures the whole desktop.** Every 5 s it saves a full-monitor screenshot (other apps and notifications included) and copies the game's Local Storage. `logs/` is git-ignored, so none of this is published, but it sits on disk. | `tools/ride_recorder.py` | **Fixed.** Screenshots are cropped to the game window and taken only while it's in front; never the desktop or other apps. The game-settings copy stays (it's the game's own settings) and is noted in the README |
| 5 | Low | **`settings.json` is not validated.** A list, `null`, or text in place of numbers crashes the bridge at startup; `NaN`/`Infinity` are accepted and make the target speed NaN (the car then sits at the 5 mph limit). The file is local and user-edited. | `bridge/settings.py` | **Fixed.** A non-object file or section, wrong types, booleans and NaN/±Infinity are ignored with a warning; saving writes finite numbers only and keeps other sections. Tests: 9 malformed files, non-finite values |
| 6 | Low | **Supply chain.** Dependencies are unpinned (`>=`); `vgamepad` installs from a source archive (it runs a build at install time and bundles the ViGEmBus installer); winget installs the latest versions silently with agreements pre-accepted; `setup.ps1` builds a command with `Invoke-Expression`. | `requirements*.in/.txt`, `scripts/setup.ps1` | **Fixed.** Three hash-locked files (`requirements.txt`, `-calibration.txt`, `-test.txt`) from `.in` sources, installed with `pip --require-hashes`. winget versions are pinned (Python 3.13.15, Git 2.55.0.3, ViGEmBus 1.22.0; `-AllowLatest` to override). `Invoke-Expression` is replaced by a direct call, and the unpinned `pip --upgrade` step is removed. Verified: a fresh hash-checked install plus the full setup run pass. Locking also found that `speedo.py` relied on a package only bleak pulled in; it's now declared. `vgamepad` still builds from its (now hash-pinned) source archive |
| 7 | Info | **CI hardening.** No explicit `permissions:` (the token uses the repository default), and actions are pinned by tag rather than commit SHA. | `.github/workflows/tests.yml` | **Fixed.** `permissions: contents: read`, `persist-credentials: false`, actions pinned to commits (`checkout` 11d5960a…, `setup-python` a26af69b…), and hash-locked installs. Reproduced locally: 78 passed, 0 skipped |
| 8 | Info | **Testing tools only.** The OCR parser throws on a tab inside a number (`"6\t.3"`); an OCR call that times out leaves its thread behind; on-screen text is written to CSV as-is (a spreadsheet could read `=`, `+`, `-` or `@` at the start as a formula); `probe_zwift.py` writes a handshake to the trainer; `experiments.py`/`ui.py` take window focus and inject mouse and keyboard input. None of this runs in a normal ride. | `tools/` | **Fixed.** OCR numbers no longer span tabs; OCR stops starting threads after 3 stay hung; OCR text is neutralised (`csv_safe`) before it's written to CSV; `probe_zwift.py` refuses to run without `--handshake`; `experiments.py` announces the takeover with a 3 s cancel window. The UI injection itself is the purpose of those testing tools |
| 9 | Low (safety) | **ERG targets follow an unchecked FTP.** Workout targets are a share of your FTP, and FTP comes from `--ftp`, `settings.json` or the ramp test with no range check. A typo such as `"ftp": 2500` (for 250) made the trainer hold targets up to its 2000 W maximum; a negative FTP gave 0 W. The rider is never trapped: ERG lets go when you stop pedalling or drop under 45 rpm for 3 s. Found in the 2026-10-06 review. | `bridge/coach.py`, `bridge/trainer.py` | **Fixed.** A set FTP outside 40-600 W is ignored (the estimate from past rides is used instead), and no ERG target above 1000 W is ever sent, whatever the workout asks. Test: `test_out_of_range_ftp_is_ignored_and_erg_is_capped` |

## Re-test after fixes

- **Suite:** 78 tests pass (15 new security regression tests in `tests/test_security.py`), locally and in a
  fresh CI-equivalent environment with hash-locked installs.
- **Setup:** `scripts/setup.ps1` runs end to end in hash-checked mode (6/6 environment checks, tests pass).
- **In game:** a limit-mode `--sim` ride through the new window checks held 10 → 40 mph; the displayed
  limit matched in 11/11 samples; coasting unchanged.
- **Pairing with the real trainer (confirmed live):** the first ride paired and saved the KICKR
  (`paired with KICKR CORE … saved to settings.json`); the next run scanned only for that address and
  connected; the ride settings in the same file were kept. Negative test: pinned to a made-up address, the
  bridge logged `no trainer found` and never connected to the real, awake KICKR.

## Checked and passed

- **Fuzzing (no crashes, no hangs):** 300 000 random FTMS packets (133 304 parsed, 166 696 rejected
  cleanly as malformed), 100 000 random Zwift protobuf messages, 100 000 random OCR strings, hostile
  settings files and `--sim` profiles. The whole control chain stays within bounds for any valid int16
  power: limit 5–125, throttle 0–1, never negative speed.
- **ReDoS:** OCR regexes on 50 000-character adversarial inputs take ≤ 80 ms.
- **Dependencies:** `pip-audit` finds no known vulnerabilities in any of the 23 installed packages
  (bleak 3.0.2, vgamepad 0.1.0, pillow 12.3.0, mss 10.2.0, winrt 3.2.1, pytest 9.1.1, …).
- **Dangerous calls:** no `eval`/`exec`, pickle, `shell=True` or network listeners. The only subprocesses
  are two fixed-argument calls in `tools/check_env.py`.
- **Secrets:** none in any commit (searched for API keys, tokens, passwords, private keys, GitHub/AWS key
  formats).
- **Personal data:** commit authors are rewritten to the GitHub no-reply address, and the trainer's MAC
  address, the device ID and a private document link are removed from every commit. Rides, screenshots and
  personal settings are git-ignored.
- **Safety limits:** the bridge never holds the brake at a stop (Slow Roads would reverse); a stale trainer
  (no data for 3 s) sets throttle to 0.

## Update 2026-10-06

A second pass over everything added since the first review: crash reports and `report.bat`, sounds and
hotkeys, auto-end and game auto-start, log cleanup, the overlay, the coach (ride book, workouts, dashboard),
the motivation extras (plan, journey, ghost, share card) and, most important, **trainer control**
(`bridge/trainer.py`) and **reading the game's road** (`bridge/gamestate.py`). One new finding (9, fixed).
The rest checked out:

- **Trainer control (new: the bridge now writes to the trainer).** Only the pinned trainer (finding 1) is
  ever controlled, and `--dry-run` never sends resistance. Every value is clamped before it is sent:
  ERG 0-1000 W (finding 9), and the road simulation's grade ±40 %, wind ±32 m/s, Crr 0-0.0255 and Cw
  0-2.55 kg/m. Writes are rate-limited (ERG 1 s, simulation 0.5 s); a refused control request backs off
  15 s and only logs. ERG lets go when you stop pedalling, under 45 rpm for 3 s, in rest blocks and while
  paused. The trainer is reset at the end of every ride, including Ctrl+C and crashes (in a `finally`,
  with a 3 s timeout so shutdown never hangs). `--no-resistance` turns all of it off.
- **Live ride 2026-10-06 17:30 (43 min, long-ride workout):** control granted once, 26 ERG and 27 road-feel
  changes as you stopped and started pedalling, the dirt road picked up from the game as gravel, no
  warnings, and `trainer released (reset: success)` at the end.
- **Reading the game's road.** Read-only, from the game's own Local Storage folder: files over 8 MB are
  skipped, only a bounded pattern (one object of at most 400 printable characters after a 13-digit
  timestamp) is parsed with `json.loads`, and anything unexpected means "unknown road". The game's memory
  and files are never touched.
- **Ride book and plan (`logs/ridebook.json`, `logs/ride-index.json`).** Malformed files, wrong types and
  out-of-range values (feel outside 1-5, unknown challenge kinds) are ignored. Notes you type are
  HTML-escaped on the dashboard (`dashboard.py`), which has no scripts and makes no network requests.
- **Crash reports and `report.bat`.** Bluetooth addresses, e-mail addresses, user, home folder and computer
  name are redacted. Screenshots, the game's settings and `settings.json` are never included.
- **Hotkeys.** Only F6-F10 are polled (`GetAsyncKeyState`), only while the game is in front; nothing is
  hooked or recorded, and the keys still reach the game.
- **Overlay.** A click-through, never-focused window over the game; it shows trainer data only.
- **Share card.** Shows ride numbers, journey and streak only: no name, trainer or location.
- **Log cleanup.** Deletes only entries in `logs/` with a ride timestamp in the name, older than
  `keep_days`; ride CSVs and summaries are always kept, the current ride is skipped, and symlinks and
  junctions are never followed.
- **Game auto-start and opening files.** `os.startfile` opens only a fixed `steam://rungameid/3431300` URL
  and files the bridge just wrote in `logs/`. `report.py` starts Explorer with a fixed argument list.
- **The ride window (`bridge/app.py`, added the same day).** Built on tkinter from the standard library:
  no web server, no listening port, no browser, no network, and no new package. The ride runs on a worker
  thread and the window only reads its status and can ask it to end; drawing errors mid-ride go to a crash
  report and never stop the ride, so the trainer is always handed back. The hidden recorder is started with
  a fixed argument list (the venv's own `pythonw.exe` and `tools/ride_recorder.py`, no shell,
  `CREATE_NO_WINDOW`), and only for real rides. A named mutex allows one window at a time, so two bridges
  can't fight over the trainer and the game. Closing the window mid-ride asks first and ends the ride
  through the same path as the auto-end; the process waits up to 10 s for the trainer to be released. The
  desktop shortcuts point at the venv's `pythonw.exe` with fixed arguments; the icon is a file in the repo.
- **Dependencies and CI.** No new packages and no changes to the lock files or the workflow since the
  first review. `pip-audit` was not re-run this time.
- **Suite:** 221 tests pass.
