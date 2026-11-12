#!/bin/bash
# Formal device measurement, MAXN. Never changes nvpmodel; operator switches manually.
set -e
source /root/gedge_bot_validation_20261107/run_env.sh
P=/root/gedge_bot_validation_20261107
cd "$P/paper"
D=$P/paper/evidence/device
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$D"
echo "=== START $TS ==="
nvpmodel -q 2>&1 | grep -i "power mode" || true
python3 -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.cuda.get_device_name(0))"
for i in 1 2 3; do
  echo "--- bench_ai run $i $(date +%H:%M:%S) ---"
  python3 scripts/bench_ai.py --mode device --confirm-device-measurement \
    --run-id "$i" --iterations 60 --nllb-runs 5 --power-mode MAXN \
    --audio "$GEDGEBOT_AUDIO_10S" --output "$D/bench_ai_run${i}_MAXN_${TS}.json" 2>&1 | tail -3
done
echo "--- bench_voice $(date +%H:%M:%S) ---"
python3 scripts/bench_voice.py --mode device --confirm-device-measurement \
  --audio "$GEDGEBOT_AUDIO_10S" \
  --text "The robot will now turn toward the speaker and begin following." \
  --output "$D/bench_voice_MAXN_${TS}.json" 2>&1 | tail -3
echo "--- failsafe $(date +%H:%M:%S) ---"
python3 scripts/failsafe_test.py --mode device --confirm-device-measurement \
  --output "$D/failsafe_${TS}.json" 2>&1 | tail -3
echo "=== DONE $TS $(date +%H:%M:%S) ==="
ls -lh "$D"/*"${TS}"*.json
