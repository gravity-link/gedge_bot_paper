#!/bin/bash
# 15 W desktop power-mode reference run. Restores MAXN on ANY exit via trap.
set -e
source /root/gedge_bot_validation_20261107/run_env.sh
P=/root/gedge_bot_validation_20261107
cd "$P/paper"
D=$P/paper/evidence/device
TS=$(date +%Y%m%d_%H%M%S)
restore() {
  echo "=== restoring MAXN (trap) ==="
  nvpmodel -m 0
  nvpmodel -q 2>&1 | grep -i "power mode" || true
}
trap restore EXIT
echo "=== START 15W $TS ==="
nvpmodel -m 7
nvpmodel -q 2>&1 | grep -i "power mode" || true
sleep 5
python3 -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"
echo "--- bench_ai 15W run 1 $(date +%H:%M:%S) ---"
python3 scripts/bench_ai.py --mode device --confirm-device-measurement \
  --run-id 1 --iterations 60 --nllb-runs 5 --power-mode 15W \
  --audio "$GEDGEBOT_AUDIO_10S" --output "$D/bench_ai_run1_15W_${TS}.json" 2>&1 | tail -3
echo "--- bench_voice 15W $(date +%H:%M:%S) ---"
python3 scripts/bench_voice.py --mode device --confirm-device-measurement \
  --audio "$GEDGEBOT_AUDIO_10S" \
  --text "The robot will now turn toward the speaker and begin following." \
  --output "$D/bench_voice_15W_${TS}.json" 2>&1 | tail -3
echo "=== DONE $TS $(date +%H:%M:%S) ==="
ls -lh "$D"/*"${TS}"*.json
