#!/usr/bin/env python3
"""Reconstructed ``failsafe_test.py``: fault-injection entry point.

Historical October cases (Extended Abstract v1.0 Sec. 6, F1-F3):

  F1  stop sending frames mid-drive        -> wheels stop after final pulse
  F2  close serial port mid-drive          -> wheels stop immediately
  F3  garbage bytes / opcode 0x55 / bad tail -> all discarded, no spurious motion

SAFETY BOUNDARY (deliberate, see paper/README.md):
  * F1 and F2 require physically driving the robot. This script NEVER opens
    /dev/ttyUSB0, NEVER sends a motor frame, NEVER kills a process and NEVER
    asks anyone to pull a cable. In every automatic mode F1/F2 stay
    ``requires_onsite_confirmation`` with a null outcome. Their results must be
    imported with ``--mode manual --observations`` from an operator's record.
  * F3 is reproducible offline: the script exercises the captured runtime's
    microphone parser and a seven-byte motor guard against malformed inputs.
    That is a protocol/logic check, NOT a physical fault injection.

Modes:
  probe  - run offline F3 checks only (default, safe on any host)
  device - same offline checks plus device capability probe; never drives motors
  manual - import operator-recorded F1/F2/F3 outcomes
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import (  # noqa: E402
    HISTORICAL, add_mode_argument, check_device_confirmation, envelope,
    sha256_file, write_result,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = ROOT / "evidence" / "robot_runtime.current.py"
EXPECTED_RUNTIME_SHA256 = "d80196b650217cf0e76578855538ab496c7274be4c74766b5b24dc18dc89b9f5"


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("robot_runtime_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mic_frame(angle, command=0x82, subcommand=0x02, corrupt=False):
    frame = bytearray([0xFF, 0xA5, 0x01, 0x00, 0x05, command, subcommand,
                       angle & 0xFF, (angle >> 8) & 0xFF, 0x00])
    frame[-1] = sum(frame[:-1]) & 0xFF
    if corrupt:
        frame[-1] ^= 0x01
    return bytes(frame)


def make_guard(runtime):
    """Seven-byte motor-frame guard built from the captured protocol constants."""
    valid_cmds = {value for name, value in vars(runtime).items()
                  if name.startswith("CMD_") and isinstance(value, int)}

    def validate(frame):
        if not isinstance(frame, (bytes, bytearray)) or len(frame) != 7:
            return False, "length"
        if frame[0:2] != b"\xaa\x55":
            return False, "header"
        if frame[-2:] != b"\x0d\x0a":
            return False, "tail"
        if frame[2] not in valid_cmds:
            return False, "undefined_opcode"
        return True, "accepted"

    return validate, sorted(valid_cmds)


def run_offline_f3(runtime_path):
    runtime = load_runtime(runtime_path)
    validate, valid_cmds = make_guard(runtime)
    reader = runtime.MicArrayReader.__new__(runtime.MicArrayReader)
    reader.verbose = False
    reader._latest_angle = None

    cases = []

    def record(name, expected, actual, detail):
        cases.append({"case": name, "expected": expected, "observed": actual,
                      "passed": expected == actual, "detail": detail})

    # Motor-frame guard, rebuilt from the captured seven-byte protocol.
    good = runtime.build_motor_command(runtime.CMD_TURN_LEFT, 5)
    record("valid_motor_frame", "accepted", validate(good)[1], good.hex(" ").upper())
    record("garbage_bytes", "length", validate(b"\x00\x01\x02")[1], "3 random bytes")
    undefined = runtime.build_motor_command(0x55, 5)
    record("undefined_opcode_0x55", "undefined_opcode", validate(undefined)[1],
           undefined.hex(" ").upper())
    bad_tail = bytearray(good)
    bad_tail[-2:] = b"\x00\x00"
    record("bad_frame_tail", "tail", validate(bytes(bad_tail))[1],
           bytes(bad_tail).hex(" ").upper())
    bad_header = bytearray(good)
    bad_header[0:2] = b"\x00\x00"
    record("bad_frame_header", "header", validate(bytes(bad_header))[1],
           bytes(bad_header).hex(" ").upper())

    # Captured microphone-array parser.
    record("mic_valid_doa_frame", "angle_event",
           "angle_event" if runtime.MicArrayReader._parse_packet(
               reader, mic_frame(90)) is not None else "rejected",
           mic_frame(90).hex(" ").upper())
    record("mic_bad_checksum", "rejected",
           "rejected" if runtime.MicArrayReader._parse_packet(
               reader, mic_frame(90, corrupt=True)) is None else "angle_event",
           mic_frame(90, corrupt=True).hex(" ").upper())
    record("mic_non_doa_frame", "rejected",
           "rejected" if runtime.MicArrayReader._parse_packet(
               reader, mic_frame(90, command=0x81)) is None else "angle_event",
           "intensity/status frame 0x81")
    record("mic_out_of_range_angle", "rejected",
           "rejected" if runtime.MicArrayReader._parse_packet(
               reader, mic_frame(360)) is None else "angle_event",
           "angle=360 outside 0..359")

    return {
        "status": "offline_protocol_guard_check",
        "protocol": "captured seven-byte motor frame + captured mic-array parser",
        "valid_motor_opcodes": valid_cmds,
        "runtime_path": str(runtime_path.resolve()),
        "runtime_sha256": sha256_file(runtime_path),
        "runtime_hash_matches_captured_snapshot":
            sha256_file(runtime_path) == EXPECTED_RUNTIME_SHA256,
        "cases": cases,
        "cases_passed": sum(1 for c in cases if c["passed"]),
        "cases_total": len(cases),
        "physical_fault_injection_performed": False,
        "spurious_motion_observed": None,
        "note": "Passing these checks means malformed inputs are rejected by "
                "the captured parsers. It does NOT demonstrate that a physical "
                "robot produced no spurious motion.",
    }


def empty_physical_cases():
    return {
        name: {
            "status": "requires_onsite_confirmation",
            "outcome": None,
            "physical_injection_performed": False,
            "required_operator_actions": actions,
        }
        for name, actions in [
            ("F1_control_crash", [
                "Confirm the chassis is raised or in a clear area.",
                "Start a finite-pulse drive sequence under operator control.",
                "Stop frame transmission mid-drive and observe wheel stop.",
                "Record the observation in the --observations JSON.",
            ]),
            ("F2_cable_pull", [
                "Confirm the chassis is raised or in a clear area.",
                "Close the serial port / disconnect the cable mid-drive.",
                "Observe wheel stop and record it in the --observations JSON.",
            ]),
        ]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_mode_argument(parser, modes=("probe", "device", "manual"))
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--observations",
                        help="operator JSON record for --mode manual, with keys "
                             "F1_control_crash / F2_cable_pull / F3_protocol_errors")
    args = parser.parse_args()
    check_device_confirmation(parser, args)
    if args.mode == "manual" and not args.observations:
        parser.error("--mode manual requires --observations PATH")

    result = envelope("gedgebot.failsafe.rebuilt.v1", __file__, args.mode,
                      physical_cases=empty_physical_cases(),
                      protocol_checks={"status": "not_run", "cases": []},
                      historical_failsafe=HISTORICAL["failsafe"],
                      safety_declaration={
                          "serial_device_opened": False,
                          "motor_frames_sent_to_hardware": 0,
                          "cable_or_process_intervention": False,
                          "statement": "This script never actuates the robot in "
                                       "any automatic mode.",
                      },
                      errors=[])

    runtime_path = Path(args.runtime)
    if runtime_path.exists():
        try:
            result["protocol_checks"] = run_offline_f3(runtime_path)
            result["physical_cases"]["F3_protocol_errors"] = {
                "status": "offline_protocol_guard_check",
                "outcome": "malformed inputs rejected by captured parsers",
                "physical_injection_performed": False,
                "cases_passed": result["protocol_checks"]["cases_passed"],
                "cases_total": result["protocol_checks"]["cases_total"],
            }
        except Exception as exc:
            result["errors"].append("offline F3 checks failed: %s: %s" %
                                    (type(exc).__name__, exc))
    else:
        result["errors"].append("runtime snapshot not found: %s" % runtime_path)

    if args.mode == "manual":
        observations = json.loads(Path(args.observations).read_text())
        for key, value in observations.items():
            if key.startswith("F"):
                result["physical_cases"][key] = {
                    "status": "human_observation",
                    "outcome": value,
                    "physical_injection_performed_by": "operator",
                    "observation_source": str(Path(args.observations).resolve()),
                }
        result["evidence_note"] = ("F1/F2 outcomes are written operator "
                                   "observations, not automatically captured data.")
        result["evidence_status"] = "human_observation_partial"

    out = write_result(args.output, result)
    print("mode=%s evidence_status=%s protocol=%s/%s output=%s" %
          (args.mode, result["evidence_status"],
           result["protocol_checks"].get("cases_passed", 0),
           result["protocol_checks"].get("cases_total", 0), out))


if __name__ == "__main__":
    main()
