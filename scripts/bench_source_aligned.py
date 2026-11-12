#!/usr/bin/env python3
"""Source-aligned device benchmark for the GEdgeBot paper.

Unlike ``bench_ai.py`` (which was reconstructed from the Extended Abstract),
this script copies the *actual* inference parameters found in the device source
tree ``/root/src/gedge_bot`` (HEAD c25b444, audited 2026-11-07):

* YOLO      ``YOLO(...).to("cuda:0")`` + ``model.model.fuse()``, three 480x640
            warm-up frames, ``conf=0.35``, ``classes=[0]``
            (RobotControl/robot_runtime.py:697-703, 813)
* Whisper   ``pipeline(..., torch_dtype=torch.float16)``, 1 s silence warm-up
            with ``generate_kwargs={"language": "chinese"}``, same language hint
            at inference (common-ai-services/asr_service/service.py:80-98, 123)
* NLLB      ``.to("cuda").half()``, ``forced_bos_token_id``, ``num_beams=5``,
            ``repetition_penalty=1.1``, ``no_repeat_ngram_size=3``,
            ``early_stopping=True``, ``max_length=256``, ``synced_gpus=False``
            (common-ai-services/translation_service/service.py:108-114, 153-162)

A greedy control is measured alongside the beam-5 profile so the two decoding
conventions can be compared on the same hardware. This produces a NEW
measurement, never a recovered October artifact. See
``evidence/SOURCE_ALIGNMENT.md`` for the full audit.

Modes: ``probe`` (default, no model is loaded) and ``device`` (requires
``--confirm-device-measurement``).
"""
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

SOURCE_ROOT = os.environ.get("GEDGEBOT_SOURCE_ROOT", "/root/src/gedge_bot")

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

SOURCE_EVIDENCE = {
    "yolo": {
        "file": "RobotControl/robot_runtime.py",
        "lines": "697-703, 813, 1394-1399",
        "settings": {"device": "cuda:0", "fuse": True, "warmup_frames": 3,
                     "warmup_shape": [480, 640, 3], "conf": 0.35, "classes": [0],
                     "imgsz_default": 320},
    },
    "whisper": {
        "file": "common-ai-services/asr_service/service.py",
        "lines": "80-98, 123",
        "settings": {"model": "openai/whisper-small", "dtype": "float16",
                     "warmup": "1 s silence, language=chinese",
                     "generate_kwargs": {"language": "chinese"}},
        "config": "common-ai-services/common/config.py:27,37",
    },
    "nllb": {
        "file": "common-ai-services/translation_service/service.py",
        "lines": "108-114, 153-162",
        "settings": {"dtype": "float16 (via .half())",
                     "num_beams": 5, "repetition_penalty": 1.1,
                     "no_repeat_ngram_size": 3, "early_stopping": True,
                     "max_length": 256, "forced_bos_token_id": "eng_Latn",
                     "synced_gpus": False},
        "config": "common-ai-services/common/config.py:28,107",
    },
    "chattts": {
        "file": "VoiceAsistant/chattts_server.py",
        "lines": "patch_jetson_torch()",
        "note": "measured separately by scripts/chattts_bench.py",
    },
}

WORKLOAD_NAMES = [
    "yolo_320", "yolo_640", "whisper_10s", "whisper_2s_clip",
    "nllb_short", "nllb_mid", "nllb_long",
    "nllb_short_greedy", "nllb_mid_greedy", "nllb_long_greedy",
    "chattts_long",
]


def empty_workloads():
    return {name: {"status": "not_measured", "value": None, "reason": None}
            for name in WORKLOAD_NAMES}


def unavailable(name, reason, result):
    result["workloads"][name] = {"status": "unavailable", "value": None,
                                 "reason": reason}


def ok(name, value, result):
    result["workloads"][name] = {"status": "ok", "value": value, "reason": None}


def gpu_sync(torch):
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def timed_ms(torch, fn):
    gpu_sync(torch)
    start = time.perf_counter()
    fn()
    gpu_sync(torch)
    return (time.perf_counter() - start) * 1000.0


def timed_s(torch, fn):
    return timed_ms(torch, fn) / 1000.0


