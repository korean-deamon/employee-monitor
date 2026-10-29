#!/usr/bin/env bash
# All tests run against a throwaway database, never data/activity.db.
cd "$(dirname "$0")/.."
fail=0
for t in tests/test_*.py; do
  echo "── $t"
  venv/bin/python -I "$t" || fail=1
done
exit $fail
