#!/bin/bash
# Idle vs AI-loaded direct-control timing on a pty, MAXN + 15 W.
# Restores MAXN on ANY exit; never opens a real serial port.
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
echo "=== START A/B $TS ==="
for MODE in MAXN 15W; do
  if [ "$MODE" = "15W" ]; then
    nvpmodel -m 7
    sleep 5
  fi
  nvpmodel -q 2>&1 | grep -i "power mode" || true
  for COND in B_idle A_ai_loaded; do
    echo "--- ${COND}_${MODE} $(date +%H:%M:%S) ---"
    LOAD=""
    if [ "$COND" = "A_ai_loaded" ]; then
      LOAD="--load-python python3 --load-model /root/src/sample/yolov8n.pt --load-imgsz 640"
    fi
    python3 scripts/ab_control.py \
      --runtime evidence/robot_runtime.current.py \
      --condition "${COND}_${MODE}_pty_20261107" \
      --intervals 200 --period-ms 50 \
      --output "$D/ab_control_${COND}_${MODE}_${TS}.json" \
      $LOAD 2>&1 | tail -4
  done
done
echo "=== DONE $TS $(date +%H:%M:%S) ==="
ls -lh "$D"/ab_control_*"${TS}"*.json
