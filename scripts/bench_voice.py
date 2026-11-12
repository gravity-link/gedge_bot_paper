#!/usr/bin/env python3
"""Reconstructed ``bench_voice.py``: voice / translation pipeline timing.

Measures the chain ASR (Whisper-small) -> MT (NLLB-200) -> TTS (ChatTTS),
per-stage and end to end, plus the offline/online status of each stage.

Historical context (Extended Abstract v1.0 Sec. 5/7): the October numbers
were Whisper 10 s RTF 0.091, NLLB short/mid/long 0.42/1.02/1.55 s, ChatTTS
long-text RTF 2.51. During the N=6 demonstrations the voice assistant failed
to warm up within 60 s (voice=False), so no conversational latency was
measured there. This rebuild keeps that distinction explicit.

Modes:
  probe  - environment probe only; all measured values null (default, safe)
  device - real pipeline on the Xavier; requires --confirm-device-measurement,
           --audio and --text; missing dependencies are recorded, never faked
  manual - import an operator's written stage timings from --observations
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import (  # noqa: E402
    HISTORICAL, add_mode_argument, check_device_confirmation, envelope,
    write_result,
)

MODEL_PATHS = {
    "whisper": os.environ.get(
        "GEDGEBOT_WHISPER_MODEL",
        "/root/src/gedge_bot/VoiceAsistant/.hf_cache/hub/models--openai--whisper-small"),
    "nllb": os.environ.get("GEDGEBOT_NLLB_MODEL", "/root/src/sample/models/nllb-200"),
}

STAGES = ["asr", "mt", "tts"]


def empty_stages():
    return {name: {"status": "not_measured", "value": None,
                   "reason": "local probe; no model was loaded"}
            for name in STAGES}


def stage(result, name, value, reason=None):
    result["stages"][name] = {
        "status": "ok" if value is not None else "unavailable",
        "value": value, "reason": reason}


def run_device(args, result):
    if not args.audio or not Path(args.audio).exists():
        result["errors"].append("--audio wav file is required for device mode")
    if not args.text:
        result["errors"].append("--text is required for device mode")
    if result["errors"]:
        return

    try:
        import soundfile as sf
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline
    except Exception as exc:
        result["errors"].append("import failed: %s: %s" % (type(exc).__name__, exc))
        return

    start_total = time.perf_counter()
    # --- ASR -------------------------------------------------------------
    try:
        audio, rate = sf.read(args.audio, dtype="float32")
        duration_s = len(audio) / float(rate)
        asr = pipeline("automatic-speech-recognition", model=MODEL_PATHS["whisper"],
                       device=0 if torch.cuda.is_available() else -1)
        t0 = time.perf_counter()
        output = asr({"raw": audio, "sampling_rate": rate})
        asr_s = time.perf_counter() - t0
        transcript = (output.get("text") or "").strip() if isinstance(output, dict) else str(output)
        stage(result, "asr", {
            "audio_path": str(args.audio), "audio_duration_s": duration_s,
            "processing_s": asr_s, "rtf": asr_s / duration_s if duration_s else None,
            "transcript_chars": len(transcript),
            "transcript": None,
            "note": "transcript text deliberately not persisted in evidence JSON",
        })
    except Exception as exc:
        stage(result, "asr", None, "ASR failed: %s: %s" % (type(exc).__name__, exc))
        transcript = args.text

    # --- MT --------------------------------------------------------------
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATHS["nllb"], local_files_only=True)
        mt = AutoModelForSeq2SeqLM.from_pretrained(
            MODEL_PATHS["nllb"], torch_dtype=torch.float16,
            local_files_only=True).to("cuda")
        source = transcript or args.text
        inputs = tokenizer(source, return_tensors="pt").to("cuda")
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        generated = mt.generate(**inputs, max_new_tokens=64, do_sample=False)
        torch.cuda.synchronize()
        mt_s = time.perf_counter() - t0
        stage(result, "mt", {
            "source_chars": len(source), "latency_s": mt_s,
            "output_tokens": int(generated.shape[1]),
            "decoding": "greedy", "dtype": "fp16",
        })
    except Exception as exc:
        stage(result, "mt", None, "MT failed: %s: %s" % (type(exc).__name__, exc))

    # --- TTS -------------------------------------------------------------
    try:
        import ChatTTS
    except Exception as exc:
        stage(result, "tts", None,
              "ChatTTS unavailable: %s: %s" % (type(exc).__name__, exc))
    else:
        stage(result, "tts", None,
              "ChatTTS RTF needs a synthesized-audio duration; the operator "
              "must supply the reference text/audio to avoid inventing a denominator")

    total_s = time.perf_counter() - start_total
    ok = [name for name in STAGES if result["stages"][name]["status"] == "ok"]
    stages_complete = len(ok) == len(STAGES)
    result["end_to_end"] = {
        "status": "ok" if stages_complete else "incomplete",
        "wall_clock_s": total_s if stages_complete else None,
        "stages_complete": ok,
        "conversation_verified": False,
        "note": "A complete stage chain is not proof of a spoken dialogue; "
                "the October demonstrations ran with voice=False.",
    }
    result["voice_assistant_ready"] = None
    result["offline_status"] = {
        "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        "local_files_only": True,
        "cloud_calls_observed": None,
        "note": "Offline status is asserted from configuration, not from "
                "packet capture. It must not be reported as 'fully offline' "
                "without checking the actual assistant configuration.",
    }


def run_manual(args, result):
    observations = json.loads(Path(args.observations).read_text())
    for name in STAGES:
        value = observations.get(name)
        stage(result, name, value,
              None if value is not None else "not supplied by operator")
    result["end_to_end"] = {
        "status": "human_observation",
        "wall_clock_s": observations.get("end_to_end_s"),
        "stages_complete": [n for n in STAGES if observations.get(n)],
        "conversation_verified": observations.get("conversation_verified"),
        "note": "Timings were written by the operator, not captured automatically.",
    }
    result["voice_assistant_ready"] = observations.get("voice_assistant_ready")
    result["offline_status"] = {"note": observations.get("offline_note")}
    result["observation_source"] = str(Path(args.observations).resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_mode_argument(parser, modes=("probe", "device", "manual"))
    parser.add_argument("--audio", help="wav file for device-mode ASR stage")
    parser.add_argument("--text", help="fallback source text for device-mode MT stage")
    parser.add_argument("--observations", help="operator JSON record for manual mode")
    args = parser.parse_args()
    check_device_confirmation(parser, args)
    if args.mode == "manual" and not args.observations:
        parser.error("--mode manual requires --observations PATH")

    result = envelope("gedgebot.bench_voice.rebuilt.v1", __file__, args.mode,
                      condition={"model_paths": MODEL_PATHS,
                                 "audio": args.audio,
                                 "text_chars": len(args.text or "")},
                      stages=empty_stages(),
                      end_to_end={"status": "not_measured", "wall_clock_s": None,
                                  "stages_complete": [],
                                  "conversation_verified": None},
                      voice_assistant_ready=None,
                      offline_status={"hf_hub_offline": os.environ.get("HF_HUB_OFFLINE")},
                      historical_bench_voice=HISTORICAL["bench_voice"],
                      errors=[])

    if args.mode == "device":
        result["measurement_attempted"] = True
        run_device(args, result)
        ok = sum(1 for s in result["stages"].values() if s["status"] == "ok")
        if ok:
            result["evidence_status"] = "device_measurement_partial"
            result["device_measurement"] = True
            result["citation_allowed"] = False
            result["evidence_note"] = ("New device measurement produced by the "
                                       "reconstructed script, not a recovered "
                                       "October run. Review before citing.")
        else:
            result["evidence_status"] = "device_measurement_failed"
            result["errors"].append("no voice stage could be measured")
    elif args.mode == "manual":
        result["measurement_attempted"] = False
        result["evidence_note"] = ("Values imported from a written operator "
                                   "record. Verify the original notes before citing.")
    else:
        result["measurement_attempted"] = False

    out = write_result(args.output, result)
    print("mode=%s evidence_status=%s stages_ok=%d output=%s" %
          (args.mode, result["evidence_status"],
           sum(1 for s in result["stages"].values() if s["status"] == "ok"), out))


if __name__ == "__main__":
    main()
