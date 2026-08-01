#!/usr/bin/env bash
# Run the narration pass to completion, surviving worker death.
#
# The Inflect worker exits on an unhandled "no speakable tokens" error, which
# kills the whole run. gen_narration.py skips clips that already exist, so the
# fix is simply to restart the worker and go again — each pass makes progress.
#
#   tools/narrate_all.sh [max_rounds]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARE="$HOME/.local/share/readaloud"
SOCK="${XDG_RUNTIME_DIR:-/tmp}/readaloud-inflect.sock"
MAX="${1:-40}"

missing() {
  python3 - "$ROOT" <<'PY'
import json, glob, os, sys
root = sys.argv[1]
n = 0
for f in glob.glob(os.path.join(root, "narrate/scripts/*.json")):
    d = json.load(open(f)); lid = d["id"]
    ids = [f"ch-{c['id']}" for c in d["chapters"]]
    for k, v in (d.get("nodes") or {}).items():
        ids.append(f"node-{k}-short")
        if isinstance(v, dict) and v.get("deep"):
            ids.append(f"node-{k}-deep")
    n += sum(1 for i in ids
             if not os.path.exists(os.path.join(root, f"narrate/audio/{lid}/{i}.opus")))
print(n)
PY
}

start_worker() {
  pgrep -f "inflect_worker.py" >/dev/null && return 0
  rm -f "$SOCK" "$SOCK.ready"
  READALOUD_INFLECT_MODEL="$SHARE/inflect-micro-v2" \
  READALOUD_INFLECT_SOCK="$SOCK" \
  READALOUD_INFLECT_READY="$SOCK.ready" \
  setsid nohup "$SHARE/inflect-venv/bin/python" "$SHARE/inflect_worker.py" \
    >>"$SHARE/inflect-worker.log" 2>&1 </dev/null &
  disown
  for _ in $(seq 1 30); do sleep 2; [ -S "$SOCK" ] && return 0; done
  return 1
}

for round in $(seq 1 "$MAX"); do
  left="$(missing)"
  echo "== round $round · $left clip(s) missing =="
  [ "$left" -le 0 ] && echo "NARRATION COMPLETE" && break

  start_worker || { echo "worker would not start"; exit 1; }
  ( cd "$ROOT" && python3 tools/gen_narration.py ) 2>&1 | tail -3

  after="$(missing)"
  if [ "$after" -ge "$left" ]; then
    # A round that gains nothing means the next clip kills the worker every time.
    echo "no progress at $after — the blocking clip is unsynthesizable, stopping"
    break
  fi
done

echo "final: $(missing) clip(s) without audio"
( cd "$ROOT" && python3 tools/gen_narration.py --manifest-only )
