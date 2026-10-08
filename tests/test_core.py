import json
import os

import pytest

from user_provisioning import core
from user_provisioning.core import AuditLog, ValidationError, build_plan, plan_row


def test_create_builds_argument_list(policy):
    op = plan_row(
        2,
        {
            "action": "create",
            "username": "jdoe",
            "groups": "developers;www-data",
            "shell": "/bin/bash",
            "comment": "Jane Doe",
        },
        policy,
    )
    assert op.argv == [
        "/usr/sbin/useradd",
        "--create-home",
        "--shell",
        "/bin/bash",
        "--comment",
        "Jane Doe",
        "--groups",
        "developers,www-data",
        "--",
        "jdoe",
    ]


def test_create_existing_user_is_skipped(policy):
    op = plan_row(2, {"action": "create", "username": "alice"}, policy)
    assert op.skip_reason == "user already exists"
    assert op.argv == []


def test_lock_also_expires_account(policy):
    op = plan_row(2, {"action": "lock", "username": "olduser"}, policy)
    assert op.argv == ["/usr/sbin/usermod", "--lock", "--expiredate", "1", "--", "olduser"]


@pytest.mark.parametrize(("flag", "expected"), [("yes", ["--remove"]), ("", [])])
def test_delete_remove_home_flag(policy, flag, expected):
    op = plan_row(2, {"action": "delete", "username": "contractor1", "remove_home": flag}, policy)
    assert op.argv == ["/usr/sbin/userdel", *expected, "--", "contractor1"]


def test_missing_user_is_skipped_for_lock_and_delete(policy):
    assert plan_row(2, {"action": "delete", "username": "ghost"}, policy).skip_reason


@pytest.mark.parametrize(
    "username",
    ["Root", "1abc", "a" * 33, "bad name", "evil;rm -rf /", "$(id)", "-oops", "user/../x", ""],
)
def test_invalid_usernames_rejected(policy, username):
    with pytest.raises(ValidationError, match="invalid username"):
        plan_row(2, {"action": "create", "username": username}, policy)


@pytest.mark.parametrize("name", ["root", "daemon"])
def test_system_accounts_are_protected(policy, name):
    for action in ("lock", "delete"):
        with pytest.raises(ValidationError, match="system account"):
            plan_row(2, {"action": action, "username": name}, policy)


def test_privileged_group_requires_opt_in(policy):
    row = {"action": "create", "username": "bob", "groups": "sudo"}
    with pytest.raises(ValidationError, match="privileged"):
        plan_row(2, row, policy)
    policy.allow_privileged_groups = True
    assert "--groups" in plan_row(2, row, policy).argv


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"action": "rename", "username": "bob"}, "unknown action"),
        ({"action": "create", "username": "bob", "shell": "/opt/evil/sh"}, "shell"),
        ({"action": "create", "username": "bob", "comment": "x:0:0"}, "comment"),
        ({"action": "create", "username": "bob", "comment": "a\nb"}, "comment"),
        ({"action": "create", "username": "bob", "groups": "nonexistent"}, "does not exist"),
        ({"action": "create", "username": "bob", "groups": "Bad,Group"}, "invalid group"),
    ],
)
def test_field_validation(policy, row, message):
    with pytest.raises(ValidationError, match=message):
        plan_row(2, row, policy)


def test_duplicate_users_rejected(policy):
    rows = [(2, {"action": "create", "username": "bob"}), (3, {"action": "create", "username": "bob"})]
    ops, errors = build_plan(rows, policy)
    assert len(ops) == 1
    assert "more than once" in errors[0]


def test_read_rows_requires_header(write_csv):
    with pytest.raises(ValidationError, match="missing required column"):
        core.read_rows(write_csv("foo,bar\n1,2\n"))


def test_read_rows_skips_comments_and_blank_lines(write_csv):
    rows = core.read_rows(write_csv("action,username\n# note,x\n\ncreate,bob\n"))
    assert rows == [(4, {"action": "create", "username": "bob"})]


def test_read_rows_limit(write_csv, monkeypatch):
    monkeypatch.setattr(core, "MAX_ROWS", 2)
    with pytest.raises(ValidationError, match="more than 2"):
        core.read_rows(write_csv("action,username\n" + "create,u\n" * 3))


def test_audit_log_permissions_and_content(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).write(action="create", username="bob", result="ok")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    record = json.loads(path.read_text())
    assert record["username"] == "bob"
    assert "timestamp" in record
    assert "operator" in record


def test_audit_log_refuses_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("")
    link = tmp_path / "audit.jsonl"
    os.symlink(target, link)
    with pytest.raises(OSError):
        AuditLog(link).write(action="x")
