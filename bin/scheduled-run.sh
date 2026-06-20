#!/usr/bin/env bash
# Scheduled quant-tracker runs, driven by the launchd agents
# com.tj.quant-tracker.{weekly,daily,monthly}.
#
#   Usage: scheduled-run.sh <weekly|daily|monthly>
#     weekly  → seed --refresh → screen → report
#     daily   → paper monitor → report
#     monthly → paper cycle → paper monitor → report
#
# Cold-start safe (Insight B16): absolute paths only, doctor-gated, idempotent,
# no missed-run catch-up. All durable state lives in store/ (SQLite) + the vault;
# this script holds none. Logs to logs/sched-<job>-<ts>.log (gitignored).
set -uo pipefail

ROOT="/Users/user/dev/quant-tracker"
PY="$ROOT/.venv/bin/python"
JOB="${1:-}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/sched-${JOB:-none}-$TS.log"

cd "$ROOT" || exit 1

run() { "$PY" -m cli.track "$@"; }

{
  echo "=== quant-tracker $JOB run: $TS ==="

  # Off-Drive preflight gate — abort the whole run if store/vault are unsafe.
  if ! run doctor; then
    echo "ABORT: doctor preflight failed (store or vault unsafe)."
    exit 2
  fi

  # Each step is best-effort + idempotent; a hiccup in one still lets `report`
  # run so the dashboard reflects whatever the DB currently holds.
  case "$JOB" in
    weekly)
      echo "--- seed --refresh ---"; run seed --refresh || echo "WARN: seed had errors"
      echo "--- screen ---";         run screen        || echo "WARN: screen had errors"
      ;;
    daily)
      echo "--- paper monitor ---";  run paper monitor || echo "WARN: monitor had errors"
      ;;
    monthly)
      echo "--- paper cycle ---";    run paper cycle   || echo "WARN: cycle had errors"
      echo "--- paper monitor ---";  run paper monitor || echo "WARN: monitor had errors"
      ;;
    *)
      echo "ERROR: unknown job '$JOB' (expected weekly|daily|monthly)"; exit 3
      ;;
  esac

  echo "--- report ---"; run report || echo "WARN: report had errors"
  echo "=== done: $(date '+%Y-%m-%d %H:%M:%S') ==="
} >> "$LOG" 2>&1

# Retain ~2 months of scheduled-run logs.
find "$LOG_DIR" -name 'sched-*.log' -mtime +60 -delete 2>/dev/null || true
