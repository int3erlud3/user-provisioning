# user-provisioning

[![CI](https://github.com/int3erlud3/user-provisioning/actions/workflows/ci.yml/badge.svg)](https://github.com/int3erlud3/user-provisioning/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

```text
   _  _ ___ ___ _ _
  | || (_-</ -_) '_|
   \_,_/__/\___|_|
                    _    _          _
   _ __ _ _ _____ _(_)__(_)___ _ _ (_)_ _  __ _
  | '_ \ '_/ _ \ V / (_-< / _ \ ' \| | ' \/ _` |
  | .__/_| \___/\_/|_/__/_\___/_||_|_|_||_\__, |
  |_|                                     |___/

+====================================================================+
|  USER PROVISIONING  ::  CSV-Driven Account Lifecycle               |
+--------------------------------------------------------------------+
|  Create, lock and delete Linux users - dry-run by default          |
|  v1.0.0  -  Bastion Ops Toolkit  -  by int3erlud3                  |
+====================================================================+
```

Create, lock and delete Linux user accounts from a CSV file using the standard
`useradd` / `usermod` / `userdel` tools. **Dry-run by default**, strict validation,
fail-closed behaviour and a JSON Lines audit log.

## Features

- Actions: `create`, `lock` (password lock **and** account expiry, which also blocks
  SSH key logins), `delete` (optionally with home directory)
- **Dry-run is the default**; changes require `--apply` and root
- **All-or-nothing validation**: if any row is invalid, nothing is executed
- Idempotent: existing users are skipped on `create`, missing users on `lock`/`delete`
- Protects system accounts (UID < 1000 by default) from `lock`/`delete`
- Refuses privileged groups (`sudo`, `wheel`, `docker`, `adm`, …) unless
  `--allow-privileged-groups` is given
- Audit log (JSON Lines, mode `0600`) with timestamp, operator (`SUDO_USER`), command and result

## Installation

```bash
git clone https://github.com/int3erlud3/user-provisioning.git
cd user-provisioning
sudo python3 -m venv /opt/user-provisioning
sudo /opt/user-provisioning/bin/pip install .
sudo ln -s /opt/user-provisioning/bin/user-provisioning /usr/local/sbin/
```

## CSV format

```csv
action,username,groups,shell,comment,remove_home
create,jdoe,users,/bin/bash,Jane Doe,
create,svc-backup,,/usr/sbin/nologin,Backup service account,
lock,olduser,,,,
delete,contractor1,,,,yes
```

| Column | Required | Description |
|---|---|---|
| `action` | yes | `create`, `lock` or `delete` (rows starting with `#` are ignored) |
| `username` | yes | `^[a-z_][a-z0-9_-]{0,31}$` |
| `groups` | no | Supplementary groups separated by `;` (must exist) |
| `shell` | no | Must be listed in `/etc/shells` (default `/bin/bash`) |
| `comment` | no | GECOS field; letters, digits, spaces and `.,'()@+-` only |
| `remove_home` | no | `yes` → `userdel --remove` |

## Usage

```bash
# 1. Review the plan (no root needed, nothing changes)
user-provisioning examples/users.csv

# 2. Apply
sudo user-provisioning examples/users.csv --apply

# Custom audit log location
sudo user-provisioning users.csv --apply --audit-log /var/log/provisioning/audit.jsonl
```

Example dry-run output:

```text
Dry-run mode: no changes will be made. Re-run with --apply to execute.
[DRY-RUN] create jdoe: /usr/sbin/useradd --create-home --shell /bin/bash --comment 'Jane Doe' --groups users -- jdoe
[SKIP]    lock   olduser: user does not exist
2 operation(s) processed, 0 failure(s).
```

Exit codes: `0` success, `1` validation/usage error (nothing changed), `2` one or more commands failed.

## Options

| Option | Default | Description |
|---|---|---|
| `--apply` | off | Execute changes (requires root) |
| `--audit-log PATH` | `/var/log/user-provisioning.jsonl` with `--apply` | Audit log file |
| `--min-uid N` | `1000` | Lowest UID that may be locked/deleted (cannot be < 1000) |
| `--allow-privileged-groups` | off | Permit `sudo`, `wheel`, `docker`, … |
| `--no-banner` / `NO_BANNER=1` | off | Suppress the startup banner (shown on a terminal only) |

## Startup banner

Part of the **Bastion Ops Toolkit**. When run interactively, `user-provisioning` prints the
banner shown above to **stderr** – only if stderr is a terminal. Pipes,
cron jobs, systemd units and monitoring agents see exactly the same output and exit
codes as before. Disable it with `--no-banner` or `NO_BANNER=1`; `--help` and
`--version` show it on a terminal too.

## Development

```bash
make venv && make lint test security   # ruff, pytest (subprocess mocked), bandit, pip-audit
make integration                        # real useradd/usermod/userdel in a throwaway container
make scan                               # gitleaks secret scan (needs Docker)
```

## Security notes

- Commands are executed with `subprocess.run([...])` **argument lists**, never
  `shell=True`; binaries are resolved from a fixed trusted `PATH`, and the child
  environment is reduced to that `PATH`.
- Every field is validated against allow-lists; usernames are passed after `--`
  so they can never be interpreted as options.
- New accounts get **no password** (locked password) – distribute SSH keys or set a
  password through your normal, secure process.
- The audit log is opened with `O_NOFOLLOW` and mode `0600` (no symlink attacks,
  not world-readable).
- Run the dry-run as an unprivileged user; use `sudo` only for `--apply`.

See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
