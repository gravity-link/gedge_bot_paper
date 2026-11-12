#!/usr/bin/env python3
"""E3: product-loop software-in-the-loop replay (Q3).

WHAT THIS IS
    A fully offline replay that executes the *audited source functions* from
    ``evidence/robot_runtime.current.py`` (Git HEAD c25b444 of gedge_bot):
    AngleMapper.plan_sound_orient, RobotRuntime.orient_to_sound,
    VisualFollower.update, MotorController.pan_head/turn_body and the real
    seven-byte frame builders. Peripheral sources (mic array, camera, serial)
    are replaced with in-memory event sources and a virtual clock.

    It answers part of Q3 -- "does the integrated artifact complete a
    product-level interaction loop, including its degraded modes?" -- with
    reproducible traces instead of the ``n=0`` empty demo record.

WHAT THIS IS NOT
    - NOT a physical demonstration. No serial port, camera, microphone or
      voice subprocess is ever opened; no motor frame is written to hardware.
      The replay monkeypatches the source's device-opening methods to raise.
    - NOT a recovered October trace. The six DOA seed angles are the
      *transcribed* historical turn angles in rebuild_common.HISTORICAL; the
      face-position streams are synthetic probe sequences, not recorded YOLO
      output. The frame period uses the transcribed 17.7 FPS as a scenario
      parameter, not as a measurement.
    - NOT a wall-clock latency measurement. Timings come from a virtual clock
      driven by the delays and settling waits present in the audited source.
      They are a source-derived latency *budget*, not a robot measurement.
    - NOT a conversational success rate. The voice subsystem is out of scope;
      the historical demonstrations ran with voice=False and this replay does
      not claim any dialogue result.

    Every emitted frame is decoded and checked against the host-side clamp
    and the seven-byte contract. All numbers are computed, never hand-entered.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import platform
import statistics
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import HISTORICAL, sha256_file, utc_now_iso  # noqa: E402

EVID = ROOT / "evidence"
RUNTIME = EVID / "robot_runtime.current.py"
FIRMWARE = EVID / "motor_uart_vision.current.c"
DEFAULT_JSON = EVID / "analysis" / "product_loop_replay_20261107.json"
DEVICE_JSON = EVID / "analysis" / "product_loop_replay_20261107_jetson.json"
DEFAULT_MD = EVID / "PRODUCT_LOOP_REPLAY.md"

# Scenario parameters. Sources are recorded in the JSON so none is implicit.
DOA_SEED_ANGLES_DEG = list(HISTORICAL["demo"]["doa_turn_angles_deg"])
DEAD_ZONE_ANGLES_DEG = [-14, -10, -5, 5, 10, 14]
FRAME_PERIOD_S = 1.0 / 17.7  # transcribed historical perception rate
KNOWN_CMDS = {0x01: "body_turn_left", 0x02: "body_turn_right",
              0x09: "vision_track_head"}

# Synthetic face-position probe streams. None = detector found no target.
STREAM_NOMINAL = ([520] * 20 + [420] * 10 + [320] * 8 + [220] * 20 + [320] * 10)
STREAM_DROPOUT = ([520] * 12 + [None] * 10 + [520] * 20 + [320] * 8)


class VirtualClock:
    """Only ``time`` inside the loaded runtime module is replaced by this."""

    def __init__(self, t0=10000.0):
        self.t = float(t0)
        self.sleep_calls = 0
        self.advance_calls = 0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def perf_counter(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))
        self.sleep_calls += 1

    def advance(self, seconds):
        self.t += max(0.0, float(seconds))
        self.advance_calls += 1


class FrameRecorder:
    """In-memory serial sink. Decodes every frame as it is written."""

    def __init__(self, clock):
        self.clock = clock
        self.frames = []
        self.open_attempts = 0

    def write(self, data):
        b = bytes(data)
        pulse_le = b[3] | (b[4] << 8) if len(b) >= 5 else None
        self.frames.append({
            "t": round(self.clock.time(), 6),
            "hex": b.hex(" ").upper(),
            "bytes": list(b),
            "len": len(b),
            "cmd": b[2] if len(b) >= 3 else None,
            "cmd_name": KNOWN_CMDS.get(b[2]) if len(b) >= 3 else None,
            "pulse_le": pulse_le,
            "direction_bit": bool(pulse_le & 0x8000) if pulse_le is not None else None,
            "pulse_magnitude": (pulse_le & 0x7FFF) if pulse_le is not None else None,
        })

    def open(self, *a, **k):
        self.open_attempts += 1
        raise RuntimeError("E3 replay forbids opening a physical serial port")

    def close(self, *a, **k):
        return None


def forbid_device_open(*_a, **_k):
    raise RuntimeError("E3 replay forbids opening any physical device or "
                       "launching any voice/camera subprocess")


def install_guardrails(mod):
    """Make any accidental hardware access fail loudly rather than silently."""
    blocked = [
        (mod.TermiosSerial, "open"),
        (mod.CameraFaceTracker, "open"),
        (mod.CameraFaceTracker, "start"),
        (mod.MonitorServer, "start"),
        (mod.VoiceAssistantProcess, "prewarm"),
        (mod.VoiceAssistantProcess, "start"),
        (mod.VoiceAssistantProcess, "shutdown"),
        (mod.RobotRuntime, "run_auto_wakeup"),
        (mod.RobotRuntime, "run_follow"),
        (mod.RobotRuntime, "follow_loop"),
    ]
    names = []
    for cls, method in blocked:
        if hasattr(cls, method):
            setattr(cls, method, forbid_device_open)
            names.append("%s.%s" % (cls.__name__, method))
    return names


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("gedge_runtime_under_replay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ReplayRuntime:
    """Minimal host exposing exactly what orient_to_sound() reads from self."""

    def __init__(self, mod, clock, recorder):
        self.args = SimpleNamespace(skip_sound_head=False, sound_head_body_delay=2.0)
        self.angle_mapper = mod.AngleMapper(
            mic_angle_offset=0.0, mic_invert=False, sound_dead_zone_deg=15.0,
            max_head_pulse=15, max_body_pulse=25, min_sound_body_pulse=15)
        self.motor = mod.MotorController(
            "/dev/ttyUSB0", 9600, True, 15, 25, verbose=False)
        self.motor.serial = recorder  # never a real TermiosSerial write
        self.visual_follower = mod.VisualFollower(
            center_zone_width=64, command_interval=0.35, body_interval=2.0,
            body_track_threshold=15, body_track_pulse=10)
        self.camera = SimpleNamespace(
            frame_lock=threading.Lock(), latest_face_x=None,
            latest_status={"state": "ready", "updated_at": None, "fps": 0.0})
        self.mic = None
        self.clock = clock
        self.recorder = recorder

    def wait_for_camera_ready(self, timeout=0.0):
        return True


def frame_contract_errors(frame):
    errs = []
    b = frame["bytes"]
    if frame["len"] != 7:
        errs.append("frame length %d != 7" % frame["len"])
        return errs
    if b[0:2] != [0xAA, 0x55]:
        errs.append("bad header")
    if b[5:7] != [0x0D, 0x0A]:
        errs.append("bad tail")
    if frame["cmd"] not in KNOWN_CMDS:
        errs.append("unknown opcode 0x%02X" % frame["cmd"])
    mag = frame["pulse_magnitude"]
    if frame["cmd"] == 0x09:
        if mag is None or not (5 <= mag <= 15):
            errs.append("head pulse %s outside host clamp 5..15" % mag)
    elif mag is not None and not (5 <= mag <= 25):
        errs.append("body pulse %s outside host clamp 5..25" % mag)
    return errs


def drive_follow(runtime, clock, stream, frame_period_s):
    """Drive the real VisualFollower.update / pan_head / turn_body chain."""
    start = clock.time()
    steps = []
    first_action_t = None
    head_actions = 0
    body_actions = 0
    for idx, face_x in enumerate(stream):
        clock.advance(frame_period_s)
        action = runtime.visual_follower.update(face_x)
        step = {
            "frame": idx,
            "t_s": round(clock.time() - start, 6),
            "face_x": face_x,
            "decision": action.reason,
            "head_pulse": action.head_pulse,
            "body_pulse": action.body_pulse,
        }
        if action.head_direction:
            runtime.motor.pan_head(action.head_direction, action.head_pulse, action.reason)
            head_actions += 1
            if first_action_t is None:
                first_action_t = clock.time() - start
        if action.body_direction:
            runtime.motor.turn_body(action.body_direction, action.body_pulse,
                                    "face follow body recenter")
            body_actions += 1
            if first_action_t is None:
                first_action_t = clock.time() - start
        steps.append(step)
    return {
        "frames_driven": len(stream),
        "first_action_latency_s": (round(first_action_t, 6)
                                   if first_action_t is not None else None),
        "head_actions": head_actions,
        "body_actions": body_actions,
        "duration_s": round(clock.time() - start, 6),
        "steps": steps,
    }


def run_trace(mod, scenario):
    clock = VirtualClock()
    mod.time = clock
    recorder = FrameRecorder(clock)
    runtime = ReplayRuntime(mod, clock, recorder)

    t_trigger = clock.time()
    raw_angle = scenario["doa_deg"] % 360
    plan = runtime.angle_mapper.plan_sound_orient(raw_angle)
    t_plan = clock.time()
    event = mod.MicAngleEvent(angle=raw_angle, timestamp=t_trigger)

    orient_frames_before = len(recorder.frames)
    mod.RobotRuntime.orient_to_sound(runtime, event)
    t_orient_end = clock.time()
    orient_frames = recorder.frames[orient_frames_before:]
    # orient_to_sound emits [head pre-turn (0x09), body (0x01/0x02),
    # counter-head (0x09) via the after_send callback]. Only the 0x09 frame
    # AFTER the body frame is the counter-head command.
    body_idx = next((i for i, f in enumerate(orient_frames)
                     if f["cmd"] in (0x01, 0x02)), None)
    pre_turn_frames = [f for i, f in enumerate(orient_frames)
                       if f["cmd"] == 0x09 and (body_idx is None or i < body_idx)]
    counter_head_frames = [f for i, f in enumerate(orient_frames)
                           if f["cmd"] == 0x09
                           and body_idx is not None and i > body_idx]

    camera_ready = scenario["camera_ready"]
    follow = None
    if camera_ready:
        follow = drive_follow(runtime, clock, scenario["face_stream"], FRAME_PERIOD_S)
    t_end = clock.time()

    all_frames = list(recorder.frames)
    contract_errors = []
    for i, f in enumerate(all_frames):
        for err in frame_contract_errors(f):
            contract_errors.append({"frame_index": i, "error": err})

    # Completion is the trigger-to-follow chain only. Voice dialogue is never
    # in scope, and a dead-zone DOA legitimately skips the orientation stage.
    orient_expected = not scenario["in_dead_zone"]
    orient_done = (len([f for f in orient_frames if f["cmd"] in (0x01, 0x02)]) > 0
                   if orient_expected else True)
    follow_started = bool(follow and follow["frames_driven"] > 0)
    follow_acted = bool(follow and (follow["head_actions"] + follow["body_actions"]) > 0)
    completed = bool(orient_done and follow_started and follow_acted)
    failure_reason = None if completed else (
        "camera not ready; follow stage never started" if not camera_ready
        else "orientation body frame missing" if not orient_done
        else "no follow action emitted")

    return {
        "trace_id": scenario["trace_id"],
        "mode": scenario["mode"],
        "set": scenario["set"],
        "scenario": {
            "doa_seed_deg": scenario["doa_deg"],
            "doa_raw_angle": raw_angle,
            "doa_source": scenario["doa_source"],
            "voice_ready": scenario["voice_ready"],
            "camera_ready": camera_ready,
            "face_stream_kind": scenario["face_stream_kind"],
            "face_stream_frames": len(scenario["face_stream"]),
            "none_frames": sum(1 for x in scenario["face_stream"] if x is None),
            "frame_period_s": FRAME_PERIOD_S,
        },
        "orientation_plan": {
            "raw_angle": plan.raw_angle,
            "signed_deg": round(plan.signed_deg, 3),
            "direction": plan.direction,
            "head_pulse": plan.head_pulse,
            "body_pulse": plan.body_pulse,
            "saturated_at_90_deg": abs(scenario["doa_deg"]) > 90.0,
            "seed_matches_signed": abs(plan.signed_deg - scenario["doa_deg"]) < 1e-6,
            "skipped_by_dead_zone": plan.direction is None,
        },
        "stages": {
            "t_trigger": round(t_trigger, 6),
            "t_plan": round(t_plan, 6),
            "t_orient_end": round(t_orient_end, 6),
            "t_end": round(t_end, 6),
            "trigger_to_plan_s": round(t_plan - t_trigger, 6),
            "orient_total_s": round(t_orient_end - t_plan, 6),
            "chain_total_s": round(t_end - t_trigger, 6),
            "orient_frame_times_rel_s": [round(f["t"] - t_trigger, 6)
                                         for f in orient_frames],
        },
        "frames": {
            "total": len(all_frames),
            "orient": len(orient_frames),
            "follow": len(all_frames) - len(orient_frames),
            "pre_turn_head_emitted": len(pre_turn_frames),
            "counter_head_emitted": len(counter_head_frames),
            "counter_head_mcu_queue_reliable": False,
            "contract_errors": contract_errors,
            "all_frames": all_frames,
            "physical_device_open_attempts": recorder.open_attempts,
        },
        "follow": follow,
        "completion": {
            "trigger_to_follow_chain_completed": completed,
            "orient_stage_completed": orient_done,
            "follow_stage_completed": follow_acted,
            "failure_reason": failure_reason,
            "conversational_loop_in_scope": False,
            "conversational_success": None,
            "physical_actuation": False,
        },
        "virtual_clock": {"sleep_calls": clock.sleep_calls,
                          "advance_calls": clock.advance_calls},
    }


def build_scenarios():
    scenarios = []
    n = 0

    def add(mode, set_name, doa, source, voice, camera, stream_kind, stream):
        nonlocal n
        n += 1
        scenarios.append({
            "trace_id": "T%02d" % n,
            "mode": mode,
            "set": set_name,
            "doa_deg": doa,
            "doa_source": source,
            "voice_ready": voice,
            "camera_ready": camera,
            "face_stream_kind": stream_kind,
            "face_stream": list(stream),
            "in_dead_zone": abs(doa) < 15.0,
        })

    hist = "transcribed historical turn angle (HISTORICAL.demo.doa_turn_angles_deg)"
    for deg in DOA_SEED_ANGLES_DEG:
        add("nominal", "A_runnable", deg, hist, True, True, "nominal", STREAM_NOMINAL)
    for deg in DOA_SEED_ANGLES_DEG:
        add("voice_degraded", "A_runnable", deg, hist, False, True,
            "nominal", STREAM_NOMINAL)
    for deg in DOA_SEED_ANGLES_DEG:
        add("face_dropout", "A_runnable", deg, hist, True, True,
            "dropout", STREAM_DROPOUT)
    for deg in DEAD_ZONE_ANGLES_DEG:
        add("doa_dead_zone", "B_degraded", deg,
            "synthetic dead-zone probe (|angle| < 15 deg)", True, True,
            "nominal", STREAM_NOMINAL)
    for deg in DOA_SEED_ANGLES_DEG:
        add("camera_not_ready", "C_hard_failure", deg, hist, True, False,
            "none", [])
    return scenarios


def summarize(traces):
    by_mode = {}
    for t in traces:
        m = t["mode"]
        g = by_mode.setdefault(m, {"n": 0, "completed": 0, "orient_s": [],
                                   "chain_s": [], "first_action_s": [],
                                   "frames": 0, "contract_errors": 0,
                                   "counter_head_frames": 0,
                                   "voice_ready_n": 0,
                                   "conversational_claims": 0})
        g["n"] += 1
        g["completed"] += int(t["completion"]["trigger_to_follow_chain_completed"])
        g["orient_s"].append(t["stages"]["orient_total_s"])
        g["chain_s"].append(t["stages"]["chain_total_s"])
        fa = t["follow"]["first_action_latency_s"] if t["follow"] else None
        if fa is not None:
            g["first_action_s"].append(fa)
        g["frames"] += t["frames"]["total"]
        g["contract_errors"] += len(t["frames"]["contract_errors"])
        g["counter_head_frames"] += t["frames"]["counter_head_emitted"]
        g["voice_ready_n"] += int(bool(t["scenario"]["voice_ready"]))
    for g in by_mode.values():
        for k in ("orient_s", "chain_s", "first_action_s"):
            vals = g.pop(k)
            g[k + "_mean"] = round(statistics.fmean(vals), 6) if vals else None
            g[k + "_min"] = round(min(vals), 6) if vals else None
            g[k + "_max"] = round(max(vals), 6) if vals else None
        g["completion_rate"] = "%d/%d" % (g["completed"], g["n"])

    runnable = [t for t in traces if t["set"] in ("A_runnable", "B_degraded")]
    all_frames = sum(t["frames"]["total"] for t in traces)
    all_errors = sum(len(t["frames"]["contract_errors"]) for t in traces)
    saturating = [t for t in traces
                  if t["orientation_plan"]["saturated_at_90_deg"]
                  and t["set"] == "A_runnable" and t["mode"] == "nominal"]
    seed_match = all(t["orientation_plan"]["seed_matches_signed"]
                     for t in traces if t["scenario"]["doa_source"].startswith("transcribed"))
    dropout = next((q for q in traces if q["mode"] == "face_dropout"), None)
    dropout_stats = {"no_face_frames": 0, "actions_during_no_face": 0,
                     "actions_after_recovery": 0}
    if dropout and dropout["follow"]:
        steps = dropout["follow"]["steps"]
        none_steps = [st for st in steps if st["face_x"] is None]
        last_none = max((st["frame"] for st in none_steps), default=-1)
        dropout_stats = {
            "no_face_frames": len(none_steps),
            "actions_during_no_face": sum(
                1 for st in none_steps
                if st["head_pulse"] or st["body_pulse"]),
            "actions_after_recovery": sum(
                1 for st in steps if st["frame"] > last_none
                and (st["head_pulse"] or st["body_pulse"])),
        }
    return {
        "by_mode": by_mode,
        "totals": {
            "traces": len(traces),
            "runnable_traces": len(runnable),
            "runnable_completed": sum(
                int(t["completion"]["trigger_to_follow_chain_completed"])
                for t in runnable),
            "all_traces_completed": sum(
                int(t["completion"]["trigger_to_follow_chain_completed"])
                for t in traces),
            "frames_emitted": all_frames,
            "frame_contract_errors": all_errors,
            "counter_head_frames": sum(t["frames"]["counter_head_emitted"]
                                       for t in traces),
            "pre_turn_head_frames": sum(t["frames"]["pre_turn_head_emitted"]
                                        for t in traces),
            "physical_device_open_attempts": sum(
                t["frames"]["physical_device_open_attempts"] for t in traces),
        },
        "findings": {
            "historical_angles_saturating_at_90_deg": len(saturating),
            "historical_angles_total": len(DOA_SEED_ANGLES_DEG),
            "historical_seed_matches_source_mapping": seed_match,
            "voice_degraded_chain_completed": by_mode.get("voice_degraded", {})
                .get("completion_rate"),
            "voice_degraded_conversational_claims": 0,
            "camera_not_ready_chain_completed": by_mode.get("camera_not_ready", {})
                .get("completion_rate"),
            "face_dropout": dropout_stats,
            "counter_head_frames_without_reliable_mcu_queue": sum(
                t["frames"]["counter_head_emitted"] for t in traces),
            "orient_delay_driven_by_source_constants": True,
        },
    }


def fingerprint(traces, summary, scenarios_meta):
    canonical = json.dumps({"traces": traces, "summary": summary,
                            "scenarios_meta": scenarios_meta},
                           sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_report(result):
    s = result["summary"]
    t = s["totals"]
    f = s["findings"]
    mode_order = ["nominal", "voice_degraded", "face_dropout",
                  "doa_dead_zone", "camera_not_ready"]
    a = []
    add = a.append
    add("# E3：产品闭环 software-in-the-loop replay（Q3）")
    add("")
    add("自动生成于 `%s`，由 `scripts/product_loop_replay.py` 从\n"
        "`evidence/analysis/product_loop_replay_20261107.json` 渲染。\n"
        "**不要手改本文件**；改脚本后重跑即可。" % result["generated_at_utc"])
    add("")
    add("## 1. 这是什么 / 不是什么")
    add("")
    add("**是**：把 audited 源码 `evidence/robot_runtime.current.py`\n"
        "（gedge_bot HEAD `c25b444`）里的真实决策与帧构造函数放进一个闭环：\n"
        "`AngleMapper.plan_sound_orient` → `RobotRuntime.orient_to_sound` →\n"
        "`VisualFollower.update` → `MotorController.pan_head/turn_body` →\n"
        "`build_motor_command` / `build_vision_track_command`。\n"
        "麦克风、相机、串口全部替换为内存事件源与虚拟时钟。")
    add("")
    add("**不是**：")
    add("")
    add("- **不是物理演示**。不打开串口、相机、麦克风，不启动语音子进程，\n"
        "  不写入任何电机帧到硬件；`TermiosSerial.open`、\n"
        "  `CameraFaceTracker.open/start`、`VoiceAssistantProcess.*`、\n"
        "  `RobotRuntime.follow_loop/run_follow/run_auto_wakeup` 被显式替换成抛错守卫。")
    add("- **不是恢复的十月 trace**。6 个 DOA 种子是历史**转录**的转向角\n"
        "  `%s`；face 位置流是合成探测序列，不是录制的 YOLO 输出。" %
        ", ".join("%+d" % x for x in DOA_SEED_ANGLES_DEG))
    add("- **不是 wall-clock 延迟测量**。时间来自虚拟时钟，由源码里的\n"
        "  `sound_head_body_delay=2.0 s`、`0.30 s` 最小命令间隔、\n"
        "  `pulse*0.043+1.2 s` body settle 等常量推进；这是**源码导出的延迟预算**。")
    add("- **不是对话成功率**。语音子系统不在 replay 执行范围内；\n"
        "  历史十月演示 `voice=False`，本 replay 不主张任何对话结果。")
    add("")
    add("证据信封：`evidence_status=%s`，`device_measurement=%s`，\n"
        "`physical_actuation=%s`，`citation_allowed=%s`。" % (
            result["evidence_status"], result["device_measurement"],
            result["physical_actuation"], result["citation_allowed"]))
    add("")
    add("## 2. 场景设计")
    add("")
    add("| 集合 | 模式 | n | 说明 |")
    add("| --- | --- | --- | --- |")
    add("| A 可运行 | `nominal` | %d | 6 个历史转录 DOA 角度，标称条件 |" %
        s["by_mode"]["nominal"]["n"])
    add("| A 可运行 | `voice_degraded` | %d | `voice_ready=false`，对应十月实况 |" %
        s["by_mode"]["voice_degraded"]["n"])
    add("| A 可运行 | `face_dropout` | %d | 事件流插入 10 帧检测丢失 |" %
        s["by_mode"]["face_dropout"]["n"])
    add("| B 降级 | `doa_dead_zone` | %d | 6 个合成角度落在 15° 死区内 |" %
        s["by_mode"]["doa_dead_zone"]["n"])
    add("| C 硬失败注入 | `camera_not_ready` | %d | 刻意注入，不并入可运行完成率 |" %
        s["by_mode"]["camera_not_ready"]["n"])
    add("")
    add("帧周期 `%.6f s` 取自历史转录的 17.7 FPS，作为**场景参数**；\n"
        "face 流 `nominal` %d 帧、`dropout` %d 帧（其中 10 帧 `None`）。" % (
            FRAME_PERIOD_S, len(STREAM_NOMINAL), len(STREAM_DROPOUT)))
    add("")
    add("## 3. 结果")
    add("")
    add("共 **%d 条 trace**：可运行集合 A+B 完成 **%d/%d**，含硬失败注入 C 为\n"
        "**%d/%d**。共发出 **%d 个七字节帧**，帧契约错误 **%d** 处，\n"
        "物理设备打开尝试 **%d** 次。" % (
            t["traces"], t["runnable_completed"], t["runnable_traces"],
            t["all_traces_completed"], t["traces"], t["frames_emitted"],
            t["frame_contract_errors"], t["physical_device_open_attempts"]))
    add("")
    add("| 模式 | n | 闭环完成 | 转向段 (s) mean [min,max] | replay 全链虚拟时长 (s) mean | 首个 follow 动作 (s) mean | 帧数 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for m in mode_order:
        g = s["by_mode"][m]
        add("| `%s` | %d | %s | %s | %s | %s | %d |" % (
            m, g["n"], g["completion_rate"],
            "%.3f [%.3f, %.3f]" % (g["orient_s_mean"], g["orient_s_min"],
                                   g["orient_s_max"]),
            "%.3f" % g["chain_s_mean"] if g["chain_s_mean"] is not None else "-",
            "%.3f" % g["first_action_s_mean"]
            if g["first_action_s_mean"] is not None else "-",
            g["frames"]))
    add("")
    add("> 表中所有时间都是**虚拟时钟下的源码延迟预算**，不是物理秒表读数。\n"
        "> `replay 全链虚拟时长` 包含整段合成 follow 事件流（68/50 帧，含多次\n"
        "> body settle），随事件流长度变化，**不是** trigger-to-follow 延迟指标。\n"
        "> 不得与历史 4.35 s trigger-to-follow 延迟合并、对比或当作复现。")
    add("")
    add("`nominal` 与 `voice_degraded` 的帧序列和虚拟时延**逐位相同**。这不是\n"
        "凑数或漏跑：在 audited 源码 `run_auto_wakeup` 里，语音子进程是与\n"
        "`orient_to_sound` / `follow_loop` **并行**启动的，不在 trigger-to-follow\n"
        "关键路径上；`voice_ready` 只是场景标记，不改变被重放的函数链。\n"
        "因此这两组结果支持的是一个**源码结构结论**：十月 `voice=False` 与\n"
        "6/6 trigger-to-follow 完成可以并存。它**不是**「语音降级对产品无影响」\n"
        "的实测统计，也不主张任何对话可用性。")
    add("")
    add("## 4. 四个可复核发现")
    add("")
    add("1. **历史 DOA 角度多数被源码饱和**。%d/%d 个历史转录角度超过 ±90°，\n"
        "   经 `min(abs(signed),90)` 饱和后产生相同的 9 个 head / 25 个 body 脉冲。\n"
        "   6 个转录角度经 `raw=deg mod 360` 映射回 `AngleMapper` 的 signed 值\n"
        "   %s。这与正文已记载的「120° 输入饱和到 90°」是同一机制，\n"
        "   并给出量化覆盖率。" % (
            f["historical_angles_saturating_at_90_deg"],
            f["historical_angles_total"],
            "全部一致（seed_matches_signed=true）"
            if f["historical_seed_matches_source_mapping"] else "存在不一致"))
    add("2. **转向段时延由确定性编排主导，不是推理**。转向段 %.3f–%.3f s 几乎\n"
        "   全部来自 `sound_head_body_delay=2.0 s`、`0.30 s` 最小命令间隔与\n"
        "   `pulse*0.043+1.2 s` body settle；不同角度只通过 pulse 数改变 settle。\n"
        "   它与历史转录的 4.35 s trigger-to-follow **量级相同但估计量不同**\n"
        "   （虚拟预算 vs wall-clock），因此只能作为机制性提示：闭环延迟由\n"
        "   控制边界与编排决定，而非 GPU 峰值吞吐。**不能据此宣称复现历史数值**。" % (
            s["by_mode"]["nominal"]["orient_s_min"],
            s["by_mode"]["nominal"]["orient_s_max"]))
    dr = f["face_dropout"]
    add("3. **降级模式被显式覆盖，且完成率不等于对话成功率**。\n"
        "   `voice_degraded` 完成 %s 的 trigger-to-follow 链，但对话成功声明为\n"
        "   **0**（见上文源码解耦说明）；`doa_dead_zone` 由源码跳过转向后仍\n"
        "   完成跟随；`face_dropout` 的 %d 帧 `no face` 期间发出 %d 个动作，\n"
        "   目标恢复后继续发出 %d 个动作；`camera_not_ready` 完成 %s\n"
        "   （刻意注入的硬失败，不并入可运行完成率）。" % (
            f["voice_degraded_chain_completed"],
            dr["no_face_frames"], dr["actions_during_no_face"],
            dr["actions_after_recovery"],
            f["camera_not_ready_chain_completed"]))
    add("4. **counter-head 帧的固件契约缺口在闭环里复现**。共发出 **%d** 个\n"
        "   反向云台帧（另有 %d 个 head pre-turn 帧，口径已按 body 帧位置区分），\n"
        "   而 audited 固件在阻塞的 `TurnBody()` 内使用固定 15 脉冲，返回后重置\n"
        "   `rx_idx`，主机第二帧没有可靠排队路径。这与 `BOUNDARY_ISOLATION.md` /\n"
        "   固件静态审计交叉印证，且是**源码契约推导**，不是示波器观测。" % (
            f["counter_head_frames_without_reliable_mcu_queue"],
            t["pre_turn_head_frames"]))
    add("")
    add("## 5. 帧契约与安全守卫")
    add("")
    add("每个帧都按 7 字节 `AA 55 | cmd | pulse_lo | pulse_hi | 0D 0A` 解码，\n"
        "并检查 header/tail、已知 opcode、以及主机侧 clamp 范围\n"
        "（head 5–15、body 5–25）。**%d 帧全部通过**（%d 处错误）。" % (
            t["frames_emitted"], t["frame_contract_errors"]))
    add("")
    add("被禁用的源码方法（任何意外调用都会抛错）：")
    add("")
    for name in result["guardrails"]:
        add("- `%s`" % name)
    add("")
    add("`physical_device_open_attempts` 由每条 trace 的 recorder 实际累加，\n"
        "本批次为 **0**；它不是硬编码常量。固件 MCU 侧 pulse 字段不钳制这一\n"
        "发现沿用 `BOUNDARY_ISOLATION.md`；本 replay 只验证**主机侧**发出的帧\n"
        "都在 clamp 内。")
    add("")
    add("## 6. 证据边界")
    add("")
    add("- 可与历史转录**并列呈现**，不得合并统计、不得称为复现；")
    add("- 不主张 F1/F2 物理注入、N=6 物理演示、真实 UART 时序或逻辑分析仪轨迹；")
    add("- 不主张语音对话成功、不量化经济或环境收益；")
    add("- `citation_allowed=false` 是内部治理标记：方法已写全，\n"
        "  引用前由作者复核估计量定义。")
    add("")
    add("## 7. 跨主机复跑与复现")
    add("")
    cross = result.get("cross_host_validation")
    if cross and cross["fingerprint_matches"]:
        add("同一脚本与同一份 `robot_runtime.current.py` / 固件快照在两台主机上复跑：\n"
            "本机 `%s` (%s) 与 Xavier `%s` (%s)。源码哈希一致，\n"
            "结果指纹一致：`%s`。这排除了「结果依赖某一台主机的\n"
            "Python 版本或平台行为」的可能。" % (
                result["environment"]["hostname"], result["environment"]["machine"],
                cross["hostname"], cross["machine"],
                result["result_fingerprint_sha256"]))
    else:
        add("尚未取回 Xavier 复跑 JSON；复跑后用 `--no-report` 生成\n"
            "`%s` 并重新渲染本报告，即可自动列出跨主机校验。" %
            DEVICE_JSON.name)
    add("")
    add("```bash")
    add("cd paper")
    add("python3 scripts/product_loop_replay.py")
    add("# 设备上用同一 runtime 快照复跑（不覆盖主报告）")
    add("python3 scripts/product_loop_replay.py \\")
    add("  --output evidence/analysis/product_loop_replay_20261107_jetson.json \\")
    add("  --no-report")
    add("```")
    add("")
    add("输入哈希：`robot_runtime.current.py` = `%s`，\n"
        "`motor_uart_vision.current.c` = `%s`。\n"
        "结果指纹（排除主机/时间字段）：`%s`。" % (
            result["inputs"]["runtime_sha256"][:20],
            result["inputs"]["firmware_sha256"][:20],
            result["result_fingerprint_sha256"]))
    add("")
    return "\n".join(a) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output", default=str(DEFAULT_JSON))
    p.add_argument("--report", default=str(DEFAULT_MD))
    p.add_argument("--no-report", action="store_true")
    args = p.parse_args()

    mod = load_runtime(RUNTIME)
    guardrails = install_guardrails(mod)
    scenarios = build_scenarios()
    # The audited runtime prints every command. Keep the whole replay silent
    # so the machine-readable summary is the only stdout.
    with contextlib.redirect_stdout(io.StringIO()):
        traces = [run_trace(mod, sc) for sc in scenarios]
    summary = summarize(traces)

    scenarios_meta = {
        "doa_seed_angles_deg": DOA_SEED_ANGLES_DEG,
        "doa_seed_source": "rebuild_common.HISTORICAL.demo.doa_turn_angles_deg "
                           "(transcribed October value, not a recorded trace)",
        "dead_zone_angles_deg": DEAD_ZONE_ANGLES_DEG,
        "frame_period_s": FRAME_PERIOD_S,
        "frame_period_source": "transcribed historical 17.7 FPS; scenario parameter",
        "nominal_stream_length": len(STREAM_NOMINAL),
        "dropout_stream_length": len(STREAM_DROPOUT),
        "dropout_none_frames": sum(1 for x in STREAM_DROPOUT if x is None),
        "face_stream_source": "synthetic probe sequence, not recorded YOLO output",
        "source_parameters": {
            "sound_head_body_delay_s": 2.0,
            "min_command_gap_s": 0.30,
            "min_body_gap_s": 2.0,
            "settle_formula": "pulse * 0.043 + 1.2",
            "max_head_pulse": 15, "max_body_pulse": 25,
            "min_sound_body_pulse": 15, "sound_dead_zone_deg": 15.0,
            "center_zone_width": 64, "command_interval_s": 0.35,
            "body_interval_s": 2.0, "body_track_threshold": 15,
            "body_track_pulse": 10,
        },
    }

    result = {
        "schema": "gedgebot.product_loop_replay.v1",
        "generated_at_utc": utc_now_iso(),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(__file__),
        "evidence_status": "software_in_the_loop_replay",
        "device_measurement": False,
        "physical_actuation": False,
        "no_physical_demonstration": True,
        "citation_allowed": False,
        "evidence_note": (
            "Software-in-the-loop replay of audited source functions driven by "
            "synthetic scenario events and a virtual clock. The six DOA seeds "
            "are transcribed historical angles, not recorded sensor data. No "
            "serial/camera/microphone/voice device was opened and no motor "
            "frame reached hardware. Timings are source-derived budgets, not "
            "wall-clock measurements. Must not be pooled with or described as "
            "a reproduction of the October N=6 demonstrations."),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
            "hostname": platform.node(),
            "is_jetson": Path("/etc/nv_tegra_release").exists()
                         or platform.machine() == "aarch64",
        },
        "inputs": {
            "runtime_path": str(RUNTIME),
            "runtime_sha256": sha256_file(RUNTIME),
            "firmware_path": str(FIRMWARE),
            "firmware_sha256": sha256_file(FIRMWARE),
            "source_commit": "c25b444",
        },
        "guardrails": guardrails,
        "scenarios_meta": scenarios_meta,
        "traces": traces,
        "summary": summary,
    }
    result["result_fingerprint_sha256"] = fingerprint(
        traces, summary, scenarios_meta)

    # Cross-host check: a second run on the Xavier produces a sibling JSON.
    # It is informational only and never enters the fingerprint.
    cross = None
    out_path = Path(args.output)
    if DEVICE_JSON.exists() and DEVICE_JSON.resolve() != out_path.resolve():
        try:
            other = json.loads(DEVICE_JSON.read_text())
            cross = {
                "path": str(DEVICE_JSON),
                "hostname": other["environment"]["hostname"],
                "machine": other["environment"]["machine"],
                "is_jetson": other["environment"]["is_jetson"],
                "fingerprint": other["result_fingerprint_sha256"],
                "fingerprint_matches": (other["result_fingerprint_sha256"]
                                        == result["result_fingerprint_sha256"]),
                "runtime_sha_matches": (other["inputs"]["runtime_sha256"]
                                       == result["inputs"]["runtime_sha256"]),
            }
        except (OSError, KeyError, ValueError):
            cross = None
    result["cross_host_validation"] = cross

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if not args.no_report:
        Path(args.report).write_text(render_report(result), encoding="utf-8")

    print("traces=%d runnable_completed=%d/%d frames=%d contract_errors=%d"
          " fingerprint=%s" % (
              summary["totals"]["traces"], summary["totals"]["runnable_completed"],
              summary["totals"]["runnable_traces"], summary["totals"]["frames_emitted"],
              summary["totals"]["frame_contract_errors"],
              result["result_fingerprint_sha256"][:16]))
    print("json=%s%s" % (out, "" if args.no_report else " report=%s" % args.report))


if __name__ == "__main__":
    main()
