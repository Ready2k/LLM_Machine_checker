#!/usr/bin/env python3
"""Turn reported auto-router misroutes into regression tests.

    python scripts/autoroute_feedback.py list                 # pending reports
    python scripts/autoroute_feedback.py promote 3 7          # -> tests/test_autoroute.py
    python scripts/autoroute_feedback.py promote --all
    python scripts/autoroute_feedback.py promote 4 --text "a prompt that shows the problem"
    python scripts/autoroute_feedback.py dismiss 5

Reports come from the chat UI ("wrong route?") or the dashboard's Auto-Router tab
(POST /api/router/feedback) and live in the telemetry SQLite DB on the gateway
host. `promote` appends `(prompt, EXPECTED_ROUTE)` to FEEDBACK_CORPUS in
tests/test_autoroute.py; the new case fails until you tune config/autoroute.yaml
or gateway/policy/autoroute.py. Promoting or dismissing deletes the stored
prompt text from the DB. Reports without stored text (dashboard reports, anything
involving the private route) need --text: write a synthetic prompt with the same
shape -- never paste real sensitive data into a committed test file.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.policy.autoroute import classify  # noqa: E402

DEFAULT_DB = ROOT / "data" / "telemetry" / "observability.db"
DEFAULT_TESTS = ROOT / "tests" / "test_autoroute.py"
END_MARKER = "# feedback-corpus-end"
ROUTE_CONSTANTS = {"local-fast": "FAST", "local-quality": "QUALITY", "private-docs": "PRIVATE"}


def corpus_line(text: str, desired: str, comment: str) -> str:
    comment = " ".join(comment.split())
    return f"    ({text!r}, {ROUTE_CONSTANTS.get(desired, repr(desired))}),  # {comment}\n"


def add_to_corpus(tests_path: Path, text: str, desired: str, comment: str) -> bool:
    """Insert a case before the end marker. False if that prompt is already there."""
    src = tests_path.read_text(encoding="utf-8")
    if END_MARKER not in src:
        raise SystemExit(f"{tests_path}: marker '{END_MARKER}' not found")
    if f"    ({text!r}, " in src:
        return False
    head, _, tail = src.partition("    " + END_MARKER)
    tests_path.write_text(head + corpus_line(text, desired, comment) + "    " + END_MARKER + tail, encoding="utf-8")
    return True


def current_route(text: str) -> str:
    return classify([{"role": "user", "content": text}]).route


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"No telemetry DB at {db_path} (run this on the gateway host, or pass --db)")
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def cmd_list(con, args) -> int:
    where = "" if args.all else "WHERE status = 'new'"
    try:
        rows = con.execute(f"SELECT * FROM router_feedback {where} ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        print("No feedback entries." if args.all else "No pending feedback (use --all for history).")
        return 0
    for r in rows:
        conf = f" conf={r['confidence']:.2f}" if r["confidence"] is not None else ""
        print(f"#{r['id']}  {r['created_at'][:19]}  {r['actual_route']} -> should be {r['desired_route']}"
              f"  [{r['status']}]{conf}  prompt: {'stored' if r['prompt_text'] else 'not stored (needs --text)'}")
        if r["reason"]:
            print(f"     reason: {r['reason']}")
        if r["note"]:
            print(f"     note:   {r['note']}")
        if args.show_text and r["prompt_text"]:
            print(f"     text:   {r['prompt_text']!r}")
    return 0


def cmd_promote(con, args) -> int:
    if args.text and (args.all or len(args.ids) != 1):
        raise SystemExit("--text needs exactly one id")
    if args.all:
        rows = con.execute("SELECT * FROM router_feedback WHERE status = 'new' ORDER BY id").fetchall()
    else:
        rows = [con.execute("SELECT * FROM router_feedback WHERE id = ?", (i,)).fetchone() for i in args.ids]
        missing = [i for i, r in zip(args.ids, rows) if r is None]
        if missing:
            raise SystemExit(f"No such feedback id: {', '.join(map(str, missing))}")
    if not rows:
        print("Nothing to promote.")
        return 0

    added = skipped = 0
    for r in rows:
        text = args.text or r["prompt_text"]
        if not text:
            print(f"#{r['id']}: no prompt stored -- rerun with: promote {r['id']} --text \"<representative prompt>\"")
            skipped += 1
            continue
        now = current_route(text)
        comment = f"fb#{r['id']} {r['created_at'][:10]} was {r['actual_route']}" + (f": {r['note']}" if r["note"] else "")
        if args.dry_run:
            print(f"#{r['id']} would add: {corpus_line(text, r['desired_route'], comment).strip()}")
        elif add_to_corpus(args.tests, text, r["desired_route"], comment):
            added += 1
            print(f"#{r['id']} added -> expects {r['desired_route']}")
        else:
            print(f"#{r['id']} already in the corpus")
        if now == r["desired_route"]:
            print("     already routes correctly on its own (fixed since, or it depended on conversation "
                  "context/tools) -- kept as a guard.")
        else:
            print(f"     currently routes to {now}: the test fails until the classifier is tuned.")
        if not args.dry_run:
            con.execute("UPDATE router_feedback SET status = 'promoted', prompt_text = NULL WHERE id = ?", (r["id"],))
    con.commit()
    if added:
        print(f"\n{added} case(s) added to {args.tests}. Next: pytest tests/test_autoroute.py -k reported_misroutes")
    return 1 if skipped and not added and not args.dry_run else 0


def cmd_dismiss(con, args) -> int:
    for i in args.ids:
        n = con.execute("UPDATE router_feedback SET status = 'dismissed', prompt_text = NULL WHERE id = ?", (i,)).rowcount
        print(f"#{i} dismissed" if n else f"#{i} not found")
    con.commit()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Auto-router feedback -> regression tests")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="telemetry SQLite DB")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="show reported misroutes")
    ls.add_argument("--all", action="store_true", help="include promoted/dismissed")
    ls.add_argument("--show-text", action="store_true", help="print stored prompt text")
    pr = sub.add_parser("promote", help="append entries to the test corpus")
    pr.add_argument("ids", nargs="*", type=int)
    pr.add_argument("--all", action="store_true", help="every pending entry")
    pr.add_argument("--text", help="prompt to use (required when none was stored)")
    pr.add_argument("--tests", type=Path, default=DEFAULT_TESTS)
    pr.add_argument("--dry-run", action="store_true")
    ds = sub.add_parser("dismiss", help="mark entries as not-a-misroute")
    ds.add_argument("ids", nargs="+", type=int)
    args = p.parse_args(argv)
    if args.cmd == "promote" and not args.all and not args.ids:
        p.error("promote needs ids or --all")
    con = _connect(args.db)
    try:
        return {"list": cmd_list, "promote": cmd_promote, "dismiss": cmd_dismiss}[args.cmd](con, args)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
