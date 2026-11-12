#!/usr/bin/env python3
"""Reconstructed ``bench_ai.py``: on-device AI feasibility and contention.

October 2026 original was lost. This rebuild follows the procedure described in
Extended Abstract v1.0 Sec. 5 and paper/main.tex Sec. 6:

  * YOLOv8n at 320 and 640, FP32 CUDA, n=60 per run, latency distribution;
  * Whisper-small RTF on 10 s audio (and a 2 s clip);
  * NLLB-200 greedy fp16, 5 runs for short/mid/long text;
  * ChatTTS synthesis RTF;
  * three-model residency (YOLO + Whisper + NLLB) and YOLO contention latency;
  * 15 W vs MAXN power sensitivity (the script NEVER changes the power mode).

SAFETY / EVIDENCE
  * Default ``--mode probe`` only probes the environment. Every measured field
    stays null. It does not import CUDA, load a model or touch the robot.
  * ``--mode device`` performs real inference and must be run on the Xavier in a
    recoverable environment with the original models. It requires
    ``--confirm-device-measurement``. If a dependency is missing it records the
    failure instead of inventing a number.
  * The script never runs nvpmodel to change clocks; it only records the mode.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import (  # noqa: E402
    HISTORICAL, add_mode_argument, check_device_confirmation, command_output,
    envelope, summarize, write_result,
)

DEFAULT_MODELS = {
    "yolo": os.environ.get("GEDGEBOT_YOLO_MODEL", "/root/src/sample/yolov8n.pt"),
    "whisper": os.environ.get(
        "GEDGEBOT_WHISPER_MODEL",
        "/root/src/gedge_bot/VoiceAsistant/.hf_cache/hub/models--openai--whisper-small"),
    "nllb": os.environ.get("GEDGEBOT_NLLB_MODEL", "/root/src/sample/models/nllb-200"),
}

NLLB_PROMPTS = {
    "short": "Hello, how are you?",
    "mid": "The robot will now turn toward the speaker and begin following.",
    "long": ("Compute regeneration reorganizes retired edge AI silicon into a new "
             "product by keeping non-deterministic workloads on the old accelerator "
             "and moving hard real-time control to a deterministic microcontroller."),
}

WORKLOAD_NAMES = ["yolo_320", "yolo_640", "whisper_10s", "whisper_2s_clip",
                  "nllb_short", "nllb_mid", "nllb_long", "chattts_long"]


def empty_workloads():
    return {name: {"status": "not_measured", "value": None,
                   "reason": "local probe; no model was loaded"}
            for name in WORKLOAD_NAMES}


def unavailable(name, reason, result):
    result["workloads"][name] = {"status": "unavailable", "value": None,
                                 "reason": reason}


def gpu_sync(torch):
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def timed(torch, fn):
    gpu_sync(torch)
    start = time.perf_counter()
    fn()
    gpu_sync(torch)
    return (time.perf_counter() - start) * 1000.0


def run_yolo(name, imgsz, iterations, model_path, result):
    try:
        import numpy as np
        import torch
        from ultralytics import YOLO
    except Exception as exc:
        unavailable(name, "import failed: %s: %s" % (type(exc).__name__, exc), result)
        return None
    if not Path(model_path).exists():
        unavailable(name, "model file not found: %s" % model_path, result)
        return None
    if not torch.cuda.is_available():
        unavailable(name, "CUDA unavailable (FP32 CUDA benchmark required)", result)
        return None
    try:
        model = YOLO(model_path)
        image = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        for _ in range(3):  # warm-up
            model.predict(image, imgsz=imgsz, device=0, verbose=False)
        latencies = [timed(torch, lambda: model.predict(
            image, imgsz=imgsz, device=0, verbose=False))
                     for _ in range(iterations)]
        stats = summarize(latencies)
        result["workloads"][name] = {
            "status": "ok",
            "value": {
                "imgsz": imgsz, "precision": "FP32", "device": "CUDA",
                "n": stats["n"], "latency_mean_ms": stats["mean"],
                "latency_p50_ms": stats["p50"], "latency_p99_ms": stats["p99"],
                "latency_pstdev_ms": stats["pstdev"],
                "latency_max_ms": stats["max"],
                "fps_from_mean": 1000.0 / stats["mean"] if stats["mean"] else None,
                "latencies_ms": latencies,
            },
            "reason": None,
        }
        return model
    except Exception as exc:
        unavailable(name, "inference failed: %s: %s" % (type(exc).__name__, exc), result)
        return None


def run_whisper(name, audio_path, clip_seconds, result):
    if not audio_path or not Path(audio_path).exists():
        unavailable(name, "audio file not supplied/found (--audio required for device mode)", result)
        return None
    try:
        import soundfile as sf
        import torch
        from transformers import pipeline
    except Exception as exc:
        unavailable(name, "import failed: %s: %s" % (type(exc).__name__, exc), result)
        return None
    try:
        audio, rate = sf.read(audio_path, dtype="float32")
        if clip_seconds:
            audio = audio[:int(rate * clip_seconds)]
        duration_s = len(audio) / float(rate)
        asr = pipeline("automatic-speech-recognition", model=DEFAULT_MODELS["whisper"],
                       device=0 if torch.cuda.is_available() else -1)
        elapsed = timed(torch, lambda: asr({"raw": audio, "sampling_rate": rate}))
        processing_s = elapsed / 1000.0
        result["workloads"][name] = {
            "status": "ok",
            "value": {"audio_path": str(audio_path), "audio_duration_s": duration_s,
                      "processing_s": processing_s,
                      "rtf": processing_s / duration_s if duration_s else None,
                      "transcript": None,
                      "note": "transcript deliberately not stored; measurement only"},
            "reason": None,
        }
    except Exception as exc:
        unavailable(name, "ASR failed: %s: %s" % (type(exc).__name__, exc), result)


def run_nllb(name, prompt_key, iterations, model_path, result):
    if not Path(model_path).exists():
        unavailable(name, "model dir not found: %s" % model_path, result)
        return None
    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except Exception as exc:
        unavailable(name, "import failed: %s: %s" % (type(exc).__name__, exc), result)
        return None
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(
            model_path, torch_dtype=torch.float16, local_files_only=True).to("cuda")
        inputs = tokenizer(NLLB_PROMPTS[prompt_key], return_tensors="pt").to("cuda")
        durations = []
        output_tokens = 0
        for _ in range(iterations):
            gpu_sync(torch)
            start = time.perf_counter()
            output = model.generate(**inputs, max_new_tokens=64, do_sample=False)
            gpu_sync(torch)
            durations.append(time.perf_counter() - start)
            output_tokens = output.shape[1]
        stats = summarize(durations)
        result["workloads"][name] = {
            "status": "ok",
            "value": {"prompt_key": prompt_key, "prompt_chars": len(NLLB_PROMPTS[prompt_key]),
                      "decoding": "greedy", "dtype": "fp16", "n": stats["n"],
                      "latency_mean_s": stats["mean"], "latency_pstdev_s": stats["pstdev"],
                      "latency_cv": stats["cv"], "output_tokens": int(output_tokens),
                      "tokens_per_s": output_tokens / stats["mean"] if stats["mean"] else None,
                      "durations_s": durations},
            "reason": None,
        }
        return model
    except Exception as exc:
        unavailable(name, "generation failed: %s: %s" % (type(exc).__name__, exc), result)
        return None


def run_chattts(result):
    try:
        import ChatTTS  # noqa: F401
    except Exception as exc:
        unavailable("chattts_long",
                    "ChatTTS unavailable: %s: %s" % (type(exc).__name__, exc), result)
        return
    unavailable("chattts_long",
                "automatic ChatTTS RTF requires an operator-supplied reference "
                "text/audio duration; not run to avoid inventing a denominator", result)


def run_concurrency(iterations, yolo_model_path, result):
    """Three-model residency + YOLO latency under contention."""
    try:
        import numpy as np
        import torch
        from ultralytics import YOLO
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline
    except Exception as exc:
        result["concurrency"] = {"status": "unavailable", "value": None,
                                 "reason": "import failed: %s: %s" % (type(exc).__name__, exc)}
        return
    try:
        yolo = YOLO(yolo_model_path)
        asr = pipeline("automatic-speech-recognition", model=DEFAULT_MODELS["whisper"],
                       device=0 if torch.cuda.is_available() else -1)
        tokenizer = AutoTokenizer.from_pretrained(DEFAULT_MODELS["nllb"], local_files_only=True)
        mt = AutoModelForSeq2SeqLM.from_pretrained(
            DEFAULT_MODELS["nllb"], torch_dtype=torch.float16,
            local_files_only=True).to("cuda")
        del tokenizer
        gpu_sync(torch)
        torch_mb = torch.cuda.memory_allocated() / (1024.0 * 1024.0)
        smi = command_output(["nvidia-smi", "--query-gpu=memory.used",
                              "--format=csv,noheader,nounits"])
        image = np.zeros((320, 320, 3), dtype=np.uint8)
        for _ in range(3):
            yolo.predict(image, imgsz=320, device=0, verbose=False)
        latencies = [timed(torch, lambda: yolo.predict(
            image, imgsz=320, device=0, verbose=False))
                     for _ in range(iterations)]
        stats = summarize(latencies)
        result["concurrency"] = {
            "status": "ok",
            "value": {
                "models_resident": ["YOLOv8n@320", "Whisper-small", "NLLB-200"],
                "torch_allocated_mb": torch_mb,
                "nvidia_smi_used_mb_raw": smi,
                "yolo_latency_mean_ms": stats["mean"],
                "yolo_latency_p99_ms": stats["p99"],
                "yolo_baseline_mean_ms": None,
                "latencies_ms": latencies,
                "memory_measurement_note": "torch.cuda.memory_allocated covers PyTorch "
                    "only; nvidia-smi covers all processes. Neither is guaranteed to "
                    "match the historical 1782 MB estimator.",
            },
            "reason": None,
        }
        # keep references alive until the measurement is complete
        del yolo, asr, mt
    except Exception as exc:
        result["concurrency"] = {"status": "unavailable", "value": None,
                                 "reason": "%s: %s" % (type(exc).__name__, exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_mode_argument(parser)
    parser.add_argument("--run-id", type=int, default=1, choices=[1, 2, 3])
    parser.add_argument("--iterations", type=int, default=60,
                        help="YOLO iterations per imgsz (historical n=60)")
    parser.add_argument("--nllb-runs", type=int, default=5)
    parser.add_argument("--power-mode", choices=["15W", "MAXN"], default="15W",
                        help="label for the run; the script never changes clocks")
    parser.add_argument("--audio", help="10 s wav file for the Whisper RTF benchmark")
    parser.add_argument("--yolo-model", default=DEFAULT_MODELS["yolo"])
    parser.add_argument("--nllb-model", default=DEFAULT_MODELS["nllb"])
    args = parser.parse_args()
    check_device_confirmation(parser, args)

    result = envelope("gedgebot.bench_ai.rebuilt.v1", __file__, args.mode,
                      run_id=args.run_id,
                      condition={
                          "power_mode_requested": args.power_mode,
                          "power_mode_observed": (command_output(["nvpmodel", "-q"])
                                                  if args.mode == "device" else None),
                          "iterations_per_workload": args.iterations,
                          "nllb_runs_per_length": args.nllb_runs,
                          "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
                          "model_paths": DEFAULT_MODELS,
                      },
                      workloads=empty_workloads(),
                      concurrency={"status": "not_measured", "value": None,
                                   "reason": "local probe; no model was loaded"},
                      power_sensitivity={
                          "status": "not_measured", "value": None,
                          "reason": "requires two separate runs at 15 W and MAXN; "
                                    "this script does not switch power modes",
                      },
                      historical_bench_ai=HISTORICAL["bench_ai"],
                      errors=[])

    if args.mode == "device":
        result["measurement_attempted"] = True
        run_yolo("yolo_320", 320, args.iterations, args.yolo_model, result)
        run_yolo("yolo_640", 640, args.iterations, args.yolo_model, result)
        run_whisper("whisper_10s", args.audio, None, result)
        run_whisper("whisper_2s_clip", args.audio, 2.0, result)
        for key in ("short", "mid", "long"):
            run_nllb("nllb_" + key, key, args.nllb_runs, args.nllb_model, result)
        run_chattts(result)
        run_concurrency(args.iterations, args.yolo_model, result)
        measured = sum(1 for w in result["workloads"].values() if w["status"] == "ok")
        result["measurement_summary"] = {
            "workloads_ok": measured,
            "workloads_total": len(result["workloads"]),
            "concurrency_ok": result["concurrency"]["status"] == "ok",
        }
        if measured == 0:
            result["evidence_status"] = "device_measurement_failed"
            result["device_measurement"] = False
            result["errors"].append("no workload could be measured; see per-workload reasons")
        else:
            result["evidence_status"] = "device_measurement_partial"
            result["device_measurement"] = True
            result["citation_allowed"] = False
            result["evidence_note"] = (
                "New device measurement produced by the reconstructed script. "
                "It is NOT the recovered October run. Review per-workload "
                "environment and estimator definitions before citing.")
    else:
        result["measurement_attempted"] = False

    out = write_result(args.output, result)
    print("mode=%s evidence_status=%s output=%s" %
          (args.mode, result["evidence_status"], out))


if __name__ == "__main__":
    main()
