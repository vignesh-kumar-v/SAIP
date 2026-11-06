"""What a shell hook runs after each command: `python -m saip.hooks command --exit-code N --program P --start T`.

Only a result is recorded: the exit code, whether it was git or another command, and a hash of
(program, exit code) so the same failing command shows up as a repeat. The command line, its arguments
and its output are never read or stored. The hook must never break the user's shell, so every error
here is swallowed.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time

from ..aw import DEFAULT_URL, FS, TERMINAL, ActivityWatch, AWError, bucket_id, iso

NOT_FAILURES = (0, 130)          # success and Ctrl-C


def command_event(exit_code: int, program: str, start: float, now: float) -> dict:
    sig = None
    if exit_code not in NOT_FAILURES:
        sig = hashlib.sha1(f"{program}:{exit_code}".encode()).hexdigest()[:12]
    return {"timestamp": iso(start), "duration": max(0.0, now - start),
            "data": {"exit_code": exit_code, "cmd_kind": "git" if program == "git" else "shell",
                     "error_sig": sig, "error_type": None}}


def emit_command(aw: ActivityWatch, event: dict) -> bool:
    try:
        bid = bucket_id(TERMINAL)
        aw.ensure_bucket(bid, TERMINAL[1], TERMINAL[0])
        aw.insert(bid, [event])
        return True
    except AWError:
        return False


def emit_commit(aw: ActivityWatch, now: float) -> bool:
    try:
        bid = bucket_id(FS)
        aw.ensure_bucket(bid, FS[1], FS[0])
        aw.insert(bid, [{"timestamp": iso(now), "duration": 0.0, "data": {"kind": "commit"}}])
        return True
    except AWError:
        return False


def main(argv=None) -> int:
    try:
        ap = argparse.ArgumentParser(prog="python -m saip.hooks")
        sub = ap.add_subparsers(dest="cmd", required=True)
        c = sub.add_parser("command")
        c.add_argument("--exit-code", type=int, required=True)
        c.add_argument("--program", default="")
        c.add_argument("--start", type=float, default=None)
        c.add_argument("--duration", type=float, default=0.0)
        c.add_argument("--url", default=DEFAULT_URL)
        k = sub.add_parser("commit")
        k.add_argument("--url", default=DEFAULT_URL)
        a = ap.parse_args(argv)
        now = time.time()
        if a.cmd == "commit":
            emit_commit(ActivityWatch(a.url, timeout=2.0), now)
            return 0
        start = a.start if a.start and a.start > 1e9 else now - a.duration
        emit_command(ActivityWatch(a.url, timeout=2.0), command_event(a.exit_code, a.program, start, now))
    except (Exception, SystemExit):        # never disturb the shell (argparse exits on bad arguments)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
