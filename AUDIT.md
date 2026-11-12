# Publication audit

Generated 2026-11-12 for `gravity-link/gedge_bot_paper`, the public artifact
repository for arXiv:2611.11698. Source workspace:
`/Users/henryluo/lobsterai/project/paper`.

## Method

A strict allowlist was used: only released LaTeX sources, all `.py` scripts,
measured JSON records and on-device runner `.sh` scripts were copied.
Every copied file was verified byte-identical to its source (125/125 matched).
No copied script or JSON was edited, because evidence records embed
`script_sha256` hashes. The parent git workspace (business plans, financial
models, market studies) and the separate `~/src/gedge_bot` repository were never
touched. A credential-pattern scan found no keys, tokens or passwords.

## Verification results

| Check | Result |
| --- | --- |
| Copied files byte-identical | 125 / 125 |
| Python scripts syntax (`compileall`) | 24 / 24 |
| Evidence + metadata JSON parse | 87 / 87 |
| Credential / private-file scan | PASS |
| `script_sha256` references | 40 checked: 33 matched, 6 documented historical drift, 1 unresolved |
| New (undocumented) hash drift | 0 |

The 6 historical drifts are recorded in `tools/known_hash_drift.json`: four
reconstructed local probes were generated against an early script revision,
and two sensitivity runs hash a deployed device copy. Those records carry
`local_probe_no_measurement` or device-side evidence status and were not edited.
One analysis record (`integration_surface_20261107.json`) has an empty `script`
field, so its hash cannot be resolved to a shipped script.

## Environment metadata retained on purpose

Some JSON records contain historical device paths (`/root/...`,
`/mnt/nvme_data/...`), one RFC1918 address (`192.168.1.130`) and local
workspace paths. These are environmental metadata, are not credentials, and
were kept verbatim so the recorded hashes and evidence chain stay valid.
`REPRODUCING.md` explains how to override them.

## Included in the public repository (136 files)