def run_yolo(name, imgsz, iterations, model_path, result):
    if not Path(model_path).exists():
        unavailable(name, "model not found: %s" % model_path, result)
        return
    try:
        import numpy as np
        import torch
        from ultralytics import YOLO
    except Exception as exc:
        unavailable(name, "import failed: %s: %s" % (type(exc).__name__, exc), result)
        return
    try:
        model = YOLO(model_path)
        model.to("cuda:0")
        model.model.fuse()
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        for _ in range(3):
            model.predict(dummy, device="cuda:0", imgsz=imgsz, conf=0.35,
                          classes=[0], verbose=False)
        frame = np.random.default_rng(20261107).integers(
            0, 256, size=(480, 640, 3), dtype=np.uint8)
        latencies = [timed_ms(torch, lambda: model.predict(
            frame, device="cuda:0", imgsz=imgsz, conf=0.35,
            classes=[0], verbose=False)) for _ in range(iterations)]
        stats = summarize(latencies)
        ok(name, {
            "imgsz": imgsz, "precision": "FP32 (ultralytics default)",
            "device": "cuda:0", "fused": True, "conf": 0.35, "classes": [0],
            "frame_shape": [480, 640, 3], "n": stats["n"],
            "latency_mean_ms": stats["mean"], "latency_pstdev_ms": stats["pstdev"],
            "latency_p50_ms": stats["p50"], "latency_p99_ms": stats["p99"],
            "latency_min_ms": stats["min"], "latency_max_ms": stats["max"],
            "fps_from_mean": 1000.0 / stats["mean"] if stats["mean"] else None,
            "latencies_ms": latencies,
            "profile": "source_aligned",
        }, result)
    except Exception as exc:
        unavailable(name, "inference failed: %s: %s" % (type(exc).__name__, exc), result)


class WhisperHarness:
    """Source-aligned ASR pipeline, loaded once, warmed up once."""

    def __init__(self, model_path, result):
        self.result = result
        self.error = None
        self.pipeline = None
        self.rate = 16000
        try:
            import numpy as np  # noqa: F401
            import torch
            from transformers import pipeline
            self.torch = torch
            self.np = np
            self.pipeline = pipeline(
                "automatic-speech-recognition", model=model_path,
                device=0 if torch.cuda.is_available() else -1,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            )
            warm = np.zeros(16000, dtype=np.float32)
            warm_s = timed_s(torch, lambda: self.pipeline(
                warm, generate_kwargs={"language": "chinese"}))
            self.result["whisper_warmup"] = {
                "status": "ok",
                "value": {"description": "1 s silence, language=chinese; includes "
                                         "CUDA/cuDNN first-call initialization",
                          "duration_s": warm_s, "audio_duration_s": 1.0,
                          "warmup_rtf": warm_s},
            }
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.result["whisper_warmup"] = {"status": "unavailable",
                                             "value": None, "reason": self.error}

    def measure(self, audio_path, clip_seconds, name):
        if self.pipeline is None:
            unavailable(name, "pipeline unavailable: %s" % self.error, self.result)
            return
        try:
            import soundfile as sf
            audio, rate = sf.read(audio_path, dtype="float32")
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            if clip_seconds:
                audio = audio[:int(rate * clip_seconds)]
            duration_s = len(audio) / float(rate)
            elapsed_s = timed_s(self.torch, lambda: self.pipeline(
                audio, generate_kwargs={"language": "chinese"}))
            ok(name, {
                "audio_path": str(audio_path), "audio_duration_s": duration_s,
                "sampling_rate": rate, "processing_s": elapsed_s,
                "rtf": elapsed_s / duration_s if duration_s else None,
                "dtype": "float16", "language_hint": "chinese",
                "warmed_up": True, "transcript": None,
                "note": "transcript deliberately not stored; steady-state measurement "
                        "after the source-style warm-up",
                "profile": "source_aligned",
            }, self.result)
        except Exception as exc:
            unavailable(name, "ASR failed: %s: %s" % (type(exc).__name__, exc),
                        self.result)


