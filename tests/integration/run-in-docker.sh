#!/usr/bin/env bash
# End-to-end test with the real useradd/usermod/userdel inside a throwaway container.
# Usage: tests/integration/run-in-docker.sh   (needs Docker; never touches the host's users)
set -euo pipefail
IMAGE="${IMAGE:-python:3.12-slim}"
repo="$(cd "$(dirname "$0")/../.." && pwd)"

docker run --rm --network none -v "$repo:/src:ro" "$IMAGE" bash -euo pipefail -c '
  fail() { echo "FAIL: $*"; exit 1; }
  up() { PYTHONPATH=/src/src PYTHONDONTWRITEBYTECODE=1 python -m user_provisioning "$@"; }
  groupadd developers && useradd -m olduser && useradd -m contractor1
  printf "action,username,groups,shell,comment,remove_home\ncreate,jdoe,developers,/bin/bash,Jane Doe,\nlock,olduser,,,,\ndelete,contractor1,,,,yes\n" >/tmp/u.csv
  up /tmp/u.csv
  if id jdoe 2>/dev/null; then fail "dry-run created a user"; fi
  up /tmp/u.csv --apply --audit-log /tmp/audit.jsonl
  id -nG jdoe | grep -qw developers || fail "jdoe not in developers"
  passwd -S olduser | grep -q " L " || fail "olduser password not locked"
  chage -l olduser | grep -q "Jan 02, 1970" || fail "olduser not expired"
  if id contractor1 2>/dev/null || [ -d /home/contractor1 ]; then fail "contractor1 not removed"; fi
  [ "$(stat -c %a /tmp/audit.jsonl)" = 600 ] || fail "audit log mode"
  [ "$(wc -l </tmp/audit.jsonl)" = 3 ] || fail "audit log records"
  printf "action,username\ndelete,root\n" >/tmp/bad.csv
  if up /tmp/bad.csv --apply --audit-log /tmp/audit.jsonl; then fail "root deletion not refused"; fi
  id root >/dev/null || fail "root missing"
  echo "integration test passed"
'
