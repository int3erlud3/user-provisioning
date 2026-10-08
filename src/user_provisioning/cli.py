"""Command line interface for user-provisioning."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .banner import maybe_print_banner, maybe_print_banner_for_info
from .core import AuditLog, Policy, ValidationError, build_plan, execute, load_allowed_shells, read_rows

DEFAULT_AUDIT_LOG = Path("/var/log/user-provisioning.jsonl")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="user-provisioning",
        description="Create, lock and delete Linux users from a CSV file. Dry-run unless --apply is given.",
    )
    p.add_argument(
        "csv", type=Path, help="CSV file with columns action,username[,groups,shell,comment,remove_home]"
    )
    p.add_argument("--apply", action="store_true", help="actually execute the changes (requires root)")
    p.add_argument(
        "--audit-log",
        type=Path,
        help=f"JSON Lines audit log (default with --apply: {DEFAULT_AUDIT_LOG}; dry-run: none)",
    )
    p.add_argument("--min-uid", type=int, default=1000, help="never lock/delete users below this UID")
    p.add_argument(
        "--allow-privileged-groups",
        action="store_true",
        help="allow adding users to sudo/wheel/docker/adm/... groups",
    )
    p.add_argument(
        "--no-banner", action="store_true", help="do not print the startup banner (or set NO_BANNER=1)"
    )
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    maybe_print_banner_for_info(argv)
    args = build_parser().parse_args(argv)
    maybe_print_banner(args.no_banner)
    if args.min_uid < 1000:
        print("error: --min-uid below 1000 is not allowed", file=sys.stderr)
        return 1
    if args.apply and os.geteuid() != 0:
        print("error: --apply requires root privileges (run with sudo)", file=sys.stderr)
        return 1

    policy = Policy(
        min_uid=args.min_uid,
        allow_privileged_groups=args.allow_privileged_groups,
        allowed_shells=load_allowed_shells(),
    )
    try:
        ops, errors = build_plan(read_rows(args.csv), policy)
    except (OSError, ValidationError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if errors:
        # Fail closed: nothing is executed if any row is invalid.
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        print(f"{len(errors)} validation error(s); no changes made.", file=sys.stderr)
        return 1

    audit_path = args.audit_log or (DEFAULT_AUDIT_LOG if args.apply else None)
    if not args.apply:
        print("Dry-run mode: no changes will be made. Re-run with --apply to execute.")
    try:
        failures = execute(ops, apply=args.apply, audit=AuditLog(audit_path))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{len(ops)} operation(s) processed, {failures} failure(s).")
    return 2 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
