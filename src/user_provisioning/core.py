"""Validation, planning and execution of user provisioning operations."""

from __future__ import annotations

import csv
import getpass
import grp
import json
import os
import pwd
import re
import shlex
import shutil

# Commands are always run with argument lists and a fixed PATH, never through a shell.
import subprocess  # nosec B404
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
COMMENT_RE = re.compile(r"^[\w .,'()@+-]{0,64}$")
ACTIONS = ("create", "lock", "delete")
REQUIRED_COLUMNS = {"action", "username"}
MAX_ROWS = 1000
DEFAULT_SHELL = "/bin/bash"
# Membership in these groups is equivalent to (or close to) root access.
PRIVILEGED_GROUPS = frozenset({"root", "sudo", "wheel", "adm", "admin", "docker", "lxd", "disk", "shadow"})
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
TRUE_VALUES = {"1", "true", "yes", "y"}


class ValidationError(ValueError):
    """Raised when an input row is invalid."""


@dataclass
class Operation:
    line: int
    action: str
    username: str
    argv: list[str] = field(default_factory=list)
    skip_reason: str | None = None


@dataclass
class Policy:
    min_uid: int = 1000
    allow_privileged_groups: bool = False
    allowed_shells: frozenset[str] = frozenset()


def load_allowed_shells(path: Path = Path("/etc/shells")) -> frozenset[str]:
    """Return login shells from /etc/shells plus nologin shells."""
    shells = {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            entry = raw.strip()
            if entry.startswith("/"):
                shells.add(entry)
    except OSError:
        shells.update({"/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/zsh"})
    return frozenset(shells)


def _binary(name: str) -> str:
    """Resolve a system binary from a fixed, trusted PATH (no PATH hijacking)."""
    found = shutil.which(name, path=SAFE_PATH)
    if not found:
        raise FileNotFoundError(f"{name} not found in {SAFE_PATH}")
    return found


def _user_uid(username: str) -> int | None:
    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError:
        return None


def _group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
    except KeyError:
        return False
    return True


def read_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    """Read CSV rows (with header). Returns (line number, row) tuples."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValidationError(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        rows = []
        for row in reader:
            if reader.line_num > MAX_ROWS + 1:
                raise ValidationError(f"CSV has more than {MAX_ROWS} rows")
            cleaned = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not any(cleaned.values()) or cleaned.get("action", "").startswith("#"):
                continue
            rows.append((reader.line_num, cleaned))
    return rows


def _parse_groups(line: int, raw: str, policy: Policy) -> list[str]:
    groups = [g for g in re.split(r"[;\s]+", raw) if g]
    for group in groups:
        if not USERNAME_RE.fullmatch(group):
            raise ValidationError(f"line {line}: invalid group name {group!r}")
        if group in PRIVILEGED_GROUPS and not policy.allow_privileged_groups:
            raise ValidationError(
                f"line {line}: group {group!r} is privileged (use --allow-privileged-groups)"
            )
        if not _group_exists(group):
            raise ValidationError(f"line {line}: group {group!r} does not exist")
    return groups


def _plan_create(op: Operation, row: dict[str, str], policy: Policy, exists: bool) -> Operation:
    shell = row.get("shell") or DEFAULT_SHELL
    if shell not in policy.allowed_shells:
        raise ValidationError(f"line {op.line}: shell {shell!r} is not listed in /etc/shells")
    comment = row.get("comment", "")
    if not COMMENT_RE.fullmatch(comment):
        raise ValidationError(f"line {op.line}: comment contains invalid characters")
    groups = _parse_groups(op.line, row.get("groups", ""), policy)
    if exists:
        op.skip_reason = "user already exists"
        return op
    op.argv = [_binary("useradd"), "--create-home", "--shell", shell]
    if comment:
        op.argv += ["--comment", comment]
    if groups:
        op.argv += ["--groups", ",".join(groups)]
    op.argv += ["--", op.username]
    return op


def plan_row(line: int, row: dict[str, str], policy: Policy) -> Operation:
    """Validate one CSV row and turn it into an Operation (argument list)."""
    action = row.get("action", "").lower()
    username = row.get("username", "")
    if action not in ACTIONS:
        raise ValidationError(f"line {line}: unknown action {action!r} (expected {', '.join(ACTIONS)})")
    if not USERNAME_RE.fullmatch(username):
        raise ValidationError(f"line {line}: invalid username {username!r}")

    op = Operation(line=line, action=action, username=username)
    uid = _user_uid(username)
    if action == "create":
        return _plan_create(op, row, policy, exists=uid is not None)

    # lock / delete: never touch system accounts
    if uid is None:
        op.skip_reason = "user does not exist"
        return op
    if uid < policy.min_uid:
        raise ValidationError(f"line {line}: refusing to {action} system account {username!r} (uid {uid})")
    if action == "lock":
        # --expiredate 1 also blocks SSH key logins, which --lock alone does not.
        op.argv = [_binary("usermod"), "--lock", "--expiredate", "1", "--", username]
    else:
        remove_home = row.get("remove_home", "").lower() in TRUE_VALUES
        op.argv = [_binary("userdel"), *(["--remove"] if remove_home else []), "--", username]
    return op


def build_plan(
    rows: Iterable[tuple[int, dict[str, str]]], policy: Policy
) -> tuple[list[Operation], list[str]]:
    """Validate all rows. Returns operations and a list of error messages."""
    ops: list[Operation] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line, row in rows:
        try:
            op = plan_row(line, row, policy)
        except ValidationError as exc:
            errors.append(str(exc))
            continue
        if op.username in seen:
            errors.append(f"line {line}: user {op.username!r} appears more than once")
            continue
        seen.add(op.username)
        ops.append(op)
    return ops, errors


class AuditLog:
    """Append-only JSON Lines audit log, created with mode 0600."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def write(self, **record: object) -> None:
        if self.path is None:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operator": os.environ.get("SUDO_USER") or getpass.getuser(),
            **record,
        }
        # O_NOFOLLOW: refuse to write through a symlink planted at the log path.
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")


def execute(ops: list[Operation], apply: bool, audit: AuditLog, echo=print) -> int:
    """Run (or print in dry-run mode) the planned operations. Returns failure count."""
    failures = 0
    for op in ops:
        prefix = "APPLY" if apply else "DRY-RUN"
        if op.skip_reason:
            echo(f"[SKIP]    {op.action:<6} {op.username}: {op.skip_reason}")
            audit.write(action=op.action, username=op.username, result="skipped", reason=op.skip_reason)
            continue
        echo(f"[{prefix}] {op.action:<6} {op.username}: {shlex.join(op.argv)}")
        if not apply:
            audit.write(action=op.action, username=op.username, argv=op.argv, result="dry-run")
            continue
        # Fixed binary path + validated argument list + shell=False (default).
        proc = subprocess.run(  # noqa: S603  # nosec B603
            op.argv, capture_output=True, text=True, timeout=120, check=False, env={"PATH": SAFE_PATH}
        )
        ok = proc.returncode == 0
        failures += not ok
        if not ok:
            echo(f"[FAILED]  {op.action:<6} {op.username}: rc={proc.returncode} {proc.stderr.strip()}")
        audit.write(
            action=op.action,
            username=op.username,
            argv=op.argv,
            result="ok" if ok else "failed",
            returncode=proc.returncode,
            stderr=proc.stderr.strip()[:500],
        )
    return failures
