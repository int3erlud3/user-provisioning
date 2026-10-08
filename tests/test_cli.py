import json
import subprocess

from user_provisioning import cli, core

CSV = """action,username,groups,shell,comment,remove_home
create,jdoe,developers,/bin/bash,Jane Doe,
create,alice,,,,
lock,olduser,,,,
delete,contractor1,,,,yes
"""


def test_dry_run_is_default_and_runs_nothing(write_csv, run_mock, capsys):
    assert cli.main([str(write_csv(CSV))]) == 0
    out = capsys.readouterr().out
    assert run_mock == []
    assert "Dry-run mode" in out
    assert "[DRY-RUN] create jdoe" in out
    assert "[SKIP]    create alice" in out


def test_apply_requires_root(write_csv, run_mock, monkeypatch, capsys):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    assert cli.main([str(write_csv(CSV)), "--apply"]) == 1
    assert run_mock == []
    assert "requires root" in capsys.readouterr().err


def test_apply_executes_without_shell_and_audits(write_csv, run_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    audit = tmp_path / "audit.jsonl"
    assert cli.main([str(write_csv(CSV)), "--apply", "--audit-log", str(audit)]) == 0
    argvs = [argv for argv, _ in run_mock]
    assert [a[0] for a in argvs] == ["/usr/sbin/useradd", "/usr/sbin/usermod", "/usr/sbin/userdel"]
    for argv, kwargs in run_mock:
        assert isinstance(argv, list)
        assert kwargs.get("shell", False) is False
        assert kwargs["env"] == {"PATH": core.SAFE_PATH}
    records = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [r["result"] for r in records] == ["ok", "skipped", "ok", "ok"]


def test_validation_error_blocks_everything(write_csv, run_mock, monkeypatch, capsys):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    bad = CSV + "delete,root,,,,\n"
    assert cli.main([str(write_csv(bad)), "--apply", "--audit-log", "/dev/null"]) == 1
    assert run_mock == []
    assert "no changes made" in capsys.readouterr().err


def test_command_failure_returns_2(write_csv, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)

    def failing_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 9, stdout="", stderr="useradd: group missing")

    monkeypatch.setattr(core.subprocess, "run", failing_run)
    audit = tmp_path / "a.jsonl"
    rc = cli.main([str(write_csv("action,username\ncreate,bob\n")), "--apply", "--audit-log", str(audit)])
    assert rc == 2
    assert "rc=9" in capsys.readouterr().out
    assert json.loads(audit.read_text())["result"] == "failed"


def test_min_uid_cannot_be_lowered(write_csv):
    assert cli.main([str(write_csv(CSV)), "--min-uid", "0"]) == 1


def test_missing_csv(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope.csv")]) == 1
    assert "error" in capsys.readouterr().err
