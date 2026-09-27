"""Report whether everything the bridge needs is present. Prints one line per check; exit 1 on any FAIL."""

import importlib
import platform
import subprocess
import sys


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    return ok


def main() -> int:
    results = [check("python", sys.version_info >= (3, 10), platform.python_version())]
    for mod in ("bleak", "vgamepad", "pytest"):
        try:
            m = importlib.import_module(mod)
            results.append(check(mod, True, getattr(m, "__version__", "")))
        except Exception as e:  # vgamepad raises if the ViGEmBus driver is missing
            results.append(check(mod, False, f"{type(e).__name__}: {e}"))
    svc = subprocess.run(["sc", "query", "ViGEmBus"], capture_output=True, text=True)
    results.append(check("ViGEmBus service", "RUNNING" in svc.stdout, "running" if "RUNNING" in svc.stdout else svc.stdout.strip()[-80:]))
    bt = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue | Measure-Object).Count"],
        capture_output=True, text=True,
    )
    count = int(bt.stdout.strip() or 0)
    results.append(check("bluetooth adapter", count > 0, f"{count} devices OK"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
