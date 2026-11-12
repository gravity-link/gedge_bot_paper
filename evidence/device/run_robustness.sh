#!/bin/bash
# 2026-11-07 evening robustness batch (Xavier only).
#
# Phase 1: repeated, paired A/B control-frame timing. Five B_idle /
#          A_ai_loaded pairs per power mode plus a GPU-free CPU-load control,
#          to characterise the unstable 15 W idle baseline seen in the first
#          batch instead of quoting a single ratio.
# Phase 2: three repeats of the source-aligned benchmark per power mode, so
#          the new AI numbers have a dispersion rather than a single mean.
#
# Safety: pty only, never a real serial port; no motor frames reach hardware.
# MAXN is restored on ANY exit path via trap.
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

snapshot() {
  printf '%s mode=%s loadavg=%s temps=%s\n' \
    "$(date +%H:%M:%S)" "$1" "$(cut -d' ' -f1-3 /proc/loadavg)" \
    "$(cat /sys/devices/virtual/thermal/thermal_zone0/temp 2>/dev/null || echo na)" \
    | tee -a "$D/robustness_env_${TS}.log"
}

echo "=== PHASE 1 A/B robustness START batch=$TS $(date +%F' '%H:%M:%S) ==="
for MODE in MAXN 15W; do
  if [ "$MODE" = "15W" ]; then
    nvpmodel -m 7
    sleep 8
  fi
  nvpmodel -q 2>&1 | grep -i "power mode" || true
  snapshot "$MODE"
  for REP in 1 2 3 4 5; do
    for COND in B_idle A_ai_loaded; do
      echo "--- ${COND}_${MODE}_rep${REP} $(date +%H:%M:%S) ---"
      LOAD=""
      if [ "$COND" = "A_ai_loaded" ]; then
        LOAD="--load-python python3 --load-model /root/src/sample/yolov8n.pt --load-imgsz 640"
      fi
      python3 scripts/ab_control.py \
        --runtime evidence/robot_runtime.current.py \
        --condition "${COND}_${MODE}_rep${REP}_pty_20261107" \
        --intervals 200 --period-ms 50 \
        --output "$D/ab_control_${COND}_${MODE}_rep${REP}_${TS}.json" \
        $LOAD 2>&1 | tail -3
    done
  done
  for REP in 1 2; do
    echo "--- C_cpu_load_${MODE}_rep${REP} $(date +%H:%M:%S) ---"
    python3 scripts/ab_control.py \
      --runtime evidence/robot_runtime.current.py \
      --condition "C_cpu_load_${MODE}_rep${REP}_pty_20261107" \
      --intervals 200 --period-ms 50 \
      --output "$D/ab_control_C_cpu_load_${MODE}_rep${REP}_${TS}.json" \
      --load-python python3 --load-script scripts/cpu_load.py \
      --load-model cpu_only --load-imgsz 640 2>&1 | tail -3
  done
  snapshot "${MODE}_done"
done
nvpmodel -m 0
sleep 5
echo "=== PHASE 1 DONE $(date +%H:%M:%S) ==="

echo "=== PHASE 2 repeated source-aligned benchmarks START $(date +%H:%M:%S) ==="
for MODE in MAXN 15W; do
  if [ "$MODE" = "15W" ]; then
    nvpmodel -m 7
    sleep 8
  fi
  nvpmodel -q 2>&1 | grep -i "power mode" || true
  snapshot "phase2_${MODE}"
  for REP in 1 2 3; do
    echo "--- bench_source_aligned ${MODE} rep${REP} $(date +%H:%M:%S) ---"
    python3 scripts/bench_source_aligned.py --mode device --confirm-device-measurement \
      --run-id "$REP" --iterations 60 --nllb-runs 3 --power-mode "$MODE" \
      --audio "$GEDGEBOT_AUDIO_10S" \
      --output "$D/bench_ai_source_aligned_${MODE}_rep${REP}_${TS}.json" 2>&1 | tail -3
  done
  snapshot "phase2_${MODE}_done"
done
echo "=== PHASE 2 DONE $(date +%H:%M:%S) ==="
echo "=== ALL DONE batch=$TS $(date +%F' '%H:%M:%S) ==="
ls -lh "$D"/*"${TS}"*.json | wc -l
