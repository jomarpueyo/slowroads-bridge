"""Your ride plan, monthly challenge and journey: python -m bridge.plan [...]

  python -m bridge.plan                          show plan, this month's challenge and your journey
  python -m bridge.plan set tue thu sat 18:30 30 ride days, time (optional) and ride length in minutes
  python -m bridge.plan clear                    forget the plan
  python -m bridge.plan challenge rides 12       this month: 12 rides  (or: miles 100, long 60 = one 60 min ride)

The start screen shows the plan and the next planned ride; free rides use its length to nudge you to ease
off in the last minutes. Stored in logs/ridebook.json (git-ignored).
"""

import sys
from datetime import date

from . import motivation as mo


def main(argv=None) -> int:
    from .coach import Coach
    from .ridelog import LOG_DIR

    argv = sys.argv[1:] if argv is None else argv
    today = date.today()
    month = today.strftime("%Y-%m")
    if argv and argv[0] == "set":
        try:
            plan = mo.parse_plan(argv[1:])
        except ValueError as e:
            print(e)
            return 1
        mo.update_book(LOG_DIR, lambda b: b.__setitem__(
            "plan", {"days": list(plan.days), "time": plan.time, "minutes": plan.minutes}))
        print(f"plan saved: {plan.text()}")
        return 0
    if argv and argv[0] == "clear":
        mo.update_book(LOG_DIR, lambda b: b.pop("plan", None))
        print("plan cleared")
        return 0
    if argv and argv[0] == "challenge":
        try:
            mo.set_challenge(LOG_DIR, month, argv[1], float(argv[2]))
        except (IndexError, ValueError) as e:
            print(e if isinstance(e, ValueError) and str(e).startswith("challenge") else
                  "challenge: rides N | miles N | long MINUTES")
            return 1
        print(f"{today:%B} challenge set")
        return 0
    if argv:
        print(__doc__)
        return 1
    coach = Coach.from_settings(LOG_DIR)
    print(mo.plan_line(coach.plan(), today) or "plan: none (python -m bridge.plan set tue thu sat 18:30 30)")
    print(coach.challenge_text())
    print("journey: " + mo.journey_line(coach.lifetime()["miles"]))
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "plan"))