def run_nllb_suite(model_path, runs, max_length, result):
    """Load the 2.3 GB model once; measure beam-5 (source) and greedy (control)."""
    if not Path(model_path).exists():
        for suffix in ("", "_greedy"):
            for key in NLLB_PROMPTS:
                unavailable("nllb_" + key + suffix,
                            "model dir not found: %s" % model_path, result)
        return
    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM
        try:
            from transformers import NllbTokenizer
            tokenizer_cls = NllbTokenizer
            tokenizer_name = "NllbTokenizer"
        except ImportError:
            from transformers import AutoTokenizer
            tokenizer_cls = AutoTokenizer
            tokenizer_name = "AutoTokenizer (NllbTokenizer unavailable)"
    except Exception as exc:
        for suffix in ("", "_greedy"):
            for key in NLLB_PROMPTS:
                unavailable("nllb_" + key + suffix,
                            "import failed: %s: %s" % (type(exc).__name__, exc), result)
        return
    try:
        tokenizer = tokenizer_cls.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(
            model_path, local_files_only=True).to("cuda").half()
        tokenizer.src_lang = "zho_Hans"
        tgt_token_id = tokenizer.convert_tokens_to_ids("eng_Latn")
        result["nllb_setup"] = {
            "tokenizer": tokenizer_name, "model_path": str(model_path),
            "src_lang": "zho_Hans", "forced_bos_token": "eng_Latn",
            "forced_bos_token_id": int(tgt_token_id),
            "dtype": "float16 (.half())",
            "note": "language tags follow the service defaults; prompts are English "
                    "because the historical prompt text was English. Only latency "
                    "is measured, so the mismatch does not affect timing.",
        }
    except Exception as exc:
        for suffix in ("", "_greedy"):
            for key in NLLB_PROMPTS:
                unavailable("nllb_" + key + suffix,
                            "model load failed: %s: %s" % (type(exc).__name__, exc), result)
        return

    for key, prompt in NLLB_PROMPTS.items():
        inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
        for profile in ("beam5_source", "greedy_control"):
            durations = []
            output_tokens = 0
            for _ in range(runs):
                if profile == "beam5_source":
                    def call():
                        return model.generate(
                            **inputs, forced_bos_token_id=tgt_token_id,
                            max_length=max_length, num_beams=5,
                            repetition_penalty=1.1, no_repeat_ngram_size=3,
                            early_stopping=True, synced_gpus=False)
                else:
                    def call():
                        return model.generate(
                            **inputs, do_sample=False, max_new_tokens=64)
                try:
                    out = None
                    gpu_sync(torch)
                    start = time.perf_counter()
                    out = call()
                    gpu_sync(torch)
                    durations.append(time.perf_counter() - start)
                    output_tokens = out.shape[1]
                except Exception as exc:
                    reason = "generation failed: %s: %s" % (type(exc).__name__, exc)
                    name = "nllb_" + key + ("" if profile == "beam5_source" else "_greedy")
                    unavailable(name, reason, result)
                    break
            else:
                stats = summarize(durations)
                name = "nllb_" + key + ("" if profile == "beam5_source" else "_greedy")
                ok(name, {
                    "prompt_key": key, "prompt_chars": len(prompt),
                    "decoding": profile, "dtype": "fp16", "n": stats["n"],
                    "latency_mean_s": stats["mean"],
                    "latency_pstdev_s": stats["pstdev"],
                    "latency_cv": stats["cv"],
                    "output_tokens": int(output_tokens),
                    "tokens_per_s": output_tokens / stats["mean"] if stats["mean"] else None,
                    "durations_s": durations,
                    "generate_kwargs": ({"num_beams": 5, "max_length": max_length,
                                         "repetition_penalty": 1.1,
                                         "no_repeat_ngram_size": 3,
                                         "early_stopping": True,
                                         "forced_bos_token_id": int(tgt_token_id)}
                                        if profile == "beam5_source"
                                        else {"do_sample": False, "max_new_tokens": 64}),
                    "profile": "source_aligned" if profile == "beam5_source" else "control",
                }, result)


