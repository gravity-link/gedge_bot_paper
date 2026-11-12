#!/bin/bash
# True CUDA cold-start control for the Whisper discrepancy.
#
# In run_sensitivity.sh the fp32_no_warm profile runs *after* fp16_warm in the
# same process, so CUDA/cuDNN is already initialised and its "cold" call is
# only 1-2 s. This script launches a fresh Python process per power mode with
# --profiles fp32_no_warm --components whisper, so the first inference really
# starts from a cold CUDA context, matching the first reconstructed bench_ai
# batch (which created a fresh FP32 pipeline per call and saw RTF ~2.27).
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

echo "=== COLD WHISPER START batch=$TS $(date +%F' '%H:%M:%S) ==="
for MODE in MAXN 15W; do
  if [ "$MODE" = "15W" ]; then
    nvpmodel -m 7
    sleep 8
  fi
  nvpmodel -q 2>&1 | grep -i "power mode" || true
  echo "--- cold fp32 no-warm $MODE $(date +%H:%M:%S) ---"
  python3 scripts/sensitivity_bench.py --mode device --confirm-device-measurement \
    --power-mode "$MODE" --repeats 3 \
    --profiles fp32_no_warm --components whisper \
    --audio "$GEDGEBOT_AUDIO_10S" \
    --output "$D/sensitivity_cold_${MODE}_${TS}.json" 2>&1 | tail -5
done
echo "=== COLD WHISPER DONE batch=$TS $(date +%F' '%H:%M:%S) ==="
ls -lh "$D"/sensitivity_cold_*"${TS}"*.json
