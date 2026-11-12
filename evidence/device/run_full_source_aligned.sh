#!/bin/bash
# Source-aligned benchmarks + AI-loaded A/B control, MAXN and 15 W.
# Restores MAXN on ANY exit. Never writes to a real serial port (pty only).
set -e
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

echo "=== START full source-aligned $TS ==="
nvpmodel -q 2>&1 | grep -i "power mode" || true
python3 -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.cuda.get_device_name(0))"

for MODE in MAXN 15W; do
  if [ "$MODE" = "15W" ]; then
    nvpmodel -m 7
    sleep 5
    echo "=== switched to 15W $(date +%H:%M:%S) ==="
    nvpmodel -q 2>&1 | grep -i "power mode" || true
  fi
  echo "--- bench_source_aligned $MODE $(date +%H:%M:%S) ---"
  python3 scripts/bench_source_aligned.py --mode device --confirm-device-measurement \
    --run-id 1 --iterations 60 --nllb-runs 3 --power-mode "$MODE" \
    --audio "$GEDGEBOT_AUDIO_10S" \
    --output "$D/bench_ai_source_aligned_${MODE}_${TS}.json" 2>&1 | tail -3

  for COND in B_idle A_ai_loaded; do
    echo "--- ab_control ${COND}_${MODE} $(date +%H:%M:%S) ---"
    LOAD=""
    if [ "$COND" = "A_ai_loaded" ]; then
      LOAD="--load-python python3 --load-model /root/src/sample/yolov8n.pt --load-imgsz 640"
    fi
    python3 scripts/ab_control.py \
      --runtime evidence/robot_runtime.current.py \
      --condition "${COND}_${MODE}_pty_20261107" \
      --intervals 200 --period-ms 50 \
      --output "$D/ab_control_${COND}_${MODE}_${TS}.json" \
      $LOAD 2>&1 | tail -3
  done
done
echo "=== DONE $TS $(date +%H:%M:%S) ==="
ls -lh "$D"/*"${TS}"*.json
