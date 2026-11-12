#!/usr/bin/env python3
"""Shared helpers for the rebuilt GEdgeBot October-2026 benchmark scripts.

Why this module exists
----------------------
The October 2026 scripts and raw JSON logs were lost (see
``backup_recovery/REPORT.md``).  The scripts in this directory reconstruct the
*measurement entry points*, not the measurements themselves.  Every JSON they
emit carries an explicit evidence status:

* ``local_probe_no_measurement`` - runs on any machine, performs only
  environment probing / pure-logic checks, and leaves measured values null.
* ``device_measurement``        - only set when a script actually executed a
  measurement against real hardware or a real model on that host.
* ``human_observation``         - values were imported from an operator's
  written record, not captured automatically.

Local probe output MUST NOT be cited as an experimental result, and MUST NOT
be pooled with the historical October values.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Historical October 2026 values, transcribed from Extended Abstract v1.0.
# These are a reference for comparison ONLY. They are not measurements produced
# by the rebuilt scripts and are never written into the `measurements` block.
# ---------------------------------------------------------------------------
HISTORICAL = {
    "source": "ExtendedAbstract_ComputeRegeneration_Luo.pdf v1.0 (2026-10-17), "
              "also transcribed in paper/main.tex Sec. 6",
    "status": "transcribed_historical_value_not_recovered_measurement",
    "measured_on": "2026-10-15..2026-10-17, single Jetson AGX Xavier prototype",
    "environment": {
        "board": "Jetson AGX Xavier 32GB, third-party carrier",
        "jetpack": "5.1.x / L4T r35",
        "cuda": "11.4",
        "tensorrt": "8.5",
        "pytorch": "2.1.0a0+nv23.6 (patched)",
        "python": "3.8",
        "power_modes": ["MODE_15W_DESKTOP", "MAXN"],
        "hf_hub_offline": True,
        "temperature_c_max": 43,
    },
    "bench_ai": {
        "yolo_320_15w": {"fps": 26.6, "mean_ms": 37.6, "p99_ms": 84.0, "n": 60},
        "yolo_640_15w": {"fps": 16.3, "mean_ms": 61.2, "n": 60},
        "whisper_10s_15w": {"rtf": 0.091},
        "whisper_2s_clip": {"rtf": 0.44},
        "nllb_single_model_15w": {
            "short_s": 0.42, "short_cv": 0.005,
            "mid_s": 1.02, "mid_cv": 0.098,
            "long_s": 1.55, "long_cv": 0.008,
            "tokens_per_s": "24-27", "runs_per_length": 5,
        },
        "chattts_long": {"rtf": 2.51, "realtime": False},
        "three_model_concurrency": {
            "gpu_memory_mb": 1782,
            "models": ["YOLOv8n@320", "Whisper-small", "NLLB-200"],
            "yolo_mean_ms": 59.0, "yolo_p99_ms": 173.5,
            "yolo_baseline_mean_ms": 37.6,
        },
        "power_sensitivity": {
            "yolo_320_fps": {"15W": 26.6, "MAXN": 28.0},
            "whisper_10s_rtf": {"15W": 0.091, "MAXN": 0.032},
            "nllb_short_s": {"15W": 1.33, "MAXN": 0.66},
        },
    },
    "bench_voice": {
        "asr": "Whisper-small",
        "mt": "NLLB-200 greedy fp16",
        "tts": "ChatTTS",
        "note": "Voice assistant failed to warm up within 60 s during the N=6 "
                "October demonstrations (voice=False degraded mode). No "
                "conversational latency was measured in those runs.",
    },
    "ab_control": {
        "period_ms": 50.0, "frames_per_condition": 200,
        "idle_15w": {"interval_std_ms": 0.063, "p99_deviation_ms": 0.24},
        "ai_loaded_15w": {"interval_std_ms": 13.37, "p99_deviation_ms": 49.93,
                          "max_slip_ms": 212},
        "ai_loaded_maxn": {"interval_std_ms": 11.41, "p99_deviation_ms": 49.92,
                           "max_slip_ms": 182},
        "ratio_reported": "208x",
    },
    "failsafe": {
        "F1_control_crash": "stop sending frames mid-drive; wheels stop after final pulse",
        "F2_cable_pull": "close serial port mid-drive; wheels stop immediately",
        "F3_protocol_errors": "garbage bytes / undefined opcode 0x55 / bad frame tail discarded",
        "limitation": "no hardware watchdog or command timeout",
    },
    "demo": {
        "runs": 6, "capture_windows": 5, "success_rate": "6/6",
        "trigger_to_follow_mean_s": 4.35, "spread_s": 0.19,
        "p50_s": 4.42, "min_s": 3.95, "max_s": 4.43, "cv_percent": 4.5,
        "doa_turn_angles_deg": [90, -53, 104, 110, -97, 114],
        "follow_segments": ["120 s x3", "30 s x2", "window-end x1"],
        "perception": {"yolo_fps": 17.7, "inference_ms": 37},
        "voice_degraded": True,
    },
}

DISCREPANCY_REGISTER = [
    {
        "id": "NLLB_SHORT_CONFLICT",
        "description": "NLLB-200 short-text latency at 15 W is 0.42 s in the "
                       "single-model table but 1.33 s in the power-sensitivity "
                       "table. Prompt text, run id and raw logs are lost.",
        "resolution": "unresolved; paper/main.tex excludes the 2.0x NLLB power "
                      "scaling from quantitative conclusions",
    },
    {
        "id": "MIC_FRAME_SPEC_DRIFT",
        "description": "Extended Abstract describes a mic frame with "
                       "header/seq/CRC/tail; the captured runtime uses "
                       "FF A5 | 01 00 | len | cmd | sub | payload | checksum.",
        "resolution": "rebuilt scripts target the captured runtime protocol",
    },
    {
        "id": "MOTOR_FRAME_SPEC_DRIFT",
        "description": "The captured motor frame is seven bytes with no "
                       "sequence number and no CRC field.",
        "resolution": "rebuilt protocol guard checks the seven-byte form",
    },
]


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def platform_info():
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "hostname": platform.node(),
    }
    release = Path("/etc/nv_tegra_release")
    info["l4t_release"] = release.read_text().strip() if release.exists() else None
    info["is_jetson"] = release.exists() or platform.machine() == "aarch64"
    return info


def command_output(argv, timeout=5):
    try:
        return subprocess.check_output(argv, stderr=subprocess.STDOUT,
                                       timeout=timeout, text=True).strip()
    except Exception as exc:
        return "unavailable: %s: %s" % (type(exc).__name__, exc)


def probe_environment(model_paths=None):
    """Read-only capability probe. Never imports or modifies a CUDA runtime."""
    env = platform_info()
    env["power_mode"] = (command_output(["nvpmodel", "-q"])
                         if env["is_jetson"] else "not Jetson")
    env["hf_hub_offline"] = os.environ.get("HF_HUB_OFFLINE")
    try:
        import importlib.util
        env["torch_importable"] = importlib.util.find_spec("torch") is not None
        env["ultralytics_importable"] = importlib.util.find_spec("ultralytics") is not None
        env["transformers_importable"] = importlib.util.find_spec("transformers") is not None
    except Exception as exc:
        env["dependency_probe_error"] = "%s: %s" % (type(exc).__name__, exc)
    if model_paths:
        env["model_paths"] = {name: {"path": str(p), "exists": Path(p).exists()}
                              for name, p in model_paths.items()}
    return env


def envelope(schema, script_path, mode, **extra):
    """Common evidence envelope. `citation_allowed` is deliberately strict."""
    status = {
        "probe": "local_probe_no_measurement",
        "device": "device_measurement",
        "manual": "human_observation",
    }[mode]
    payload = {
        "schema": schema,
        "generated_at_utc": utc_now_iso(),
        "timestamp_unix": datetime.datetime.now().timestamp(),
        "script": str(Path(script_path).resolve()),
        "script_sha256": sha256_file(script_path),
        "mode": mode,
        "evidence_status": status,
        "device_measurement": mode == "device",
        "citation_allowed": False,
        "evidence_note": (
            "Reconstructed script, not a recovered October 2026 artifact. "
            "Local probe runs do not measure the robot or any AI model and "
            "must not be cited as experimental results."
        ),
        "environment": probe_environment(),
        "historical_reference": {
            "status": HISTORICAL["status"],
            "source": HISTORICAL["source"],
            "measured_on": HISTORICAL["measured_on"],
        },
        "discrepancy_register": DISCREPANCY_REGISTER,
    }
    payload.update(extra)
    return payload


def write_result(path, payload):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return out


def percentile_nearest(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def summarize(values):
    """Population stdev + nearest-rank percentiles, matching main.tex Sec. 6."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {"n": 0, "mean": None, "pstdev": None, "cv": None,
                "p50": None, "p99": None, "min": None, "max": None}
    mean = statistics.fmean(clean)
    stdev = statistics.pstdev(clean) if len(clean) > 1 else 0.0
    return {
        "n": len(clean),
        "mean": mean,
        "pstdev": stdev,
        "cv": (stdev / mean) if mean else None,
        "p50": percentile_nearest(clean, 0.50),
        "p99": percentile_nearest(clean, 0.99),
        "min": min(clean),
        "max": max(clean),
    }


def add_mode_argument(parser, modes=("probe", "device")):
    parser.add_argument("--mode", choices=list(modes), default="probe",
                        help="probe is safe on any host; device requires real hardware/models")
    parser.add_argument("--output", required=True, help="destination JSON path")
    parser.add_argument("--confirm-device-measurement", action="store_true",
                        help="explicit acknowledgement required for --mode device")


def check_device_confirmation(parser, args):
    if args.mode == "device" and not args.confirm_device_measurement:
        parser.error("--mode device requires --confirm-device-measurement; "
                     "read paper/evidence/REBUILT_BENCHMARKS.md first")
