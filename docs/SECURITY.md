# Security review

2026-09-27. All findings fixed the same day (see the Status column). Scope: the whole repository — the ride bridge (`bridge/`), the tools
(`tools/`), the launchers and setup script, the CI workflow and the git history. Method: threat modelling of
every untrusted input, fuzzing, verified exploit attempts where it was safe to try, a dependency audit, and a
search of the history for secrets and personal data.

**Threat model.** A local Windows hobby tool. It opens no network ports and needs no accounts or secrets.
Its untrusted inputs are: Bluetooth advertisements and packets from any device in range (about 10 m),
text read off the screen (OCR, testing tools only), the local `settings.json` and `logs/active-ride.txt`,
and the command line. What it can affect: a virtual Xbox controller, the mouse cursor and wheel, and files
in the project folder.

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