| Path | Origin | Env refs |
| --- | --- | --- |
| `.gitignore` | New in this release (metadata / documentation / verification tool) | 0 |
| `AUDIT.md` | New in this release (metadata / documentation / verification tool) | 4 |
| `CITATION.cff` | New in this release (metadata / documentation / verification tool) | 0 |
| `DATA_LICENSE.md` | New in this release (metadata / documentation / verification tool) | 0 |
| `LICENSE` | MIT license from the repository initial commit | 0 |
| `README.md` | New in this release (metadata / documentation / verification tool) | 0 |
| `REPRODUCING.md` | New in this release (metadata / documentation / verification tool) | 2 |
| `codemeta.json` | New in this release (metadata / documentation / verification tool) | 0 |
| `evidence/ab_control_B_idle_MAXN_pty_20261107.json` | Byte-identical copy of paper/evidence/ab_control_B_idle_MAXN_pty_20261107.json | 1 |
| `evidence/ab_control_B_idle_MAXN_pty_20261107.summary.json` | Byte-identical copy of paper/evidence/ab_control_B_idle_MAXN_pty_20261107.summary.json | 0 |
| `evidence/ab_control_local_smoke.json` | Byte-identical copy of paper/evidence/ab_control_local_smoke.json | 1 |
| `evidence/analysis/boundary_isolation_20261107.json` | Byte-identical copy of paper/evidence/analysis/boundary_isolation_20261107.json | 1 |
| `evidence/analysis/full_runtime_replay_20261107.json` | Byte-identical copy of paper/evidence/analysis/full_runtime_replay_20261107.json | 4 |
| `evidence/analysis/full_runtime_replay_20261107_jetson.json` | Byte-identical copy of paper/evidence/analysis/full_runtime_replay_20261107_jetson.json | 4 |
| `evidence/analysis/integration_surface_20261107.json` | Byte-identical copy of paper/evidence/analysis/integration_surface_20261107.json | 1 |
| `evidence/analysis/product_loop_replay_20261107.json` | Byte-identical copy of paper/evidence/analysis/product_loop_replay_20261107.json | 4 |
| `evidence/analysis/product_loop_replay_20261107_jetson.json` | Byte-identical copy of paper/evidence/analysis/product_loop_replay_20261107_jetson.json | 3 |
| `evidence/analysis/runtime_control_graph_20261107.json` | Byte-identical copy of paper/evidence/analysis/runtime_control_graph_20261107.json | 3 |
| `evidence/analysis/runtime_control_graph_20261107_jetson.json` | Byte-identical copy of paper/evidence/analysis/runtime_control_graph_20261107_jetson.json | 3 |
| `evidence/astra_smoke_20261107.json` | Byte-identical copy of paper/evidence/astra_smoke_20261107.json | 3 |
| `evidence/contract_test_results.json` | Byte-identical copy of paper/evidence/contract_test_results.json | 1 |
| `evidence/contract_test_results_xavier.json` | Byte-identical copy of paper/evidence/contract_test_results_xavier.json | 1 |
| `evidence/device/ab_control_A_ai_loaded_15W_20261107_104400.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_15W_20261107_104400.json | 3 |
| `evidence/device/ab_control_A_ai_loaded_15W_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_15W_rep1_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_15W_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_15W_rep2_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_15W_rep3_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_15W_rep3_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_15W_rep4_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_15W_rep4_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_15W_rep5_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_15W_rep5_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_MAXN_20261107_104400.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_MAXN_20261107_104400.json | 3 |
| `evidence/device/ab_control_A_ai_loaded_MAXN_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_MAXN_rep1_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_MAXN_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_MAXN_rep2_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_MAXN_rep3_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_MAXN_rep3_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_MAXN_rep4_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_MAXN_rep4_20261107_111551.json | 4 |
| `evidence/device/ab_control_A_ai_loaded_MAXN_rep5_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_A_ai_loaded_MAXN_rep5_20261107_111551.json | 4 |
| `evidence/device/ab_control_B_idle_15W_20261107_104400.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_20261107_104400.json | 1 |
| `evidence/device/ab_control_B_idle_15W_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_rep1_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_15W_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_rep2_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_15W_rep3_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_rep3_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_15W_rep4_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_rep4_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_15W_rep5_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_rep5_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_15W_repeat1_20261107_104400.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_repeat1_20261107_104400.json | 1 |
| `evidence/device/ab_control_B_idle_15W_repeat2_20261107_104400.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_15W_repeat2_20261107_104400.json | 1 |
| `evidence/device/ab_control_B_idle_MAXN_20261107_104400.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_MAXN_20261107_104400.json | 1 |
| `evidence/device/ab_control_B_idle_MAXN_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_MAXN_rep1_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_MAXN_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_MAXN_rep2_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_MAXN_rep3_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_MAXN_rep3_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_MAXN_rep4_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_MAXN_rep4_20261107_111551.json | 1 |
| `evidence/device/ab_control_B_idle_MAXN_rep5_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_B_idle_MAXN_rep5_20261107_111551.json | 1 |
| `evidence/device/ab_control_C_cpu_load_15W_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_C_cpu_load_15W_rep1_20261107_111551.json | 2 |
| `evidence/device/ab_control_C_cpu_load_15W_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_C_cpu_load_15W_rep2_20261107_111551.json | 2 |
| `evidence/device/ab_control_C_cpu_load_MAXN_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_C_cpu_load_MAXN_rep1_20261107_111551.json | 2 |
| `evidence/device/ab_control_C_cpu_load_MAXN_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/ab_control_C_cpu_load_MAXN_rep2_20261107_111551.json | 2 |
| `evidence/device/bench_ai_run1_15W_20261107_100800.json` | Byte-identical copy of paper/evidence/device/bench_ai_run1_15W_20261107_100800.json | 6 |
| `evidence/device/bench_ai_run1_MAXN_20261107_095443.json` | Byte-identical copy of paper/evidence/device/bench_ai_run1_MAXN_20261107_095443.json | 6 |
| `evidence/device/bench_ai_run2_MAXN_20261107_095443.json` | Byte-identical copy of paper/evidence/device/bench_ai_run2_MAXN_20261107_095443.json | 6 |
| `evidence/device/bench_ai_run3_MAXN_20261107_095443.json` | Byte-identical copy of paper/evidence/device/bench_ai_run3_MAXN_20261107_095443.json | 6 |
| `evidence/device/bench_ai_source_aligned_15W_20261107_103742.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_15W_20261107_103742.json | 8 |
| `evidence/device/bench_ai_source_aligned_15W_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_15W_rep1_20261107_111551.json | 8 |
| `evidence/device/bench_ai_source_aligned_15W_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_15W_rep2_20261107_111551.json | 8 |
| `evidence/device/bench_ai_source_aligned_15W_rep3_20261107_111551.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_15W_rep3_20261107_111551.json | 8 |
| `evidence/device/bench_ai_source_aligned_MAXN_20261107_103742.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_MAXN_20261107_103742.json | 8 |
| `evidence/device/bench_ai_source_aligned_MAXN_rep1_20261107_111551.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_MAXN_rep1_20261107_111551.json | 8 |
| `evidence/device/bench_ai_source_aligned_MAXN_rep2_20261107_111551.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_MAXN_rep2_20261107_111551.json | 8 |
| `evidence/device/bench_ai_source_aligned_MAXN_rep3_20261107_111551.json` | Byte-identical copy of paper/evidence/device/bench_ai_source_aligned_MAXN_rep3_20261107_111551.json | 8 |
| `evidence/device/bench_voice_15W_20261107_100800.json` | Byte-identical copy of paper/evidence/device/bench_voice_15W_20261107_100800.json | 5 |
| `evidence/device/bench_voice_MAXN_20261107_095443.json` | Byte-identical copy of paper/evidence/device/bench_voice_MAXN_20261107_095443.json | 5 |
| `evidence/device/chattts_MAXN_20261107_101512.json` | Byte-identical copy of paper/evidence/device/chattts_MAXN_20261107_101512.json | 3 |
| `evidence/device/failsafe_20261107_095443.json` | Byte-identical copy of paper/evidence/device/failsafe_20261107_095443.json | 2 |
| `evidence/device/run_ab_control.sh` | Byte-identical copy of paper/evidence/device/run_ab_control.sh | 3 |
| `evidence/device/run_bench.sh` | Byte-identical copy of paper/evidence/device/run_bench.sh | 2 |
| `evidence/device/run_bench_15w.sh` | Byte-identical copy of paper/evidence/device/run_bench_15w.sh | 2 |
| `evidence/device/run_env.sh` | Byte-identical copy of paper/evidence/device/run_env.sh | 9 |
| `evidence/device/run_full_source_aligned.sh` | Byte-identical copy of paper/evidence/device/run_full_source_aligned.sh | 3 |
| `evidence/device/run_robustness.sh` | Byte-identical copy of paper/evidence/device/run_robustness.sh | 3 |
| `evidence/device/run_sensitivity.sh` | Byte-identical copy of paper/evidence/device/run_sensitivity.sh | 2 |
| `evidence/device/run_uart_timing.sh` | Byte-identical copy of paper/evidence/device/run_uart_timing.sh | 2 |
| `evidence/device/run_whisper_cold.sh` | Byte-identical copy of paper/evidence/device/run_whisper_cold.sh | 2 |
| `evidence/device/sensitivity_15W_20261107_113758.json` | Byte-identical copy of paper/evidence/device/sensitivity_15W_20261107_113758.json | 8 |
| `evidence/device/sensitivity_MAXN_20261107_113758.json` | Byte-identical copy of paper/evidence/device/sensitivity_MAXN_20261107_113758.json | 8 |
| `evidence/device/sensitivity_cold_15W_20261107_115021.json` | Byte-identical copy of paper/evidence/device/sensitivity_cold_15W_20261107_115021.json | 6 |
| `evidence/device/sensitivity_cold_MAXN_20261107_115021.json` | Byte-identical copy of paper/evidence/device/sensitivity_cold_MAXN_20261107_115021.json | 6 |
| `evidence/device/uart_link_inventory_20261107.json` | Byte-identical copy of paper/evidence/device/uart_link_inventory_20261107.json | 0 |
| `evidence/device/uart_timing_A_ai_loaded_MAXN_rep1_20261107_133420.json` | Byte-identical copy of paper/evidence/device/uart_timing_A_ai_loaded_MAXN_rep1_20261107_133420.json | 4 |
| `evidence/device/uart_timing_A_ai_loaded_MAXN_rep2_20261107_133420.json` | Byte-identical copy of paper/evidence/device/uart_timing_A_ai_loaded_MAXN_rep2_20261107_133420.json | 4 |
| `evidence/device/uart_timing_A_ai_loaded_MAXN_rep3_20261107_133420.json` | Byte-identical copy of paper/evidence/device/uart_timing_A_ai_loaded_MAXN_rep3_20261107_133420.json | 4 |
| `evidence/device/uart_timing_B_idle_MAXN_rep1_20261107_133420.json` | Byte-identical copy of paper/evidence/device/uart_timing_B_idle_MAXN_rep1_20261107_133420.json | 1 |
| `evidence/device/uart_timing_B_idle_MAXN_rep2_20261107_133420.json` | Byte-identical copy of paper/evidence/device/uart_timing_B_idle_MAXN_rep2_20261107_133420.json | 1 |
| `evidence/device/uart_timing_B_idle_MAXN_rep3_20261107_133420.json` | Byte-identical copy of paper/evidence/device/uart_timing_B_idle_MAXN_rep3_20261107_133420.json | 1 |
| `evidence/firmware_static_results.json` | Byte-identical copy of paper/evidence/firmware_static_results.json | 0 |
| `evidence/firmware_static_results_xavier.json` | Byte-identical copy of paper/evidence/firmware_static_results_xavier.json | 0 |
| `evidence/rebuilt/bench_ai_run1.json` | Byte-identical copy of paper/evidence/rebuilt/bench_ai_run1.json | 4 |
| `evidence/rebuilt/bench_ai_run2.json` | Byte-identical copy of paper/evidence/rebuilt/bench_ai_run2.json | 4 |
| `evidence/rebuilt/bench_ai_run3.json` | Byte-identical copy of paper/evidence/rebuilt/bench_ai_run3.json | 4 |
| `evidence/rebuilt/bench_voice.json` | Byte-identical copy of paper/evidence/rebuilt/bench_voice.json | 3 |
| `evidence/rebuilt/demo_merged_20261107_165511.json` | Byte-identical copy of paper/evidence/rebuilt/demo_merged_20261107_165511.json | 1 |
| `evidence/rebuilt/demo_runs_20261107_165511_run1.json` | Byte-identical copy of paper/evidence/rebuilt/demo_runs_20261107_165511_run1.json | 1 |
| `evidence/rebuilt/demo_runs_20261107_165511_run2.json` | Byte-identical copy of paper/evidence/rebuilt/demo_runs_20261107_165511_run2.json | 1 |
| `evidence/rebuilt/demo_runs_20261107_165511_run3.json` | Byte-identical copy of paper/evidence/rebuilt/demo_runs_20261107_165511_run3.json | 1 |
| `evidence/rebuilt/demo_runs_20261107_165511_run4.json` | Byte-identical copy of paper/evidence/rebuilt/demo_runs_20261107_165511_run4.json | 1 |
| `evidence/rebuilt/demo_runs_20261107_165511_run5.json` | Byte-identical copy of paper/evidence/rebuilt/demo_runs_20261107_165511_run5.json | 1 |
| `evidence/rebuilt/demo_runs_20261107_165511_run6.json` | Byte-identical copy of paper/evidence/rebuilt/demo_runs_20261107_165511_run6.json | 1 |
| `evidence/rebuilt/failsafe_20261107_165511.json` | Byte-identical copy of paper/evidence/rebuilt/failsafe_20261107_165511.json | 2 |
| `paper/figures/architecture.tex` | Exact arXiv-submitted source (from arxiv_submission_20261108.zip) | 0 |
| `paper/figures/control_loop.tex` | Exact arXiv-submitted source (from arxiv_submission_20261108.zip) | 0 |
| `paper/figures/orchestration.tex` | Exact arXiv-submitted source (from arxiv_submission_20261108.zip) | 0 |
| `paper/figures/value_ladder.tex` | Exact arXiv-submitted source (from arxiv_submission_20261108.zip) | 0 |
| `paper/main.bbl` | Exact arXiv-submitted source (from arxiv_submission_20261108.zip) | 0 |
| `paper/main.tex` | Exact arXiv-submitted source (from arxiv_submission_20261108.zip) | 0 |
| `paper/references.bib` | Exact arXiv-submitted source (from arxiv_submission_20261108.zip) | 0 |
| `requirements.txt` | New in this release (metadata / documentation / verification tool) | 0 |
| `scripts/ab_control.py` | Byte-identical copy of paper/scripts/ab_control.py | 1 |
| `scripts/astra_smoke.py` | Byte-identical copy of paper/scripts/astra_smoke.py | 0 |
| `scripts/bench_ai.py` | Byte-identical copy of paper/scripts/bench_ai.py | 3 |
| `scripts/bench_source_aligned.py` | Byte-identical copy of paper/scripts/bench_source_aligned.py | 5 |
| `scripts/bench_voice.py` | Byte-identical copy of paper/scripts/bench_voice.py | 2 |
| `scripts/boundary_isolation.py` | Byte-identical copy of paper/scripts/boundary_isolation.py | 0 |
| `scripts/chattts_bench.py` | Byte-identical copy of paper/scripts/chattts_bench.py | 3 |
| `scripts/cpu_load.py` | Byte-identical copy of paper/scripts/cpu_load.py | 0 |
| `scripts/demo_capture.py` | Byte-identical copy of paper/scripts/demo_capture.py | 0 |
| `scripts/failsafe_test.py` | Byte-identical copy of paper/scripts/failsafe_test.py | 0 |
| `scripts/full_runtime_replay.py` | Byte-identical copy of paper/scripts/full_runtime_replay.py | 3 |
| `scripts/gpu_smoke.py` | Byte-identical copy of paper/scripts/gpu_smoke.py | 0 |
| `scripts/integration_surface.py` | Byte-identical copy of paper/scripts/integration_surface.py | 1 |
| `scripts/merge_demo_runs.py` | Byte-identical copy of paper/scripts/merge_demo_runs.py | 0 |
| `scripts/product_loop_replay.py` | Byte-identical copy of paper/scripts/product_loop_replay.py | 0 |
| `scripts/rebuild_common.py` | Byte-identical copy of paper/scripts/rebuild_common.py | 0 |
| `scripts/runtime_control_graph.py` | Byte-identical copy of paper/scripts/runtime_control_graph.py | 5 |
| `scripts/sensitivity_bench.py` | Byte-identical copy of paper/scripts/sensitivity_bench.py | 3 |
| `scripts/summarize_device_runs.py` | Byte-identical copy of paper/scripts/summarize_device_runs.py | 1 |
| `scripts/test_firmware_static.py` | Byte-identical copy of paper/scripts/test_firmware_static.py | 0 |
| `scripts/test_runtime_contract.py` | Byte-identical copy of paper/scripts/test_runtime_contract.py | 0 |
| `scripts/uart_report.py` | Byte-identical copy of paper/scripts/uart_report.py | 0 |
| `scripts/uart_timing.py` | Byte-identical copy of paper/scripts/uart_timing.py | 1 |
| `scripts/yolo_load.py` | Byte-identical copy of paper/scripts/yolo_load.py | 0 |
| `tools/check_public_release.py` | New in this release (metadata / documentation / verification tool) | 0 |
| `tools/known_hash_drift.json` | New in this release (metadata / documentation / verification tool) | 8 |

## Excluded from the source workspace (44 files)

| Path | Reason |
| --- | --- |
| `README.md` | Internal project README (superseded by the public README) |
| `arxiv_submission_20261108.zip` | Release package; its exact contents are expanded under paper/ |
| `evidence/BOUNDARY_ISOLATION.md` | Internal method / gap report (not cleared for public release) |
| `evidence/DEVICE_RESULTS.md` | Internal method / gap report (not cleared for public release) |
| `evidence/ENVIRONMENT_NOISE.md` | Internal method / gap report (not cleared for public release) |
| `evidence/EVALUATION_REDESIGN.md` | Internal method / gap report (not cleared for public release) |
| `evidence/FULL_RUNTIME_REPLAY.md` | Internal method / gap report (not cleared for public release) |
| `evidence/INTEGRATION_VALUE.md` | Internal method / gap report (not cleared for public release) |
| `evidence/PAPER_GAPS.md` | Internal method / gap report (not cleared for public release) |
| `evidence/PRODUCT_LOOP_REPLAY.md` | Internal method / gap report (not cleared for public release) |
| `evidence/REBUILT_BENCHMARKS.md` | Internal method / gap report (not cleared for public release) |
| `evidence/RUNTIME_CONTROL_GRAPH.md` | Internal method / gap report (not cleared for public release) |
| `evidence/SOURCE_ALIGNMENT.md` | Internal method / gap report (not cleared for public release) |
| `evidence/UART_TIMING.md` | Internal method / gap report (not cleared for public release) |
| `evidence/astra_haar_smoke_20261107T1325.log` | Raw on-device console log |
| `evidence/device/bench_audio_10s.wav` | Raw audio capture |
| `evidence/device/robustness_env_20261107_111551.log` | Raw on-device console log |
| `evidence/mic_doa_smoke_20261107T1328.log` | Raw on-device console log |
| `evidence/mic_smoke_20261107T1320.log` | Raw on-device console log |
| `evidence/motor_uart_vision.current.c` | Proprietary runtime / STM32 firmware source snapshot |
| `evidence/reference_audit.md` | Private source-tree manifest / audit record |
| `evidence/robot_runtime.current.py` | Proprietary runtime / STM32 firmware source snapshot |
| `evidence/source_README.current.md` | Private source-tree manifest / audit record |
| `evidence/source_audit.md` | Private source-tree manifest / audit record |
| `evidence/source_config.current.py` | Proprietary runtime / STM32 firmware source snapshot |
| `evidence/source_manifest.json` | Private source-tree manifest / audit record |
| `evidence/source_requirements.current.txt` | Private source-tree manifest / audit record |
| `evidence/source_tree.current.txt` | Private source-tree manifest / audit record |
| `evidence/validation_20261107.md` | Internal method / gap report (not cleared for public release) |
| `figures/architecture.tex` | Duplicate source; the submitted copy is published under paper/ |
| `figures/control_loop.tex` | Duplicate source; the submitted copy is published under paper/ |
| `figures/orchestration.tex` | Duplicate source; the submitted copy is published under paper/ |
| `figures/value_ladder.tex` | Duplicate source; the submitted copy is published under paper/ |
| `main.tex` | Duplicate source; the submitted copy is published under paper/ |
| `materials/abstract/ExtendedAbstract_ComputeRegeneration_Luo.txt` | Private patent drafts and extracted design materials; must not be published |
| `materials/astra_smoke_20261107.jpg` | Private patent drafts and extracted design materials; must not be published |
| `materials/manifest.json` | Private patent drafts and extracted design materials; must not be published |
| `materials/patents/专利实质审查书.txt` | Private patent drafts and extracted design materials; must not be published |
| `materials/patents/专利权利要求书.txt` | Private patent drafts and extracted design materials; must not be published |
| `materials/patents/专利说明书.txt` | Private patent drafts and extracted design materials; must not be published |
| `materials/patents/专利说明摘要.txt` | Private patent drafts and extracted design materials; must not be published |
| `materials/patents/专利请求书.txt` | Private patent drafts and extracted design materials; must not be published |
| `references.bib` | Duplicate source; the submitted copy is published under paper/ |
| `ziJPFatU` | Temporary zip (contains main.bbl only); no public value |

## Before publishing

Run `python3 tools/check_public_release.py`. It fails on any newly added
private file, credential-like string, or hash drift not listed in
`tools/known_hash_drift.json`.
