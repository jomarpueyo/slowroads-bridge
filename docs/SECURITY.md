# Security review

2026-09-27, commit before this file. Scope: the whole repository — the ride bridge (`bridge/`), the tools
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
| 1 | Medium | **Trainer spoofing / no device pinning.** The bridge connects to the first device that advertises the FTMS service or has "KICKR" in its name, with no pairing (FTMS is unauthenticated by design). Anyone in Bluetooth range can advertise first and drive the car. Verified bounds: the largest valid power (32 767 W) gives a 588 km/h target, which is clamped to the 125 mph limit with throttle 1.0; it never brakes or reverses. A neighbour's real KICKR could also be picked by mistake. | `bridge/ftms.py:82` | Open. Fix: save the trainer's address after the first connection and only reconnect to it (with a `--trainer` override) |
| 2 | Medium | **Mouse-wheel scroll can land in the wrong window.** Before scrolling, the bridge checks only that the *foreground* window is titled exactly "Slow Roads", then scrolls at a fixed point (250, 600). Windows sends wheel events to the window *under the cursor* (`MouseWheelRouting = 2` on the test PC), so an overlay or always-on-top window there receives the scrolls, and the bridge's limit count goes out of sync. Any window can also use the title "Slow Roads". | `bridge/limiter.py:171-193` | Open. Fix: require `WindowFromPoint(point)` → `GetAncestor(GA_ROOT)` to be the game window, and check the owning process is `slowroads.exe` |
| 3 | Low | **Path traversal through the ride marker.** The recorder reads the ride stamp from `logs/active-ride.txt` and uses it in file paths unchecked. Verified: the stamp `..\..\..\escaped` wrote `escaped.csv` outside `logs/`; the screenshot folder and the copy of the game's settings (`copytree`) follow the same path. It needs local write access to the project folder first. | `tools/ride_recorder.py:45` | Open. Fix: accept only `^\d{8}-\d{6}$` |
| 4 | Low (privacy) | **The recorder captures the whole desktop.** Every 5 s it saves a full-monitor screenshot (other apps and notifications included) and copies the game's Local Storage. `logs/` is git-ignored, so none of this is published, but it sits on disk. | `tools/ride_recorder.py:35,100` | Open. Fix: crop to the game window; note it in the README |
| 5 | Low | **`settings.json` is not validated.** A list, `null`, or text in place of numbers crashes the bridge at startup; `NaN`/`Infinity` are accepted and make the target speed NaN (the car then sits at the 5 mph limit). The file is local and user-edited. | `bridge/settings.py:20` | Open. Fix: check types, accept finite numbers only, and warn on bad values |
| 6 | Low | **Supply chain.** Dependencies are unpinned (`>=`); `vgamepad` installs from a source archive (it runs a build at install time and bundles the ViGEmBus installer); winget installs the latest versions silently with agreements pre-accepted; `setup.ps1` builds a command with `Invoke-Expression`. | `requirements*.txt`, `scripts/setup.ps1:30,60` | Open. Fix: pin versions with hashes (`pip-compile --generate-hashes`), pin winget versions, and call `& $py -m venv .venv` directly |
| 7 | Info | **CI hardening.** No explicit `permissions:` (the token uses the repository default), and actions are pinned by tag rather than commit SHA. | `.github/workflows/tests.yml` | Open. Fix: `permissions: contents: read`; pin `actions/checkout` and `actions/setup-python` by SHA |
| 8 | Info | **Testing tools only.** The OCR parser throws on a tab inside a number (`"6\t.3"`); an OCR call that times out leaves its thread behind; on-screen text is written to CSV as-is (a spreadsheet could read `=`, `+`, `-` or `@` at the start as a formula); `probe_zwift.py` writes a handshake to the trainer; `experiments.py`/`ui.py` take window focus and inject mouse and keyboard input. None of this runs in a normal ride. | `tools/` | Accepted; documented |

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
