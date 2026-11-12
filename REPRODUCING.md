# Reproducing the measurements

These artifacts accompany **arXiv:2611.11698**. The measurement JSON files are
shipped byte-for-byte as recorded; several of them embed `script_sha256` hashes,
so the Python scripts under `scripts/` are likewise unmodified. Keep that
property if you extend this repository: run `python3 tools/check_public_release.py`
before every release.

## What can be reproduced without the robot

The analysis, replay and audit scripts operate on the shipped JSON records or on
a source tree that you supply. They need only a Python environment plus the two
packages in `requirements.txt`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/summarize_device_runs.py --help
python3 scripts/boundary_isolation.py --help
python3 scripts/full_runtime_replay.py --help
python3 tools/check_public_release.py
```

`product_loop_replay.py`, `full_runtime_replay.py`, `runtime_control_graph.py`,
`boundary_isolation.py` and `integration_surface.py` are software-in-the-loop
analyses driven by synthetic scenario events and a virtual clock. They establish
source-level behaviour only; they are **not** physical measurements.

## What needs the device and proprietary sources

The AI benchmarks (`bench_ai.py`, `bench_voice.py`, `bench_source_aligned.py`,
`sensitivity_bench.py`, `ab_control.py`, `uart_timing.py`, `failsafe_test.py`,
`demo_capture.py`, `chattts_bench.py`, `yolo_load.py`, `cpu_load.py`, the
`gpu_smoke` / `astra_smoke` probes) were run on a Jetson AGX Xavier with a
private runtime source tree and locally cached model weights. They are included
for methodological transparency, not as a one-command reproduction.

Paths such as `/root/src/gedge_bot` and `/root/gedge_bot_validation_20261107`
are historical device paths, preserved verbatim so that recorded hashes stay
valid. Override them with:

| Variable | Purpose |
| --- | --- |
| `GEDGEBOT_SOURCE_ROOT` | Root of the runtime source tree used by static audits |
| `GEDGEBOT_YOLO_MODEL` | Path to the YOLO weights |
| `GEDGEBOT_WHISPER_MODEL` | Path to the Whisper model cache |
| `GEDGEBOT_NLLB_MODEL` | Path to the NLLB model |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | Offline model loading flags |

The on-device runner scripts in `evidence/device/run_*.sh` are verbatim field
logs; their `cd` targets and `nvpmodel` calls assume the original Jetson setup
and must be adapted to your own device. Never run motion or fault-injection
tests without mechanically securing the robot and having an operator present.

## Evidence status fields

Records carry fields such as `evidence_status`, `physical_measurement`,
`device_measurement`, `citation_allowed`, `no_physical_demonstration` and
`evidence_note`. Read them before quoting a number: several results are
contract-derived, trace-driven or reconstructed rather than physically observed.
The manuscript's "Limitations and Reproduction Plan" section lists the open
physical items (fault injection while moving, MCU pulse-pin waveform capture,
measured head/body/camera angles, a clean A/B baseline, and power-per-watt
measurements).

## Not included

Private patent materials, the proprietary runtime and firmware source
snapshots, internal audit notes, raw audio captures and internal reports are
deliberately excluded. See `AUDIT.md` for the complete inclusion/exclusion
record. The arXiv v1 manuscript states that no public artifact repository is
asserted; a v2 revision will replace that sentence with this repository and its
archived DOI.
