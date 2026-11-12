#!/usr/bin/env bash
# Real-UART no-motion timing batch, MAXN only.
# Writes inert CMD=0x00 frames to /dev/ttyUSB0; no motor routine is dispatched.
set -euo pipefail
P=/root/gedge_bot_validation_20261107
source "$P/run_env.sh"
cd "$P/paper"
D="$P/paper/evidence/device"
TS=$(date +%Y%m%d_%H%M%S)
echo "=== real-UART batch $TS ==="
nvpmodel -q 2>/dev/null | tail -2 || true

for rep in 1 2 3; do
  for cond in B_idle A_ai_loaded; do
    extra=()
    if [ "$cond" = "A_ai_loaded" ]; then
      extra=(--load-python python3
             --load-script "$P/paper/scripts/yolo_load.py"
             --load-model "${GEDGEBOT_YOLO_MODEL:-/root/src/sample/yolov8n.pt}"
             --load-imgsz 640)
    fi
    echo "--- $cond rep$rep ---"
    python3 scripts/uart_timing.py \
      --mode no-motion --confirm-physical-uart \
      --device /dev/ttyUSB0 \
      --condition "${cond}_MAXN_rep${rep}" \
      --intervals 200 --period-ms 50 \
      "${extra[@]}" \
      --output "$D/uart_timing_${cond}_MAXN_rep${rep}_${TS}.json"
  done
done

echo "=== batch complete $TS ==="
nvpmodel -q 2>/dev/null | tail -2 || true
pkill -f yolo_load.py 2>/dev/null || true
echo "no residual processes expected"
