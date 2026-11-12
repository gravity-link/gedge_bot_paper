#!/usr/bin/env python3
"""Reconstructed ``demo_capture.py``: one end-to-end demonstration run record.

Historical October setup (Extended Abstract v1.0 Sec. 7): auto-wakeup mode,
mic trigger -> DOA-guided turn -> assistant start -> face following -> return
to listening. Six runs in five capture windows, merged into
``demo_merged_20261017_131742.json``. The voice assistant did NOT warm up in
those runs (voice=False), so the recorded success is the trigger-to-follow
chain, not a verified spoken dialogue.

Modes:
  probe  - emit a fully structured empty record (default, safe). No numbers
           are invented; every measured field is null.
  manual - import an operator's written record from --observations. The
           operator supplies timestamps/angles; the script only derives
           trigger-to-follow latency from them.
  device - intentionally refuses automatic capture. Driving the product loop
           needs on-site confirmation of microphones, cameras and motion;
           use --mode manual with a written observation record instead.

Usage example (operator record):
  {"run_id": 1, "window_id": "w1", "trigger_unix": 179...,
   "follow_start_unix": 179..., "doa_turn_angle_deg": 90,
   "follow_duration_s": 120, "follow_ended_by": "segment_end",
   "voice_assistant_ready": false, "yolo_fps": 17.7, "inference_ms": 37,
   "success": true, "notes": "..."}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import (  # noqa: E402
    HISTORICAL, add_mode_argument, check_device_confirmation, envelope,
    write_result,
)


def empty_run():
    return {
        "run_id": None,
        "window_id": None,
        "trigger_unix": None,
        "follow_start_unix": None,
        "trigger_to_follow_latency_s": None,
        "doa_turn_angle_deg": None,
        "follow_segment": {"duration_s": None, "ended_by": None},
        "voice_assistant_ready": None,
        "perception": {"yolo_fps": None, "inference_ms": None},
        "success": None,
        "notes": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_mode_argument(parser, modes=("probe", "device", "manual"))
    parser.add_argument("--run-id", type=int, default=1)
    parser.add_argument("--window-id", default="w1")
    parser.add_argument("--observations", help="operator JSON record for --mode manual")
    args = parser.parse_args()
    check_device_confirmation(parser, args)
    if args.mode == "manual" and not args.observations:
        parser.error("--mode manual requires --observations PATH")

    run = empty_run()
    run["run_id"] = args.run_id
    run["window_id"] = args.window_id

    result = envelope("gedgebot.demo_run.rebuilt.v1", __file__, args.mode,
                      run=run,
                      capture_declaration={
                          "microphone_opened": False,
                          "camera_opened": False,
                          "motor_device_opened": False,
                          "robot_motion_observed": None,
                          "statement": "Automatic capture of the physical "
                                       "product loop is deliberately not "
                                       "implemented in the rebuilt script.",
                      },
                      historical_demo=HISTORICAL["demo"],
                      errors=[])

    if args.mode == "manual":
        obs = json.loads(Path(args.observations).read_text())
        run.update({
            "run_id": obs.get("run_id", args.run_id),
            "window_id": obs.get("window_id", args.window_id),
            "trigger_unix": obs.get("trigger_unix"),
            "follow_start_unix": obs.get("follow_start_unix"),
            "doa_turn_angle_deg": obs.get("doa_turn_angle_deg"),
            "follow_segment": {"duration_s": obs.get("follow_duration_s"),
                               "ended_by": obs.get("follow_ended_by")},
            "voice_assistant_ready": obs.get("voice_assistant_ready"),
            "perception": {"yolo_fps": obs.get("yolo_fps"),
                           "inference_ms": obs.get("inference_ms")},
            "success": obs.get("success"),
            "notes": obs.get("notes"),
        })
        trigger, follow = obs.get("trigger_unix"), obs.get("follow_start_unix")
        if isinstance(trigger, (int, float)) and isinstance(follow, (int, float)):
            run["trigger_to_follow_latency_s"] = float(follow) - float(trigger)
        elif "trigger_to_follow_latency_s" in obs:
            run["trigger_to_follow_latency_s"] = obs["trigger_to_follow_latency_s"]
        result["observation_source"] = str(Path(args.observations).resolve())
        result["evidence_note"] = ("Run values come from a written operator "
                                   "record. Verify the original notes before citing.")
    elif args.mode == "device":
        result["errors"].append(
            "automatic physical capture is intentionally disabled; record the "
            "run manually and import it with --mode manual --observations")
        result["evidence_status"] = "device_measurement_refused"
        result["device_measurement"] = False

    out = write_result(args.output, result)
    print("mode=%s evidence_status=%s latency=%s output=%s" %
          (args.mode, result["evidence_status"],
           result["run"]["trigger_to_follow_latency_s"], out))


if __name__ == "__main__":
    main()
