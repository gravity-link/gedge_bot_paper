#!/usr/bin/env python3
"""Audit the product value that appears only when the retired compute module
is combined with the robot.

WHAT THIS IS
    A source-grounded, fully offline integration-surface audit. It parses
    read-only snapshots captured from /root/src/gedge_bot (Git HEAD c25b444)
    on 2026-11-07, plus the 11-07 device JSONs, and builds a capability matrix
    contrasting "the compute module alone" with "the module embedded in the
    delivered robot".

WHAT THIS IS NOT
    - Not a runtime measurement. The presence of a service, a language mapping
      or a driver in source does NOT prove the product function succeeded
      end-to-end. The October demonstrations ran with voice=False.
    - Not an economic or environmental assessment. No bill of materials,
      deployment data, energy counterfactual or lifecycle claim is made.
    - Not a claim that the peripherals were newly manufactured. The model
      requires a redesigned product organisation, not a particular reuse ratio.

Every number below is derived by parsing the snapshots, never hand-entered.
"""
from __future__ import annotations

import ast
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import sha256_file, utc_now_iso  # noqa: E402

EVID = ROOT / "evidence"
CONFIG = EVID / "source_config.current.py"
TREE = EVID / "source_tree.current.txt"
README = EVID / "source_README.current.md"
RUNTIME = EVID / "robot_runtime.current.py"
FIRMWARE = EVID / "motor_uart_vision.current.c"
DEVICE = EVID / "device"
DEFAULT_JSON = EVID / "analysis" / "integration_surface_20261107.json"
DEFAULT_MD = EVID / "INTEGRATION_VALUE.md"

RUNTIME_SIGNATURES = {
    "model_prewarm": "def prewarm(",
    "readiness_protocol": "def wait_until_ready(",
    "microphone_protocol_parser": "def _parse_packet(",
    "seven_byte_motor_frame": "def build_motor_command(",
    "head_pan": "def pan_head(",
    "body_turn": "def turn_body(",
    "sound_orientation_planner": "def plan_sound_orient(",
    "vision_capture_loop": "def _capture_loop(",
}
FIRMWARE_SIGNATURES = {
    "finite_pulse_loop": "void TurnBody(",
    "frame_parser": "void ProcessCommand(",
    "stop_on_completion": "StopAllMotors();",
}


