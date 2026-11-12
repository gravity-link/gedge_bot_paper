#!/bin/bash
# 2026-11-07 parameter-sensitivity batch (Xavier only).
# Whisper dtype/warm-up/audio-length sweep + NLLB prompt/decoding sweep,
# MAXN and 15 W. pty-free, no serial port, no hardware actuation.
# MAXN restored on ANY exit path.
set -e
set -o pipefail
source /root/gedge_bot_validation_20261107/run_env.sh
P=/root/gedge_bot_validation_20261107
cd "$P/paper"
D=$P/paper/evidence/device
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$D"

restore() {
  echo "=== restoring MAXN (trap) ==="
  nvpmodel -m 0
  nvpmodel -q 2>&1 | grep -i "power mode" || true
}
trap restore EXIT

echo "=== SENSITIVITY START batch=$TS $(date +%F' '%H:%M:%S) ==="
for MODE in MAXN 15W; do
  if [ "$MODE" = "15W" ]; then
    nvpmodel -m 7
    sleep 8
  fi
  nvpmodel -q 2>&1 | grep -i "power mode" || true
  echo "--- sensitivity $MODE $(date +%H:%M:%S) ---"
  python3 scripts/sensitivity_bench.py --mode device --confirm-device-measurement \
    --power-mode "$MODE" --repeats 3 \
    --audio "$GEDGEBOT_AUDIO_10S" \
    --output "$D/sensitivity_${MODE}_${TS}.json" 2>&1 | tail -5
done
echo "=== SENSITIVITY DONE batch=$TS $(date +%F' '%H:%M:%S) ==="
ls -lh "$D"/sensitivity_*"${TS}"*.json
