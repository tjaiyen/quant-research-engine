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

# Repo root, derived from this script's location (bin/scheduled-run.sh) so the
# job works on any machine without editing a hardcoded path.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
JOB="${1:-}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
TS="$(/bin/date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/sched-${JOB:-none}-$TS.log"

cd "$ROOT" || exit 1

# ── Single-instance guard (atomic on POSIX; macOS has no flock) ──────────────
LOCK_DIR="$LOG_DIR/.lock-${JOB:-none}"
# Clear a STALE lock first: a kill -9 leaves the dir behind (the EXIT trap never
# fired), which would silently skip every later run (e.g. days 2-5 of a monthly
# window). >120 min old = stale → reclaim it.
if [ -d "$LOCK_DIR" ] && [ -n "$(/usr/bin/find "$LOCK_DIR" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
  echo "[$TS] reclaiming stale lock ($LOCK_DIR)" >> "$LOG"
  rmdir "$LOCK_DIR" 2>/dev/null
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$TS] another '$JOB' run is in progress ($LOCK_DIR) — skipping" >> "$LOG"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

run() { "$PY" -m cli.track "$@"; }

# Retry once after a pause — a transient network blip (DNS, Wi-Fi wake) killed
# the 6/28 weekly and left the screen a week stale. Delay is short enough that
# the monthly screen (fires 06:00) still finishes before the 06:25 MOO window.
RETRY_DELAY="${RETRY_DELAY:-300}"
run_retry() {
  run "$@" && return 0
  echo "RETRY: '$*' failed — waiting ${RETRY_DELAY}s then retrying once…"
  sleep "$RETRY_DELAY"
  run "$@"
}
fail=0

{
  echo "=== quant-tracker $JOB run: $TS ==="

  # Schedule fire-times are LOCAL (launchd) and the plists assume Pacific — the
  # MOO buy window is 6:25-6:28 PT. Warn loudly if the Mac isn't on PT.
  TZNOW="$(/bin/date +%Z)"
  case "$TZNOW" in
    PST|PDT) ;;
    *) echo "WARN: system timezone is $TZNOW, not Pacific — scheduled fire times"\
            "assume PT (the monthly buy targets the 6:25-6:28 PT MOO window)." ;;
  esac

  # Off-Drive preflight gate — abort the whole run if store/vault are unsafe.
  if ! run doctor; then
    echo "ABORT: doctor preflight failed (store or vault unsafe)."
    exit 2
  fi

  # Critical steps set fail=1 (surfaced in the exit code); `report` is best-effort.
  case "$JOB" in
    weekly)
      echo "--- seed --refresh ---"; run_retry seed --refresh || { echo "FAIL: seed"; fail=1; }
      echo "--- screen ---";         run_retry screen        || { echo "FAIL: screen"; fail=1; }
      echo "--- health ---";         run health              || echo "WARN: health (best-effort)"
      ;;
    daily)
      echo "--- paper monitor ---";  run_retry paper monitor || { echo "FAIL: monitor"; fail=1; }
      ;;
    monthly)
      # Fresh screen first so the buy reads a <10h-old cache (else it aborts stale).
      echo "--- screen ---";         run_retry screen        || { echo "FAIL: screen"; fail=1; }
      echo "--- paper cycle ---";    run paper cycle         || { echo "FAIL: cycle"; fail=1; }
      echo "--- paper monitor ---";  run paper monitor       || { echo "FAIL: monitor"; fail=1; }
      ;;
    *)
      echo "ERROR: unknown job '$JOB' (expected weekly|daily|monthly)"; exit 3
      ;;
  esac

  echo "--- report ---"; run report || echo "WARN: report had errors"
  echo "=== done: $(/bin/date '+%Y-%m-%d %H:%M:%S') (fail=$fail) ==="
} >> "$LOG" 2>&1

# Run-health beacon — surfaces silent scheduled failures on the dashboard
# (a failed run otherwise only lives in a log nobody reads). Best-effort.
STATUS=$([ "${fail:-0}" -eq 0 ] && echo ok || echo fail)
printf '{"job":"%s","ended":"%s","status":"%s"}\n' \
  "$JOB" "$(/bin/date '+%Y-%m-%dT%H:%M:%S')" "$STATUS" \
  > "$ROOT/store/last_run.json" 2>/dev/null || true

# Retain ~2 months of scheduled-run logs (runs even on early failure).
/usr/bin/find "$LOG_DIR" -name 'sched-*.log' -mtime +60 -delete 2>/dev/null || true

exit "$fail"
