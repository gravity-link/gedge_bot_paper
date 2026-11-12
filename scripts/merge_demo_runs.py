#!/usr/bin/env python3
"""Reconstructed ``merge_demo_runs.py``: merge N demonstration run records.

Reads the JSON files produced by demo_capture.py, derives the aggregate
statistics used in Extended Abstract v1.0 Sec. 7 (success rate, latency mean /
spread / p50 / min / max / cv, DOA angles, follow segments) and writes
``demo_merged_*.json``.

Important: run records whose latency is null (local probe output) are counted
as ``excluded_no_measurement`` and contribute NOTHING to the statistics. The
historical October values can be attached as an explicitly labelled
``historical_reference`` block with --include-historical-reference; that block
is a transcription, never a computed result of this script.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import HISTORICAL, envelope, summarize, write_result  # noqa: E402


def collect(patterns):
    paths = []
    for pattern in patterns:
        paths.extend(sorted(glob.glob(pattern)))
    return sorted(set(paths))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inputs", nargs="+", required=True,
                        help="glob patterns for demo_runs_*.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-historical-reference", action="store_true",
                        help="attach transcribed October values as a labelled reference")
    args = parser.parse_args()

    paths = collect(args.inputs)
    records = []
    for path in paths:
        try:
            records.append((path, json.loads(Path(path).read_text())))
        except Exception as exc:
            records.append((path, {"_read_error": "%s: %s" % (type(exc).__name__, exc)}))

    usable = []
    excluded = []
    for path, doc in records:
        run = doc.get("run") or {}
        latency = run.get("trigger_to_follow_latency_s")
        if isinstance(latency, (int, float)):
            usable.append((path, run, doc))
        else:
            excluded.append({"path": str(path),
                             "reason": "no trigger-to-follow measurement",
                             "evidence_status": doc.get("evidence_status")})

    latencies = [run["trigger_to_follow_latency_s"] for _, run, _ in usable]
    stats = summarize(latencies)
    successes = [run.get("success") for _, run, _ in usable]
    angles = [run.get("doa_turn_angle_deg") for _, run, _ in usable]
    segments = [run.get("follow_segment") for _, run, _ in usable]
    voice_flags = [run.get("voice_assistant_ready") for _, run, _ in usable]
    windows = [run.get("window_id") for _, run, _ in usable]

    result = envelope("gedgebot.demo_merged.rebuilt.v1", __file__, "probe",
                      inputs=[str(p) for p in paths],
                      files_found=len(paths),
                      runs_with_measurement=len(usable),
                      excluded_no_measurement=excluded,
                      aggregate={
                          "status": "computed" if usable else "no_valid_runs",
                          "n": stats["n"],
                          "success_count": sum(1 for s in successes if s is True),
                          "success_rate": (sum(1 for s in successes if s is True) / len(successes)
                                           if successes else None),
                          "trigger_to_follow": {
                              "mean_s": stats["mean"],
                              "pstdev_s": stats["pstdev"],
                              "cv": stats["cv"],
                              "p50_s": stats["p50"],
                              "min_s": stats["min"],
                              "max_s": stats["max"],
                              "p99_s": stats["p99"],
                          },
                          "doa_turn_angles_deg": [a for a in angles if a is not None],
                          "follow_segments": [s for s in segments if s],
                          "capture_windows": sorted({w for w in windows if w}),
                          "voice_assistant_ready_count":
                              sum(1 for v in voice_flags if v is True),
                          "conversation_verified": False,
                      },
                      errors=[r["_read_error"] for _, r in records if "_read_error" in r])

    if args.include_historical_reference:
        result["historical_reference"] = {
            "status": "transcribed_historical_value_not_recovered_measurement",
            "source": HISTORICAL["source"],
            "demo": HISTORICAL["demo"],
            "warning": "This block is copied from Extended Abstract v1.0. It is "
                       "NOT computed from the input run files and must not be "
                       "presented as a new measurement.",
        }
    if not usable:
        result["evidence_note"] = (
            "No input run contained a latency measurement. Probe-mode records "
            "are structural placeholders only; this merged file has no "
            "experimental content.")

    out = write_result(args.output, result)
    print("files=%d usable_runs=%d status=%s output=%s" %
          (len(paths), len(usable), result["aggregate"]["status"], out))


if __name__ == "__main__":
    main()
