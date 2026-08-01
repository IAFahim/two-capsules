#!/usr/bin/env bash
# Run the smart narration plan to completion, surviving worker death.
#
#   tools/narrate_all.sh              # full queue
#   tools/narrate_all.sh 20           # max rounds
#   tools/narrate_all.sh 40 --books-only
#
# Each round: ensure worker → gen_narration.py (priority queue) → re-plan.
# Poison clips are quarantined in narrate/.poison.json and never block the queue.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX="${1:-60}"
shift || true
EXTRA=("$@")

cd "$ROOT"

echo "== writing plan =="
python3 tools/gen_narration.py --plan "${EXTRA[@]}"

for round in $(seq 1 "$MAX"); do
  left=$(python3 - <<'PY'
import json
from pathlib import Path
p = Path("narrate/plan.json")
if not p.exists():
    print(9999); raise SystemExit
d = json.loads(p.read_text())
print(d["summary"]["queue"])
PY
)
  echo
  echo "== round $round · $left clip(s) in queue =="
  if [[ "$left" -le 0 ]]; then
    echo "NARRATION COMPLETE"
    break
  fi

  # one full pass; gen_narration restarts the worker itself as needed
  python3 tools/gen_narration.py "${EXTRA[@]}" || true

  after=$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path("narrate/plan.json").read_text())["summary"]["queue"])
PY
)
  if [[ "$after" -ge "$left" ]]; then
    echo "no progress ($after still queued) — remaining clips are poison or worker-hard"
    python3 tools/gen_narration.py --manifest-only
    exit 1
  fi
done

python3 tools/gen_narration.py --manifest-only
python3 tools/gen_narration.py --plan "${EXTRA[@]}"
echo "final queue: $(python3 -c 'import json;print(json.load(open(\"narrate/plan.json\"))[\"summary\"][\"queue\"])')"
