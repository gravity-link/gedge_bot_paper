#!/usr/bin/env python3
"""Measure ChatTTS long-text RTF on the Xavier.

The October 2026 ChatTTS RTF of 2.51 could not be reproduced by the rebuilt
``bench_ai.py`` because the package was missing from the isolated CUDA env.
On the device, ChatTTS plus its weights exist in the VoiceAsistant venv, and
``VoiceAsistant/chattts_server.py`` contains a compatibility patch for the
Jetson's torch 2.1.0a0 build (no ``weight_norm`` in parametrizations). This
script reuses that patch and measures the ratio

    RTF = synthesis wall time / duration of the synthesized waveform

so the denominator is observed, never assumed. It never invents a value: any
import/load/inference failure is recorded and the result stays unmeasured.

Modes: probe (no model load) / device (real synthesis, explicit confirmation).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# torchaudio compiled against the Jetson torch build lives outside site-packages
sys.path.insert(0, "/root/gedge_bot_validation_20261107/py38_site/torchaudio_build")
sys.path.insert(0, "/root/gedge_bot_validation_20261107/py38_site")
from rebuild_common import (  # noqa: E402
    HISTORICAL, add_mode_argument, check_device_confirmation, envelope,
    write_result,
)

SNAPSHOT_ROOT = Path(
    "/root/src/gedge_bot/VoiceAsistant/.hf_cache/hub/"
    "models--2Noise--ChatTTS/snapshots")
SAMPLE_RATE = 24000
TEXT = (
    "算力再生把退役的边缘人工智能芯片重新组织成一个新产品："
    "把非确定性的智能负载留在旧的加速器上，"
    "把硬实时的运动控制迁移到确定性的微控制器上。"
    "机器人现在转向说话人，并开始跟随。"
)


def patch_jetson_torch():
    """Compatibility shim copied from VoiceAsistant/chattts_server.py."""
    import types
    import torch
    import torch.nn as nn

    if not hasattr(torch, "distributed") or torch.distributed is None:
        torch.distributed = types.SimpleNamespace()
    if not hasattr(torch.distributed, "ReduceOp"):
        torch.distributed.ReduceOp = types.SimpleNamespace(SUM="sum")
    if not hasattr(torch.distributed, "is_available"):
        torch.distributed.is_available = lambda: False
    if not hasattr(torch.distributed, "is_initialized"):
        torch.distributed.is_initialized = lambda: False
    if not hasattr(torch.distributed, "get_world_size"):
        torch.distributed.get_world_size = lambda: 1
    if not hasattr(torch.distributed, "all_reduce"):
        torch.distributed.all_reduce = lambda tensor, op=None: tensor

    class WeightNormCompat(nn.Module):
        def __init__(self, dim=0):
            super().__init__()
            self.dim = dim

        def forward(self, weight_g, weight_v):
            return torch._weight_norm(weight_v, weight_g, self.dim)

        def right_inverse(self, weight):
            return torch.norm_except_dim(weight, 2, self.dim), weight

    def weight_norm(module, name="weight", dim=0):
        from torch.nn.utils.parametrize import register_parametrization
        register_parametrization(module, name, WeightNormCompat(dim), unsafe=True)
        return module

    import torch.nn.utils.parametrizations as parametrizations
    parametrizations.weight_norm = weight_norm


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_mode_argument(parser)
    parser.add_argument("--power-mode", default="MAXN")
    args = parser.parse_args()
    check_device_confirmation(parser, args)

    result = envelope(
        "gedgebot.chattts_bench.v1", __file__, args.mode,
        condition={
            "power_mode_requested": args.power_mode,
            "sample_rate_hz": SAMPLE_RATE,
            "text_chars": len(TEXT),
            "rtf_definition": "synthesis_wall_time_s / synthesized_audio_duration_s",
            "snapshot_root": str(SNAPSHOT_ROOT),
            "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        },
        measurement={"status": "not_measured", "value": None, "reason": None},
        historical_chattts=HISTORICAL["bench_ai"]["chattts_long"],
        errors=[],
    )

    if args.mode == "device":
        try:
            patch_jetson_torch()
            import numpy as np
            import torch
            import ChatTTS
            if not SNAPSHOT_ROOT.is_dir():
                raise RuntimeError("ChatTTS snapshot root not found: %s" % SNAPSHOT_ROOT)
            snapshots = [p for p in SNAPSHOT_ROOT.iterdir() if p.is_dir()]
            if not snapshots:
                raise RuntimeError("no ChatTTS snapshot under %s" % SNAPSHOT_ROOT)
            snapshot = max(snapshots, key=lambda p: p.stat().st_mtime)

            chat = ChatTTS.Chat()
            t0 = time.perf_counter()
            loaded = chat.load(source="custom", custom_path=str(snapshot), compile=False)
            load_s = time.perf_counter() - t0
            if not loaded:
                raise RuntimeError("ChatTTS.load returned False")

            torch.cuda.synchronize() if torch.cuda.is_available() else None
            t0 = time.perf_counter()
            wavs = chat.infer([TEXT], skip_refine_text=True)
            torch.cuda.synchronize() if torch.cuda.is_available() else None
            synth_s = time.perf_counter() - t0

            audio = np.asarray(wavs[0]).reshape(-1)
            audio_duration_s = len(audio) / float(SAMPLE_RATE)
            if audio_duration_s <= 0:
                raise RuntimeError("synthesized waveform is empty")
            rtf = synth_s / audio_duration_s
            result["measurement"] = {
                "status": "ok",
                "value": {
                    "text_chars": len(TEXT),
                    "snapshot": str(snapshot),
                    "model_load_s": load_s,
                    "synthesis_s": synth_s,
                    "audio_samples": int(len(audio)),
                    "audio_duration_s": audio_duration_s,
                    "rtf": rtf,
                    "realtime": rtf < 1.0,
                    "torch": torch.__version__,
                    "cuda": torch.version.cuda,
                    "device": (torch.cuda.get_device_name(0)
                               if torch.cuda.is_available() else None),
                },
                "reason": None,
            }
            result["evidence_status"] = "device_measurement_partial"
            result["device_measurement"] = True
            result["citation_allowed"] = False
            result["evidence_note"] = (
                "New ChatTTS synthesis measured on the Xavier with the device's "
                "own compatibility patch. Audio duration was observed from the "
                "generated waveform; this is NOT the recovered October run.")
        except Exception as exc:
            result["measurement"]["status"] = "unavailable"
            result["measurement"]["reason"] = "%s: %s" % (type(exc).__name__, exc)
            result["errors"].append("ChatTTS measurement failed: %s: %s" %
                                    (type(exc).__name__, exc))
            result["evidence_status"] = "device_measurement_failed"
            result["device_measurement"] = False

    out = write_result(args.output, result)
    print("mode=%s status=%s output=%s" %
          (args.mode, result["measurement"]["status"], out))


if __name__ == "__main__":
    main()