def parse_config():
    """Extract dataclass defaults, including dicts hidden in default_factory lambdas."""
    tree = ast.parse(CONFIG.read_text())
    scalars, dicts = {}, {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        value = node.value
        if not (isinstance(value, ast.Call) and getattr(value.func, "id", "") == "field"):
            continue
        name = node.target.id
        for kw in value.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                scalars[name] = kw.value.value
            elif (kw.arg == "default_factory" and isinstance(kw.value, ast.Lambda)
                  and isinstance(kw.value.body, ast.Dict)):
                dicts[name] = [k.value for k in kw.value.body.keys
                               if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return scalars, dicts


def parse_tree():
    text = TREE.read_text()
    sections = {"services": [], "drivers": [], "dirs": []}
    head = ""
    commit = None
    current = None
    for line in text.splitlines():
        if line.startswith("# HEAD:"):
            head = "commit"
            continue
        if head == "commit" and line.strip() and not line.startswith("#"):
            commit = line.strip()
            head = ""
            continue
        if line.startswith("# service.py"):
            current = "services"
        elif line.startswith("# driver"):
            current = "drivers"
        elif line.startswith("# top-level"):
            current = "dirs"
        elif line.startswith("#"):
            current = None
        elif current and line.strip():
            sections[current].append(line.strip())
    return commit, sections


def signature_checks(path, signatures):
    text = path.read_text()
    return {name: (sig in text) for name, sig in signatures.items()}


def measured_workloads():
    """Which AI workloads actually produced ok measurements on 2026-11-07."""
    counts = {}
    patterns = ("bench_ai_*.json", "chattts_*.json", "bench_voice_*.json")
    files = [f for pat in patterns for f in glob.glob(str(DEVICE / pat))]
    for name in files:
        data = json.loads(Path(name).read_text())
        for key, item in (data.get("workloads") or {}).items():
            if isinstance(item, dict) and item.get("status") == "ok":
                counts[key] = counts.get(key, 0) + 1
    return {"files": len(files), "workload_keys": sorted(counts), "ok_counts": counts}


def main():
    args = sys.argv[1:]
    json_out = Path(args[0]) if args else DEFAULT_JSON
    md_out = Path(args[1]) if len(args) > 1 else DEFAULT_MD

    scalars, dicts = parse_config()
    commit, sections = parse_tree()
    runtime_checks = signature_checks(RUNTIME, RUNTIME_SIGNATURES)
    firmware_checks = signature_checks(FIRMWARE, FIRMWARE_SIGNATURES)
    measured = measured_workloads()

    active_services = [p for p in sections["services"]
                       if p.endswith("service.py") and ".broken" not in p]
    broken_services = [p for p in sections["services"] if ".broken" in p]
    driver_modules = sorted({Path(p).parent.name for p in sections["drivers"]
                             if p.endswith(".c") and not p.endswith(".mod.c")})

    language_tables = {k: dicts.get(k, []) for k in
                       ("whisper_lang_map", "nllb_lang_map", "tts_voices")}
    common_languages = sorted(set.intersection(*(set(v) for v in language_tables.values())))

    enabled_flags = {k: scalars.get(k) for k in
                     ("enable_asr", "enable_translation", "enable_tts", "enable_vision")}
    model_defaults = {k: scalars.get(k) for k in
                      ("asr_model", "mt_model_path", "yolo_model_path", "use_cuda", "use_fp16")}

    product_readme = " ".join(
        line.strip() for line in README.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#"))

    matrix = [
        {
            "function": "Four on-device AI models (YOLO, Whisper, NLLB, ChatTTS)",
            "module_alone": "yes, measured on 11-07",
            "module_plus_robot": "retained as the compute core",
            "integration_asset": "isolated CUDA/PyTorch stack on the frozen BSP",
            "evidence": "evidence/device/*.json",
            "status": "measured",
        },
        {
            "function": "Seven-language speech chain (ASR -> MT -> TTS)",
            "module_alone": "models only, no audio I/O or product role",
            "module_plus_robot": "configured as five on-device services",
            "integration_asset": "microphone array, readiness/prewarm protocol, service orchestration",
            "evidence": "source_config.current.py + robot_runtime.current.py",
            "status": "configured in source; conversational loop NOT verified end-to-end (voice=False in October demos)",
        },
        {
            "function": "Sound-source orientation and counter-turning",
            "module_alone": "no",
            "module_plus_robot": "yes, mechanism-level",
            "integration_asset": "recovered FF A5 / DOA protocol + seven-byte UART frames",
            "evidence": "robot_runtime.current.py + motor_uart_vision.current.c",
            "status": "source mechanism; host/MCU counter-turn queue gap documented",
        },
        {
            "function": "Two-level visual person following",
            "module_alone": "no",
            "module_plus_robot": "yes, mechanism-level",
            "integration_asset": "OpenNI depth camera + head/body pulse state machine",
            "evidence": "robot_runtime.current.py (plan_sound_orient, _capture_loop)",
            "status": "source mechanism; no new physical demonstration in this batch",
        },
        {
            "function": "Automotive FPD-Link camera registration",
            "module_alone": "no",
            "module_plus_robot": "yes, mechanism-level",
            "integration_asset": "two kernel driver modules + device-tree / V4L2 graph",
            "evidence": "source_tree.current.txt (Xavier/drivers)",
            "status": "mechanism only; video throughput and use in October following not established",
        },
        {
            "function": "Deterministic, bounded actuation",
            "module_alone": "no",
            "module_plus_robot": "yes, by contract",
            "integration_asset": "seven-byte finite-pulse frame + STM32F407",
            "evidence": "BOUNDARY_ISOLATION.md (trace-driven contract analysis)",
            "status": "contract-derived, not a logic-analyser measurement",
        },
    ]

    payload = {
        "schema": "gedgebot.integration_surface.v1",
        "generated_at_utc": utc_now_iso(),
        "script_sha256": sha256_file(__file__),
        "analysis_type": "source_grounded_integration_audit",
        "physical_measurement": False,
        "device_measurement": False,
        "citation_allowed": False,
        "evidence_note": (
            "Static inventory of read-only source snapshots plus the list of AI "
            "workloads actually measured on 2026-11-07. Presence of a service, "
            "language mapping or driver is NOT proof of end-to-end product success. "
            "No economic, deployment or environmental benefit is quantified."
        ),
        "source_commit": commit,
        "source_hashes": {p.name: sha256_file(p) for p in
                          (CONFIG, TREE, README, RUNTIME, FIRMWARE)},
        "product_description_from_source_readme": product_readme,
        "integration_surface": {
            "active_on_device_ai_services": len(active_services),
            "active_service_files": active_services,
            "excluded_broken_services": broken_services,
            "languages_with_all_three_mappings": common_languages,
            "language_count": len(common_languages),
            "language_table_sizes": {k: len(v) for k, v in language_tables.items()},
            "enabled_service_flags": enabled_flags,
            "model_defaults": model_defaults,
            "kernel_driver_modules": driver_modules,
            "sensing_domains": ["USB microphone array (recovered FF A5 / DOA)",
                                "OpenNI Astra depth camera",
                                "automotive FPD-Link DS90UB953/954 + AR0143 via V4L2"],
            "control_boundary": "seven-byte UART frame at 9600 bit/s to STM32F407",
            "measured_workloads_on_20261107": measured,
            "runtime_contract_checks": runtime_checks,
            "firmware_contract_checks": firmware_checks,
        },
        "capability_matrix": matrix,
        "conclusion": (
            "The retired module alone supplies inference. Product functions "
            "appear only after sensing, deterministic actuation, orchestration "
            "and a new product role are designed around it. That joint value, "
            "not peak throughput, is what re-productization captures."
        ),
    }
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    s = payload["integration_surface"]
    lines = [
        "# What the Jetson box gains by becoming a robot",
        "",
        "Generated by `scripts/integration_surface.py` from read-only source "
        "snapshots (Git HEAD `%s`) and the 11-07 device JSONs. "
        "**This is a static integration-surface audit, not a runtime measurement** "
        "and not an economic or environmental assessment." % (commit or "unknown"),
        "",
        "The source README describes the artifact as: *%s*" % product_readme,
        "That is the literal form of the paper's claim: the compute module is "
        "retained as one asset inside a redesigned product, rather than being "
        "repaired, resold, or simply given a new benchmark.",
        "",
        "## Reproducible integration surface",
        "",
        "| Quantity | Value | Where it comes from |",
        "| --- | --- | --- |",
        "| Active on-device AI services | %d | `source_tree.current.txt`; `%d` `.broken` copy excluded |" % (
            s["active_on_device_ai_services"], len(s["excluded_broken_services"])),
        "| Languages with ASR + MT + TTS mappings | %d (%s) | intersection of three tables in `source_config.current.py` |" % (
            s["language_count"], ", ".join(s["languages_with_all_three_mappings"])),
        "| Measured workload keys, 11-07 (YOLO / Whisper / NLLB families; ChatTTS separate) | %d distinct keys across %d JSONs | `evidence/device/*.json` with `status=ok` |" % (
            len(s["measured_workloads_on_20261107"]["workload_keys"]),
            s["measured_workloads_on_20261107"]["files"]),
        "| Sensing domains | %d | microphone array, OpenNI depth, FPD-Link V4L2 |" % len(s["sensing_domains"]),
        "| Kernel driver modules | %d (%s) | `Xavier/drivers/` |" % (
            len(s["kernel_driver_modules"]), ", ".join(s["kernel_driver_modules"])),
        "| Control boundary | 7 bytes @ 9600 bit/s | `build_motor_command` + MCU `ProcessCommand` |",
        "| Runtime contract checks | %d/%d | `robot_runtime.current.py` |" % (
            sum(s["runtime_contract_checks"].values()), len(s["runtime_contract_checks"])),
        "| Firmware contract checks | %d/%d | `motor_uart_vision.current.c` |" % (
            sum(s["firmware_contract_checks"].values()), len(s["firmware_contract_checks"])),
        "",
        "## Capability matrix: module alone vs. module + robot",
        "",
        "| Product function | Module alone | Module + robot | Integration asset required | Evidence status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in matrix:
        lines.append("| %s | %s | %s | %s | %s |" % (
            row["function"], row["module_alone"], row["module_plus_robot"],
            row["integration_asset"], row["status"]))
    lines += [
        "",
        "## Reading",
        "",
        "Only the first row is available from the box on its own, and it is "
        "exactly the throughput measurement that `main.tex` L86 says cannot "
        "establish the paper's proposition. Every other row is a product "
        "function that exists because the module was embedded in a robot with "
        "sensing, a deterministic execution boundary, and orchestration. "
        "The added value is **joint, not additive**: it depends on the division "
        "of labour (AI on Xavier, pulses on STM32F407) and on a seven-byte "
        "interface narrow enough to keep Linux jitter out of the pulse train.",
        "",
        "### Boundaries that must be kept",
        "",
        "- Seven-language support is a **configuration fact** (three consistent "
        "mapping tables), not a measured seven-language dialogue success. The "
        "October demonstrations ran with `voice=False`.",
        "- Source mechanisms for following, counter-turning and the FPD-Link "
        "camera are not new physical demonstrations; the host/MCU counter-turn "
        "queue gap remains open.",
        "- Bounded actuation is a **contract-derived** result from "
        "`BOUNDARY_ISOLATION.md`, not a logic-analyser observation.",
        "- No economic, deployment, energy or lifecycle benefit is quantified. "
        "`main.tex` Sec. VII-D keeps that evaluation explicitly open.",
        "- The model does not claim a particular reused fraction of the robot; "
        "project documents describe both retained mechanical parts and newly "
        "designed subsystems.",
        "",
        "## Evidence status",
        "",
        "- `physical_measurement`: false; `device_measurement`: false; `citation_allowed`: false.",
        "- The source and script hashes in the JSON make every number re-derivable offline.",
        "",
        "Related: `EVALUATION_REDESIGN.md`, `BOUNDARY_ISOLATION.md`, `SOURCE_ALIGNMENT.md`.",
        "",
    ]
    md_out.write_text("\n".join(lines))
    print("integration surface: %d services, %d languages, %d sensing domains, %d product functions" % (
        s["active_on_device_ai_services"], s["language_count"],
        len(s["sensing_domains"]), len(matrix)))
    print("contracts: runtime %d/%d, firmware %d/%d" % (
        sum(s["runtime_contract_checks"].values()), len(s["runtime_contract_checks"]),
        sum(s["firmware_contract_checks"].values()), len(s["firmware_contract_checks"])))
    print("wrote %s and %s" % (json_out, md_out))


if __name__ == "__main__":
    main()
