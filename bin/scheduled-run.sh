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
# no missed-run catch-up. Single-instance (atomic mkdir lock) so a manual run
# can't collide with a scheduled one. Exits non-zero if a critical step failed
# so the launchd log reflects reality. Logs to logs/sched-<job>-<ts>.log.
set -uo pipefail

ROOT="/Users/user/dev/quant-tracker"
PY="$ROOT/.venv/bin/python"
JOB="${1:-}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
TS="$(/bin/date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/sched-${JOB:-none}-$TS.log"

cd "$ROOT" || exit 1

# ── Single-instance guard (atomic on POSIX; macOS has no flock) ──────────────
LOCK_DIR="$LOG_DIR/.lock-${JOB:-none}"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$TS] another '$JOB' run is in progress ($LOCK_DIR) — skipping" >> "$LOG"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

run() { "$PY" -m cli.track "$@"; }
fail=0

{
  echo "=== quant-tracker $JOB run: $TS ==="

  # Off-Drive preflight gate — abort the whole run if store/vault are unsafe.
  if ! run doctor; then
    echo "ABORT: doctor preflight failed (store or vault unsafe)."
    exit 2
  fi

  # Critical steps set fail=1 (surfaced in the exit code); `report` is best-effort.
  case "$JOB" in
    weekly)
      echo "--- seed --refresh ---"; run seed --refresh || { echo "FAIL: seed"; fail=1; }
      echo "--- screen ---";         run screen        || { echo "FAIL: screen"; fail=1; }
      ;;
    daily)
      echo "--- paper monitor ---";  run paper monitor || { echo "FAIL: monitor"; fail=1; }
      ;;
    monthly)
      echo "--- paper cycle ---";    run paper cycle   || { echo "FAIL: cycle"; fail=1; }
      echo "--- paper monitor ---";  run paper monitor || { echo "FAIL: monitor"; fail=1; }
      ;;
    *)
      echo "ERROR: unknown job '$JOB' (expected weekly|daily|monthly)"; exit 3
      ;;
  esac

  echo "--- report ---"; run report || echo "WARN: report had errors"
  echo "=== done: $(/bin/date '+%Y-%m-%d %H:%M:%S') (fail=$fail) ==="
} >> "$LOG" 2>&1

# Retain ~2 months of scheduled-run logs (runs even on early failure).
/usr/bin/find "$LOG_DIR" -name 'sched-*.log' -mtime +60 -delete 2>/dev/null || true

exit "$fail"
