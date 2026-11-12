#!/usr/bin/env python3
"""Parameter-sensitivity measurements for the unresolved historical numbers.

Two discrepancies in ``main.tex`` cannot be settled by another single-point
benchmark:

1. Whisper-small 10 s RTF: October 0.091, first reconstructed run 2.272,
   source-aligned run 0.172. Hypothesis: FP32 + no warm-up + a cold CUDA/cuDNN
   first call, not a hardware regression. This script measures an FP16+warm
   length sweep (1/2/5/10 s) and an FP32/no-warm control with the cold call
   isolated from steady state.
2. NLLB-200 short text: 0.42 s in the single-model table vs 1.33 s in the
   power-sensitivity table (both 15 W). Hypothesis: different prompt length /
   output tokens / decoding. This script sweeps prompt length x greedy/beam-5
   and records output-token counts alongside latency.

All numbers are NEW 2026-11-07 device measurements, never recovered October
artifacts. ``citation_allowed`` stays false until manual review.
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

SOURCE_ROOT = os.environ.get("GEDGEBOT_SOURCE_ROOT", "/root/src/gedge_bot")

NLLB_PROMPTS = {
    "short": "Hello, how are you?",
    "short_alt": "Hello, how are you? Please translate this sentence into English.",
    "mid": "The robot will now turn toward the speaker and begin following.",
    "long": ("Compute regeneration reorganizes retired edge AI silicon into a new "
             "product by keeping non-deterministic workloads on the old accelerator "
             "and moving hard real-time control to a deterministic microcontroller."),
}

WHISPER_LENGTHS_S = (1.0, 2.0, 5.0, 10.0)


def timed_call(torch, fn):
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    start = time.perf_counter()
    out = fn()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    return time.perf_counter() - start, out


def gpu_sync(torch):
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def whisper_profile(model_path, audio, rate, repeats, dtype_label, warm,
                    lengths, isolate_cold, torch, np):
    """Load one ASR pipeline, optionally warm it, then time calls per clip length."""
    from transformers import pipeline
    dtype = torch.float16 if dtype_label == "fp16" else torch.float32
    pipe = pipeline("automatic-speech-recognition", model=model_path,
                    device=0 if torch.cuda.is_available() else -1,
                    torch_dtype=dtype)
    info = {
        "dtype": dtype_label,
        "warmup": bool(warm),
        "language_hint": "chinese",
        "model_path": str(model_path),
        "sampling_rate": rate,
        "warmup_call_s": None,
        "cold_first_call_s": None,
        "clips": {},
    }
    if warm:
        silence = np.zeros(rate, dtype=np.float32)
        elapsed, _ = timed_call(
            torch, lambda: pipe(silence, generate_kwargs={"language": "chinese"}))
        info["warmup_call_s"] = elapsed
    for seconds in lengths:
        clip = audio[:int(rate * seconds)]
        duration = len(clip) / float(rate)
        durations = []
        for index in range(repeats):
            elapsed, _ = timed_call(
                torch, lambda: pipe(clip, generate_kwargs={"language": "chinese"}))
            if isolate_cold and index == 0 and seconds == max(lengths):
                info["cold_first_call_s"] = elapsed
            else:
                durations.append(elapsed)
        stats = summarize(durations)
        info["clips"]["%gs" % seconds] = {
            "audio_duration_s": duration,
            "n": stats["n"],
            "processing_mean_s": stats["mean"],
            "processing_pstdev_s": stats["pstdev"],
            "processing_s": durations,
            "rtf": (stats["mean"] / duration) if (duration and stats["mean"]) else None,
            "rtf_cold_first_call": (
                (info["cold_first_call_s"] / duration)
                if (isolate_cold and seconds == max(lengths)
                    and info["cold_first_call_s"] is not None and duration) else None),
        }
    del pipe
    gpu_sync(torch)
    try:
        torch.cuda.empty_cache()
    except Exception:
        pass
    return info


def run_whisper(args, audio, rate, result, torch, np):
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    try:
        if "fp16_warm" in profiles:
            result["whisper"]["fp16_warm"] = whisper_profile(
                args.whisper_model, audio, rate, args.repeats, "fp16", True,
                WHISPER_LENGTHS_S, False, torch, np)
        if "fp32_no_warm" in profiles:
            result["whisper"]["fp32_no_warm"] = whisper_profile(
                args.whisper_model, audio, rate, args.repeats + 1, "fp32", False,
                (10.0,), True, torch, np)
    except Exception as exc:
        result["whisper"]["error"] = "%s: %s" % (type(exc).__name__, exc)


def run_nllb(args, result, torch):
    try:
        from transformers import AutoModelForSeq2SeqLM
        try:
            from transformers import NllbTokenizer
            tokenizer = NllbTokenizer.from_pretrained(
                args.nllb_model, local_files_only=True)
            tokenizer_name = "NllbTokenizer"
        except ImportError:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                args.nllb_model, local_files_only=True)
            tokenizer_name = "AutoTokenizer"
        model = AutoModelForSeq2SeqLM.from_pretrained(
            args.nllb_model, local_files_only=True).to("cuda").half()
        tokenizer.src_lang = "zho_Hans"
        bos = tokenizer.convert_tokens_to_ids("eng_Latn")
        result["nllb"]["setup"] = {
            "tokenizer": tokenizer_name,
            "dtype": "float16",
            "src_lang": "zho_Hans",
            "forced_bos_token": "eng_Latn",
            "forced_bos_token_id": int(bos),
            "model_path": str(args.nllb_model),
            "note": "English prompts, as in the historical table; only latency "
                    "is measured, so the language-tag mismatch does not affect timing.",
        }

        def generate(inputs, profile):
            if profile == "beam5_source":
                return model.generate(
                    **inputs, forced_bos_token_id=bos, max_length=256,
                    num_beams=5, repetition_penalty=1.1,
                    no_repeat_ngram_size=3, early_stopping=True,
                    synced_gpus=False)
            return model.generate(**inputs, do_sample=False, max_new_tokens=64)

        # One discarded warm-up so the CUDA/cuDNN first call is not attributed
        # to any prompt or decoding profile.
        warm_inputs = tokenizer(NLLB_PROMPTS["short"], return_tensors="pt").to("cuda")
        result["nllb"]["warmup_call_s"], _ = timed_call(
            torch, lambda: generate(warm_inputs, "beam5_source"))

        for key, prompt in NLLB_PROMPTS.items():
            inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
            for profile in ("beam5_source", "greedy_control"):
                durations, tokens = [], []
                for _ in range(args.repeats):
                    elapsed, out = timed_call(
                        torch, lambda: generate(inputs, profile))
                    durations.append(elapsed)
                    tokens.append(int(out.shape[1]))
                stats = summarize(durations)
                result["nllb"]["cases"]["%s__%s" % (key, profile)] = {
                    "prompt_key": key,
                    "prompt_chars": len(prompt),
                    "prompt": prompt,
                    "decoding": profile,
                    "n": stats["n"],
                    "latency_mean_s": stats["mean"],
                    "latency_pstdev_s": stats["pstdev"],
                    "latency_cv": stats["cv"],
                    "durations_s": durations,
                    "output_tokens": tokens,
                    "tokens_per_s": [
                        (t / d) if d else None for t, d in zip(tokens, durations)],
                }
        del model
        gpu_sync(torch)
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
    except Exception as exc:
        result["nllb"]["error"] = "%s: %s" % (type(exc).__name__, exc)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_mode_argument(parser)
    parser.add_argument("--power-mode", choices=["15W", "MAXN"], default="MAXN",
                        help="label only; this script never changes clocks")
    parser.add_argument("--audio", help="10 s 16 kHz wav for the Whisper sweep")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--profiles", default="fp16_warm,fp32_no_warm",
                        help="Whisper profiles to run. Use only fp32_no_warm in a "
                             "fresh process to capture a true CUDA/cuDNN cold start; "
                             "running it after fp16_warm in the same process reuses "
                             "an already-initialised CUDA context.")
    parser.add_argument("--components", default="whisper,nllb",
                        help="comma-separated: whisper and/or nllb")
    parser.add_argument("--whisper-model",
                        default=os.environ.get(
                            "GEDGEBOT_WHISPER_MODEL",
                            "/root/src/gedge_bot/VoiceAsistant/.hf_cache/hub/"
                            "models--openai--whisper-small"))
    parser.add_argument("--nllb-model",
                        default=os.environ.get("GEDGEBOT_NLLB_MODEL",
                                              "/root/src/sample/models/nllb-200"))
    args = parser.parse_args()
    check_device_confirmation(parser, args)

    result = envelope(
        "gedgebot.sensitivity.v1", __file__, args.mode,
        profile="parameter_sensitivity",
        source_root=SOURCE_ROOT,
        source_commit=command_output(["git", "-C", SOURCE_ROOT, "rev-parse", "HEAD"]),
        condition={
            "power_mode_requested": args.power_mode,
            "power_mode_observed": (command_output(["nvpmodel", "-q"])
                                    if args.mode == "device" else None),
            "repeats_per_case": args.repeats,
            "whisper_profiles": [p.strip() for p in args.profiles.split(",") if p.strip()],
            "components": [c.strip() for c in args.components.split(",") if c.strip()],
            "whisper_model": args.whisper_model,
            "nllb_model": args.nllb_model,
            "audio": args.audio,
            "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        },
        targets={
            "whisper_10s_rtf_october_15w": HISTORICAL["bench_ai"]["whisper_10s_15w"]["rtf"],
            "nllb_short_s_single_model_15w":
                HISTORICAL["bench_ai"]["nllb_single_model_15w"]["short_s"],
            "nllb_short_s_power_table_15w":
                HISTORICAL["bench_ai"]["power_sensitivity"]["nllb_short_s"]["15W"],
            "note": "Historical values are transcribed, not measurements; they "
                    "are comparison targets only and are never pooled with new data.",
        },
        whisper={},
        nllb={"cases": {}},
        errors=[],
    )
    result["evidence_note"] = (
        "NEW 2026-11-07 device measurement varying inference parameters to "
        "explain discrepancies between the October transcription and the "
        "November rebuilt/source-aligned runs. It does NOT recover or reproduce "
        "the October experiment; the original audio, prompts and scripts remain "
        "unavailable. citation_allowed=false until manual review."
    )

    if args.mode == "device":
        try:
            import numpy as np
            import soundfile as sf
            import torch
        except Exception as exc:
            result["errors"].append("import failed: %s: %s" % (type(exc).__name__, exc))
            result["evidence_status"] = "device_measurement_failed"
            result["device_measurement"] = False
        else:
            audio = rate = None
            if args.audio and Path(args.audio).exists():
                audio, rate = sf.read(args.audio, dtype="float32")
                if getattr(audio, "ndim", 1) > 1:
                    audio = audio.mean(axis=1)
                result["condition"]["audio_duration_s"] = len(audio) / float(rate)
            else:
                result["errors"].append("audio not supplied/found; Whisper sweep skipped")
            components = [c.strip() for c in args.components.split(",") if c.strip()]
            if audio is not None and "whisper" in components:
                run_whisper(args, audio, rate, result, torch, np)
            if "nllb" in components:
                run_nllb(args, result, torch)
            ok_cases = len(result["nllb"].get("cases", {}))
            ok_clips = sum(len(v.get("clips", {}))
                           for v in result["whisper"].values() if isinstance(v, dict))
            result["measurement_summary"] = {
                "whisper_profiles": list(result["whisper"].keys()),
                "whisper_clip_cases": ok_clips,
                "nllb_cases": ok_cases,
            }
            if ok_cases == 0 and ok_clips == 0:
                result["evidence_status"] = "device_measurement_failed"
                result["device_measurement"] = False
            else:
                result["evidence_status"] = "device_measurement_partial"
                result["device_measurement"] = True
                result["citation_allowed"] = False

    out = write_result(args.output, result)
    print("mode=%s profile=parameter_sensitivity evidence_status=%s output=%s" %
          (args.mode, result["evidence_status"], out))


if __name__ == "__main__":
    main()
