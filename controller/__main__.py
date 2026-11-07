"""CLI.

  python -m controller run <SIM_URL> [--db state/controller.db]   the always-on loop (what run.sh starts)
  python -m controller status                                     loop health
  python -m controller why <workflow> "D3 14:00"                  why did X change, who approved, what next
  python -m controller view <workflow>                            one screen for a supervisor
  python -m controller audit <workflow>                           every decision, oldest first
"""
import argparse
import os

from . import audit
from .store import Store

DEFAULT_DB = os.environ.get("CONTROLLER_DB", "state/controller.db")


def main():
    ap = argparse.ArgumentParser(prog="controller")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("url")
    sub.add_parser("status")
    w = sub.add_parser("why")
    w.add_argument("workflow")
    w.add_argument("when")
    v = sub.add_parser("view")
    v.add_argument("workflow")
    a = sub.add_parser("audit")
    a.add_argument("workflow")
    args = ap.parse_args()
    if args.cmd == "run":
        from .loop import main as run_loop
        run_loop(args.url, args.db)
        return
    store = Store(args.db)
    if args.cmd == "status":
        print(audit.status(store))
    elif args.cmd == "why":
        print(audit.why(store, args.workflow, args.when))
    elif args.cmd == "view":
        print(audit.view(store, args.workflow))
    elif args.cmd == "audit":
        print(audit.history(store, args.workflow, 100000))


if __name__ == "__main__":
    main()
