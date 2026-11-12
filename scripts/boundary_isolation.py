#!/usr/bin/env python3
"""Trace-driven analysis of the finite-pulse execution boundary (Q2).

WHAT IS REAL MEASUREMENT
    The host-side frame arrival traces come from 30 A/B runs recorded on the
    Jetson AGX Xavier on 2026-11-07: 6000 production-format seven-byte frames
    at a 50 ms absolute schedule, with per-frame monotonic timestamps. All 30
    runs report an exact received frame sequence and no physical motor writes.

WHAT IS DERIVED, NOT MEASURED
    Two execution organisations are driven by the *same* measured trace and
    compared at the contract level:

      direct_linux_pulse_generation
          Pulse cadence is produced in Linux user space, so the host frame
          deviation transfers 1:1 to the pulse train. This is a counterfactual
          model of the historical direct-control path, not a rerun of it.

      finite_pulse_mcu_offload
          The host emits only a seven-byte semantic frame carrying a pulse
          count N. The MCU generates N pulses on its own local clock and then
          calls StopAllMotors(). Host jitter shifts the *start phase* of the
          whole motion but does not change the cadence inside the pulse train.

    Execution-side numbers below are contract-derived bounds from the audited
    firmware snapshot, NOT logic-analyser observations. The script also audits
    whether the firmware itself enforces any upper bound on N.

This is deliberately an analysis artifact, not a device measurement. It never
opens a serial port, never writes a motor frame, and never changes power mode.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import percentile_nearest, sha256_file, utc_now_iso  # noqa: E402

FIRMWARE = ROOT / "evidence" / "motor_uart_vision.current.c"
RUNTIME = ROOT / "evidence" / "robot_runtime.current.py"
AB_GLOB = ROOT / "evidence" / "device" / "ab_control_*.json"
DEFAULT_JSON = ROOT / "evidence" / "analysis" / "boundary_isolation_20261107.json"
DEFAULT_MD = ROOT / "evidence" / "BOUNDARY_ISOLATION.md"

# Constants read from evidence/motor_uart_vision.current.c (line numbers in the
# captured snapshot). Their presence is re-verified at run time.
SPEED_NORMAL_MS = 35       # L38  #define SPEED_NORMAL  35
PULSE_GAP_MS = 8           # L212 delay_ms(8)
TURN_PULSE_PERIOD_MS = SPEED_NORMAL_MS + PULSE_GAP_MS   # 43 ms
MPAN_PULSE_COUNT = 15      # L178 fixed counter-head sequence
TRAILING_SETTLE_MS = 1000  # L218 delay_ms(1000) after StopAllMotors
RUNTIME_BODY_PULSE_MAX = 25   # main.tex L133, robot_runtime default
RUNTIME_HEAD_PULSE_MIN = 5    # robot_runtime.py pan_head clamp lower bound
FRAME_FIELD_MAX = 0xFFFF      # 16-bit pulse field, build_motor_command L507

CONDITIONS = ("B_idle", "A_ai_loaded", "C_cpu_load")


def verify_contract():
    """Re-check that the audited snapshots still contain the assumed contract."""
    fw = FIRMWARE.read_text()
    rt = RUNTIME.read_text()
    checks = {
        "speed_normal_35": bool(re.search(r"#define\s+SPEED_NORMAL\s+35\b", fw)),
        "pulse_gap_8": "delay_ms(8);" in fw,
        "mpan_fixed_15": bool(re.search(r"#define\s+MPAN_PULSE_COUNT\s+15\b", fw)),
        "turnbody_loop_bounded_by_pulse_count":
            bool(re.search(r"for\s*\(uint16_t i = 0; i < pulse_count; i\+\+\)", fw)),
        "stop_all_after_loop": "StopAllMotors();" in fw,
        "trailing_settle_1000": "delay_ms(1000);" in fw,
        "pulse_field_is_16bit":
            "uint16_t pulse = rx_buf[3] | (rx_buf[4] << 8);" in fw,
        "firmware_does_not_clamp_pulse": not bool(
            re.search(r"pulse\s*=\s*(?:clamp|min|MIN|limit)", fw)),
        "host_clamps_body_pulse": bool(re.search(r"clamp\(int\(pulse\), 5,", rt)),
        "host_frame_clamps_to_0xffff": bool(re.search(r"clamp\(int\(pulse_count\), 0, 0xFFFF\)", rt)),
        "no_seq_or_crc_in_frame": ("0xAA, 0x55, cmd, pulse & 0xFF" in rt
                                   or "bytes([0xAA, 0x55" in rt),
    }
    return checks, {
        "firmware_sha256": sha256_file(FIRMWARE),
        "runtime_sha256": sha256_file(RUNTIME),
    }


def load_runs():
    runs = []
    for path in sorted(glob.glob(str(AB_GLOB))):
        data = json.loads(Path(path).read_text())
        summary = data.get("summary", {})
        deviations = summary.get("signed_deviations_ms")
        if not deviations:
            continue
        condition = data.get("condition", "")
        mode = "MAXN" if "MAXN" in condition else ("15W" if "15W" in condition else "unknown")
        base = next((c for c in CONDITIONS if condition.startswith(c)), "other")
        runs.append({
            "file": Path(path).name,
            "condition": condition,
            "mode": mode,
            "group": base,
            "deviations_ms": [float(x) for x in deviations],
            "payload_verification": data.get("payload_verification"),
            "physical_motor_writes": data.get("physical_motor_writes"),
            "transport": data.get("transport"),
            "frames_received": data.get("frames_received"),
        })
    return runs


def stats(deviations):
    absolute = [abs(x) for x in deviations]
    return {
        "n": len(deviations),
        "pstdev_ms": statistics.pstdev(deviations),
        "p99_abs_deviation_ms": percentile_nearest(absolute, 0.99),
        "max_abs_deviation_ms": max(absolute),
    }


def analyse(runs, checks, hashes):
    # ---- integrity precondition -------------------------------------------
    verified = [r for r in runs if r["payload_verification"] == "exact frame sequence"]
    physical_writes = [r["file"] for r in runs if r["physical_motor_writes"]]

    # ---- per group ---------------------------------------------------------
    groups = []
    for mode in ("MAXN", "15W"):
        for base in CONDITIONS:
            selected = [r for r in runs if r["mode"] == mode and r["group"] == base]
            if not selected:
                continue
            pooled = [x for r in selected for x in r["deviations_ms"]]
            per_run_p99 = [stats(r["deviations_ms"])["p99_abs_deviation_ms"]
                           for r in selected]
            s = stats(pooled)
            groups.append({
                "mode": mode,
                "condition": base,
                "runs": len(selected),
                "frames": len(pooled),
                "host_pstdev_ms": s["pstdev_ms"],
                "host_p99_abs_deviation_ms": s["p99_abs_deviation_ms"],
                "host_max_abs_deviation_ms": s["max_abs_deviation_ms"],
                "run_level_p99_ms": {
                    "min": min(per_run_p99),
                    "median": statistics.median(per_run_p99),
                    "max": max(per_run_p99),
                },
                "direct_linux": {
                    "pulse_train_p99_abs_deviation_ms": s["p99_abs_deviation_ms"],
                    "pulse_train_max_abs_deviation_ms": s["max_abs_deviation_ms"],
                    "host_jitter_transfer_ratio": 1.0,
                    "basis": "counterfactual: pulse cadence equals host frame cadence",
                },
                "finite_pulse_offload": {
                    "host_jitter_transfer_to_pulse_train_ratio": 0.0,
                    "pulse_train_p99_abs_deviation_ms_from_host_jitter": 0.0,
                    "command_start_phase_p99_abs_deviation_ms": s["p99_abs_deviation_ms"],
                    "command_start_phase_max_abs_deviation_ms": s["max_abs_deviation_ms"],
                    "basis": "contract: N pulses on the MCU local clock, then StopAllMotors",
                },
            })

    # ---- bounded stopping --------------------------------------------------
    def bound(n):
        return n * TURN_PULSE_PERIOD_MS / 1000.0

    bounds = []
    for label, n, origin in (
        ("runtime head minimum", RUNTIME_HEAD_PULSE_MIN, "host clamp lower bound"),
        ("MCU fixed counter-head", MPAN_PULSE_COUNT, "firmware MPAN_PULSE_COUNT"),
        ("runtime body maximum", RUNTIME_BODY_PULSE_MAX, "host clamp upper bound"),
        ("largest 16-bit frame value", FRAME_FIELD_MAX, "accepted by firmware, no clamp"),
    ):
        bounds.append({
            "case": label,
            "pulse_count": n,
            "origin": origin,
            "turnbody_residual_motion_s": round(bound(n), 3),
            "turnbody_residual_motion_min": round(bound(n) / 60.0, 2),
        })

    finding = {
        "id": "FIRMWARE_HAS_NO_PULSE_UPPER_BOUND",
        "status": "static_source_finding_plus_analytical_bound",
        "description": (
            "ProcessCommand() parses a 16-bit pulse field and passes it directly "
            "to TurnBody()/MoveForward() without clamping. The finite-pulse "
            "boundedness therefore comes from the host-side clamp (max 25 body "
            "pulses in the audited runtime), not from the MCU execution boundary. "
            "Because the seven-byte frame has neither a sequence number nor a "
            "CRC, a corrupted pulse field is not detected by the controller."
        ),
        "worst_case_turnbody_residual_motion_s": round(bound(FRAME_FIELD_MAX), 3),
        "worst_case_turnbody_residual_motion_min": round(bound(FRAME_FIELD_MAX) / 60.0, 2),
        "persistent_velocity_counterfactual": (
            "unbounded: no command timeout or hardware watchdog is implemented "
            "(main.tex L225), so motion would continue until an external stop"
        ),
        "physical_verification": "not performed; requires a logic analyser and a secured robot",
    }

    return {
        "integrity": {
            "runs_total": len(runs),
            "runs_with_exact_frame_sequence": len(verified),
            "frames_analysed": sum(len(r["deviations_ms"]) for r in runs),
            "runs_with_physical_motor_writes": physical_writes,
            "transport": sorted({str(r["transport"]) for r in runs}),
        },
        "contract_checks": checks,
        "source_hashes": hashes,
        "firmware_constants": {
            "speed_normal_ms": SPEED_NORMAL_MS,
            "pulse_gap_ms": PULSE_GAP_MS,
            "turn_pulse_period_ms": TURN_PULSE_PERIOD_MS,
            "mpan_pulse_count": MPAN_PULSE_COUNT,
            "trailing_settle_ms": TRAILING_SETTLE_MS,
            "runtime_body_pulse_max": RUNTIME_BODY_PULSE_MAX,
            "frame_pulse_field_max": FRAME_FIELD_MAX,
        },
        "groups": groups,
        "bounded_stopping": {
            "definition": (
                "Upper bound on motion remaining after the host stops sending: "
                "at most the pulse count of the command already executing, each "
                "pulse taking SPEED_NORMAL + gap on the MCU clock, then "
                "StopAllMotors() and a 1000 ms settle delay."
            ),
            "formula_s": "N * (35 + 8) / 1000",
            "cases": bounds,
            "finding": finding,
        },
        "conclusion": {
            "what_the_boundary_isolates": (
                "pulse-train cadence inside a command, and runaway motion after "
                "the host stops (bounded by the command's pulse count)"
            ),
            "what_it_does_not_isolate": (
                "the start-phase jitter of a command, which still equals the "
                "measured host-side frame deviation"
            ),
            "caveat": (
                "The 1:1 and 0:1 transfer ratios are consequences of the two "
                "execution contracts, not empirical estimates. Only the "
                "host-side frame timestamps are measurements."
            ),
        },
    }


def build_markdown(payload):
    a = payload["analysis"]
    lines = [
        "# Finite-pulse execution boundary: trace-driven isolation analysis",
        "",
        "Generated by `scripts/boundary_isolation.py`. **This is not a physical measurement.** "
        "Host frame timestamps are real measurements from the Xavier; all execution-side "
        "quantities are derived from the audited firmware contract "
        "(`evidence/motor_uart_vision.current.c`) and must be verified with a logic analyser "
        "before any hardware claim.",
        "",
        "## Input integrity",
        "",
        "- %d A/B runs, %d measured 50 ms frame intervals, %d/%d with an exact received frame sequence." % (
            a["integrity"]["runs_total"], a["integrity"]["frames_analysed"],
            a["integrity"]["runs_with_exact_frame_sequence"], a["integrity"]["runs_total"]),
        "- Physical motor writes: %s. Transport: %s." % (
            "none" if not a["integrity"]["runs_with_physical_motor_writes"] else "PRESENT",
            ", ".join(a["integrity"]["transport"])),
        "- Contract re-verification against the captured source snapshots: %d/%d checks true." % (
            sum(1 for v in a["contract_checks"].values() if v), len(a["contract_checks"])),
        "",
        "## What the boundary isolates (Q2)",
        "",
        "The same measured host trace drives two execution organisations. `direct` is the "
        "counterfactual historical path (Linux generates the pulses); `offloaded` is the "
        "shipped seven-byte finite-pulse architecture.",
        "",
        "| Mode | Condition | runs | frames | host p99 dev (ms, measured) | host max dev (ms, measured) | direct pulse-train p99 (ms, counterfactual) | offloaded pulse-train jitter attributable to host (ms, contract-derived) | offloaded command start-phase p99 (ms, contract-derived) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for g in a["groups"]:
        lines.append("| %s | %s | %d | %d | %.3f | %.3f | %.3f | %.3f | %.3f |" % (
            g["mode"], g["condition"], g["runs"], g["frames"],
            g["host_p99_abs_deviation_ms"], g["host_max_abs_deviation_ms"],
            g["direct_linux"]["pulse_train_p99_abs_deviation_ms"],
            g["finite_pulse_offload"]["pulse_train_p99_abs_deviation_ms_from_host_jitter"],
            g["finite_pulse_offload"]["command_start_phase_p99_abs_deviation_ms"]))
    lines += [
        "",
        "**Reading of the table.** The boundary does *not* make the robot timing-jitter free: "
        "the whole motion still starts late by exactly the host-side deviation, whose p99 "
        "ranges from %.3f to %.3f ms across the measured conditions. What it removes is the "
        "transfer of host jitter *into the pulse train*, plus persistent-PWM runaway. This "
        "sharpens `main.tex` L82 from a blanket claim into two separable quantities." % (
            min(g["host_p99_abs_deviation_ms"] for g in a["groups"]),
            max(g["host_p99_abs_deviation_ms"] for g in a["groups"])),
        "",
        "The 0:1 transfer ratio is a **contract property**, not an empirical estimate: the MCU "
        "runs `TurnBody()` on its own `delay_ms()` clock. MCU-local clock drift was not measured.",
        "",
        "## Bounded stopping, and where the bound actually comes from",
        "",
        "After the host stops sending, at most the pulse count of the command already executing "
        "remains: `N * (SPEED_NORMAL 35 + gap 8) ms`, then `StopAllMotors()` and a 1000 ms settle.",
        "",
        "| Case | N | Residual motion upper bound | Bound enforced by |",
        "| --- | --- | --- | --- |",
    ]
    for c in a["bounded_stopping"]["cases"]:
        val = ("%.3f s" % c["turnbody_residual_motion_s"]
               if c["turnbody_residual_motion_s"] < 60
               else "%.2f min" % c["turnbody_residual_motion_min"])
        lines.append("| %s | %d | %s | %s |" % (
            c["case"], c["pulse_count"], val, c["origin"]))
    f = a["bounded_stopping"]["finding"]
    lines += [
        "",
        "### New finding: `%s`" % f["id"],
        "",
        f["description"],
        "",
        "Worst case for `TurnBody()`: **%.1f s (%.2f min)** of residual motion from a single "
        "seven-byte frame. The persistent-velocity counterfactual is unbounded because no "
        "command timeout or hardware watchdog exists (`main.tex` L225)." % (
            f["worst_case_turnbody_residual_motion_s"],
            f["worst_case_turnbody_residual_motion_min"]),
        "",
        "This gives a concrete, quantitative reason for the paper's existing caveats: the "
        "finite-pulse boundary removes one runaway state, but its **bound is a host-side policy, "
        "not an MCU guarantee**, and the frame has neither CRC nor a sequence number "
        "(`main.tex` L121, L269). Adding integrity and a firmware-side pulse ceiling is a "
        "specific, testable design recommendation that follows directly from the measurement.",
        "",
        "## Evidence status",
        "",
        "- `analysis_type`: `%s`" % payload["analysis_type"],
        "- `physical_measurement`: false; `device_measurement`: false; `citation_allowed`: false.",
        "- Do not describe any execution-side number as observed hardware behaviour.",
        "- The historical 208x direct-control ratio is **not** reproduced or replaced by this analysis.",
        "",
        "Related: `EVALUATION_REDESIGN.md` (why this experiment replaces throughput-first testing), "
        "`ENVIRONMENT_NOISE.md` (why the 15 W idle baseline is contaminated).",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    args = parser.parse_args()

    checks, hashes = verify_contract()
    runs = load_runs()
    if not runs:
        raise SystemExit("no ab_control_*.json traces with signed_deviations_ms found")
    analysis = analyse(runs, checks, hashes)

    payload = {
        "schema": "gedgebot.boundary_isolation.v1",
        "generated_at_utc": utc_now_iso(),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(__file__),
        "analysis_type": "trace_driven_contract_analysis",
        "physical_measurement": False,
        "device_measurement": False,
        "citation_allowed": False,
        "evidence_note": (
            "Only the host-side frame timestamps are measurements (Xavier, 2026-11-07). "
            "Execution-side quantities are derived from the audited firmware contract and "
            "are NOT logic-analyser observations. Never describe them as physical measurements, "
            "and never pool them with the transcribed October 2026 values."
        ),
        "assumptions": [
            "All seven-byte frames arrive intact and in order (verified for every input run).",
            "The MCU local delay_ms() clock is stable and independent of Xavier load.",
            "The captured firmware snapshot is the one executed by the controller.",
            "No hardware watchdog or command timeout exists, as stated in main.tex L225.",
        ],
        "analysis": analysis,
    }

    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    Path(args.md_out).write_text(build_markdown(payload))

    failed = [k for k, v in checks.items() if not v]
    print("boundary isolation: %d runs, %d measured frames -> %s" % (
        analysis["integrity"]["runs_total"],
        analysis["integrity"]["frames_analysed"], json_path))
    print("contract checks: %d/%d true%s" % (
        len(checks) - len(failed), len(checks),
        " (failed: %s)" % ", ".join(failed) if failed else ""))
    print("firmware pulse clamp: %s" % (
        "ABSENT - bound is host-side only" if checks["firmware_does_not_clamp_pulse"]
        else "present"))
    print("markdown: %s" % args.md_out)


if __name__ == "__main__":
    main()
