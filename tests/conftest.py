"""Shared fixtures: a fake user/group database and a mocked subprocess.run."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from user_provisioning import core

FAKE_USERS = {"root": 0, "daemon": 1, "alice": 1001, "olduser": 1002, "contractor1": 1003}
FAKE_GROUPS = {"developers", "www-data", "sudo", "docker"}


@pytest.fixture(autouse=True)
def fake_system(monkeypatch):
    def getpwnam(name):
        if name not in FAKE_USERS:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=FAKE_USERS[name])

    def getgrnam(name):
        if name not in FAKE_GROUPS:
            raise KeyError(name)
        return SimpleNamespace(gr_name=name)

    monkeypatch.setattr(core.pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(core.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(core.shutil, "which", lambda name, path=None: f"/usr/sbin/{name}")
    monkeypatch.setattr(core, "load_allowed_shells", lambda *a: frozenset({"/bin/bash", "/usr/sbin/nologin"}))


@pytest.fixture
def run_mock(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def policy():
    return core.Policy(allowed_shells=frozenset({"/bin/bash", "/usr/sbin/nologin"}))


@pytest.fixture
def write_csv(tmp_path):
    def _write(text: str):
        path = tmp_path / "users.csv"
        path.write_text(text, encoding="utf-8")
        return path

    return _write