def run_concurrency(iterations, paths, result):
    try:
        import numpy as np
        import torch
        from ultralytics import YOLO
        from transformers import AutoModelForSeq2SeqLM, pipeline
    except Exception as exc:
        result["concurrency"] = {"status": "unavailable", "value": None,
                                 "reason": "import failed: %s: %s" % (type(exc).__name__, exc)}
        return
    try:
        yolo = YOLO(paths["yolo"])
        yolo.to("cuda:0")
        yolo.model.fuse()
        asr = pipeline("automatic-speech-recognition", model=paths["whisper"],
                       device=0, torch_dtype=torch.float16)
        mt = AutoModelForSeq2SeqLM.from_pretrained(
            paths["nllb"], local_files_only=True).to("cuda").half()
        gpu_sync(torch)
        torch_mb = torch.cuda.memory_allocated() / (1024.0 * 1024.0)
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        for _ in range(3):
            yolo.predict(image, device="cuda:0", imgsz=320, conf=0.35,
                         classes=[0], verbose=False)
        latencies = [timed_ms(torch, lambda: yolo.predict(
            image, device="cuda:0", imgsz=320, conf=0.35,
            classes=[0], verbose=False)) for _ in range(iterations)]
        stats = summarize(latencies)
        result["concurrency"] = {
            "status": "ok",
            "value": {
                "models_resident": ["YOLOv8n (fused)", "Whisper-small FP16", "NLLB-200 FP16"],
                "torch_allocated_mb": torch_mb,
                "nvidia_smi_used_mb_raw": command_output(
                    ["nvidia-smi", "--query-gpu=memory.used",
                     "--format=csv,noheader,nounits"]),
                "yolo_latency_mean_ms": stats["mean"],
                "yolo_latency_p99_ms": stats["p99"],
                "yolo_baseline_mean_ms": None,
                "latencies_ms": latencies,
                "memory_measurement_note": "torch.cuda.memory_allocated covers PyTorch "
                    "only; Jetson has no nvidia-smi. Not comparable to the historical "
                    "1782 MB estimator.",
                "profile": "source_aligned",
            },
            "reason": None,
        }
        del yolo, asr, mt
    except Exception as exc:
        result["concurrency"] = {"status": "unavailable", "value": None,
                                 "reason": "%s: %s" % (type(exc).__name__, exc)}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_mode_argument(parser)
    parser.add_argument("--run-id", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=60,
                        help="YOLO iterations per imgsz (historical n=60)")
    parser.add_argument("--nllb-runs", type=int, default=3,
                        help="generate calls per prompt per decoding profile")
    parser.add_argument("--nllb-max-length", type=int, default=256)
    parser.add_argument("--power-mode", choices=["15W", "MAXN"], default="MAXN",
                        help="label only; the script never changes clocks")
    parser.add_argument("--audio", help="10 s wav file for the Whisper RTF benchmark")
    parser.add_argument("--yolo-model", default=DEFAULT_MODELS["yolo"])
    parser.add_argument("--whisper-model", default=DEFAULT_MODELS["whisper"])
    parser.add_argument("--nllb-model", default=DEFAULT_MODELS["nllb"])
    args = parser.parse_args()
    check_device_confirmation(parser, args)

    paths = {"yolo": args.yolo_model, "whisper": args.whisper_model,
             "nllb": args.nllb_model}
    commit = command_output(["git", "-C", SOURCE_ROOT, "rev-parse", "HEAD"])
    result = envelope(
        "gedgebot.bench_ai.source_aligned.v1", __file__, args.mode,
        profile="source_aligned",
        source_root=SOURCE_ROOT,
        source_commit=commit,
        source_evidence=SOURCE_EVIDENCE,
        source_audit_doc="evidence/SOURCE_ALIGNMENT.md",
        condition={
            "power_mode_requested": args.power_mode,
            "power_mode_observed": (command_output(["nvpmodel", "-q"])
                                    if args.mode == "device" else None),
            "iterations_per_workload": args.iterations,
            "nllb_runs_per_prompt": args.nllb_runs,
            "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
            "transformers_offline": os.environ.get("TRANSFORMERS_OFFLINE"),
            "model_paths": paths,
        },
        workloads=empty_workloads(),
        whisper_warmup={"status": "not_measured", "value": None, "reason": None},
        nllb_setup=None,
        concurrency={"status": "not_measured", "value": None,
                     "reason": "local probe; no model was loaded"},
        historical_reference_full=HISTORICAL["bench_ai"],
        errors=[],
    )
    result["evidence_note"] = (
        "NEW device measurement using parameters copied from the device source "
        "tree (see source_evidence). It is NOT the recovered October run and does "
        "not prove equivalence with the historical estimator. citation_allowed "
        "remains false until manual review."
    )

    if args.mode == "device":
        result["measurement_attempted"] = True
        run_yolo("yolo_320", 320, args.iterations, args.yolo_model, result)
        run_yolo("yolo_640", 640, args.iterations, args.yolo_model, result)
        if not args.audio or not Path(args.audio).exists():
            unavailable("whisper_10s", "audio file not supplied/found (--audio)", result)
            unavailable("whisper_2s_clip", "audio file not supplied/found (--audio)", result)
        else:
            harness = WhisperHarness(args.whisper_model, result)
            harness.measure(args.audio, None, "whisper_10s")
            harness.measure(args.audio, 2.0, "whisper_2s_clip")
        run_nllb_suite(args.nllb_model, args.nllb_runs,
                       args.nllb_max_length, result)
        unavailable("chattts_long",
                    "measured separately by scripts/chattts_bench.py; not duplicated here",
                    result)
        run_concurrency(args.iterations, paths, result)
        measured = sum(1 for w in result["workloads"].values() if w["status"] == "ok")
        result["measurement_summary"] = {
            "workloads_ok": measured,
            "workloads_total": len(result["workloads"]),
            "concurrency_ok": result["concurrency"]["status"] == "ok",
        }
        if measured == 0:
            result["evidence_status"] = "device_measurement_failed"
            result["device_measurement"] = False
            result["errors"].append("no workload could be measured; see reasons")
        else:
            result["evidence_status"] = "device_measurement_partial"
            result["device_measurement"] = True
            result["citation_allowed"] = False
    else:
        result["measurement_attempted"] = False

    out = write_result(args.output, result)
    print("mode=%s profile=source_aligned evidence_status=%s output=%s" %
          (args.mode, result["evidence_status"], out))


if __name__ == "__main__":
    main()
