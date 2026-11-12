#!/usr/bin/env python3
"""E8: full-runtime control-state replay of the audited robot control plane.

WHAT THIS IS
    An offline replay that *executes the real control methods* of
    ``evidence/robot_runtime.current.py`` (the read-only snapshot of
    root@192.168.1.130:/root/src/gedge_bot/RobotControl/robot_runtime.py,
    gedge_bot HEAD c25b444).  It drives the real ``main()`` CLI dispatcher
    across all six ``--mode`` values and runs the real ``run_auto_wakeup``
    state machine: voice prewarm, parallel camera/YOLO open, two 60 s
    readiness gates, wake-word trigger, sound orientation, Qt UI trigger,
    SIGUSR-gated voice start/stop, 120 s follow window and post-follow mic
    flush.  Peripherals are replaced by recording stubs; the module's
    ``time`` is a virtual clock.

    This extends E3, which replayed only one decision chain
    (orient_to_sound -> VisualFollower.update -> frame builders) and
    explicitly guarded the higher-level control methods. Here those methods
    are the subjects of the experiment.

WHAT THIS IS NOT
    - NOT a physical demonstration. Every real device-opening method in the
      loaded module (TermiosSerial.open, CameraFaceTracker.open/start,
      MonitorServer.start, VisionAPIClient.detect, VoiceAssistantProcess.*,
      subprocess.Popen) is replaced by a guard that raises and increments a
      counter. No serial port, camera, microphone, HTTP service or voice
      subprocess is opened; no motor frame reaches hardware.
    - NOT a wall-clock measurement. All timings are virtual-clock budgets
      derived from sleeps already present in the audited source.
    - NOT a recovered October trace. DOA seeds are transcribed historical
      turn angles; face-position streams are synthetic probes.
    - NOT a conversational success rate. The voice subsystem is a recorded
      stub; the October demonstrations ran with voice=False.

Every emitted frame is decoded against the seven-byte contract and the
host-side clamps. All numbers are computed, never hand-entered.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import platform
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
DEFAULT_JSON = EVID / "analysis" / "full_runtime_replay_20261107.json"
DEFAULT_MD = EVID / "FULL_RUNTIME_REPLAY.md"

KNOWN_CMDS = {0x01: "body_turn_left", 0x02: "body_turn_right",
              0x09: "vision_track_head"}
DOA_SEEDS = list(HISTORICAL["demo"]["doa_turn_angles_deg"])

# Synthetic face-position probe streams. None = detector found no target.
STREAM_NOMINAL = [520] * 8 + [420] * 6 + [320] * 6 + [220] * 6 + [320] * 6
STREAM_DROPOUT = [520] * 6 + [None] * 8 + [520] * 8 + [320] * 6


class EndOfScenario(KeyboardInterrupt):
    """Terminates an infinite wake loop; main() catches KeyboardInterrupt."""


class EndOfFollowWindow(Exception):
    """Finite synthetic face stream -> normal return from the real follow_loop."""


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


class Recorder:
    """In-memory sink for control events and motor frames."""

    def __init__(self, clock):
        self.clock = clock
        self.events = []
        self.frames = []
        self.logical_device_opens = []
        self.physical_device_open_attempts = 0
        self.http_calls = []

    def event(self, component, action, detail=None):
        self.events.append({
            "step": len(self.events),
            "t": round(self.clock.time(), 6),
            "component": component,
            "action": action,
            "detail": detail or {},
        })

    def frame(self, data):
        b = bytes(data)
        pulse = b[3] | (b[4] << 8) if len(b) >= 5 else None
        self.frames.append({
            "t": round(self.clock.time(), 6),
            "hex": b.hex(" ").upper(),
            "bytes": list(b),
            "len": len(b),
            "cmd": b[2] if len(b) >= 3 else None,
            "cmd_name": KNOWN_CMDS.get(b[2]) if len(b) >= 3 else None,
            "pulse_le": pulse,
            "direction_bit": bool(pulse & 0x8000) if pulse is not None else None,
            "pulse_magnitude": (pulse & 0x7FFF) if pulse is not None else None,
        })


def guard_factory(rec, name):
    def _guard(*_a, **_k):
        rec.physical_device_open_attempts += 1
        raise RuntimeError("E8 replay forbids physical access: %s" % name)
    return _guard


class FakeResponse:
    status = 200

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class FakeSelect:
    @staticmethod
    def select(_r, _w, _x, _timeout):
        return ([sys.stdin], [], [])


class FakeSerial:
    """Duck-typed replacement for TermiosSerial. No os.open / os.write."""

    def __init__(self, rec, clock, role, port, baud):
        self.rec, self.clock = rec, clock
        self.role, self.port, self.baud = role, port, baud
        self.fd = None

    def open(self):
        self.rec.event("serial", "logical_open",
                       {"role": self.role, "port": self.port, "baud": self.baud})
        self.rec.logical_device_opens.append("%s:%s" % (self.role, self.port))

    def write(self, data):
        b = bytes(data)
        if self.role == "motor":
            self.rec.frame(b)
        else:
            self.rec.event("mic", "heartbeat_write",
                           {"bytes": b.hex(" ").upper()})

    def read_available(self, timeout=0.0, max_bytes=4096):
        self.clock.advance(timeout)
        return b""

    def close(self):
        self.rec.event("serial", "logical_close", {"role": self.role})


class ScriptedMic:
    """In-memory DOA source. Mirrors the real MicArrayReader API surface."""

    def __init__(self, rec, clock, mod, angles, cycles, no_angle=False,
                 poll_emits=0):
        self.rec, self.clock, self.mod = rec, clock, mod
        self.triggers = ([int(a) % 360 for a in angles][:cycles] if angles else [])
        if angles and cycles > len(angles):
            self.triggers = [int(angles[i % len(angles)]) % 360
                             for i in range(cycles)]
        self.idx = 0
        self.no_angle = no_angle
        self.poll_emits = poll_emits

    def open(self):
        self.rec.event("mic", "logical_open", {})
        self.rec.logical_device_opens.append("mic:scripted")

    def close(self):
        self.rec.event("mic", "logical_close", {})

    def _make(self, angle):
        self.rec.event("mic", "doa_angle", {"angle": angle})
        return self.mod.MicAngleEvent(angle=angle, timestamp=self.clock.time())

    def poll(self, timeout=0.0):
        self.clock.advance(timeout)
        if self.poll_emits > 0 and self.idx < len(self.triggers):
            angle = self.triggers[self.idx]
            self.idx += 1
            self.poll_emits -= 1
            return [self._make(angle)]
        return []

    def wait_for_next_angle(self, timeout=None):
        self.clock.advance(0.05 if timeout is None else timeout)
        if self.idx >= len(self.triggers):
            self.rec.event("control", "trigger_stream_exhausted",
                           {"consumed": self.idx})
            raise EndOfScenario("wake trigger stream exhausted")
        angle = self.triggers[self.idx]
        self.idx += 1
        self.rec.event("mic", "doa_trigger", {"angle": angle, "index": self.idx})
        return self._make(angle)

    def wait_for_angle(self, timeout):
        self.clock.advance(timeout)
        return None if self.no_angle else self.latest_angle()

    def latest_angle(self, max_age=None):
        if self.no_angle:
            self.rec.event("mic", "no_recent_angle", {"max_age": max_age})
            return None
        if self.idx >= len(self.triggers):
            self.rec.event("control", "trigger_stream_exhausted",
                           {"consumed": self.idx})
            raise EndOfScenario("wake trigger stream exhausted")
        angle = self.triggers[self.idx]
        self.idx += 1
        return self._make(angle)


class FakeVoice:
    """Records the SIGUSR1/SIGUSR2 prewarm gate without creating a process."""

    def __init__(self, rec, clock, enabled, use_api, ready=True):
        self.rec, self.clock = rec, clock
        self.enabled, self.use_api, self.ready = enabled, use_api, ready

    def prewarm(self):
        if not self.enabled or self.use_api:
            self.rec.event("voice", "prewarm_skipped",
                           {"enabled": self.enabled, "use_api": self.use_api})
            return
        self.rec.event("voice", "prewarm", {"gate": "idle, awaiting SIGUSR1"})

    def wait_until_ready(self, timeout=60.0):
        ok = True if (not self.enabled or self.use_api) else self.ready
        self.rec.event("voice", "wait_until_ready",
                       {"ready": ok, "timeout": timeout})
        return ok

    def start(self):
        if not self.enabled:
            self.rec.event("voice", "start_skipped", {"enabled": False})
            return
        self.rec.event("voice", "start",
                       {"signal": "api_start" if self.use_api else "SIGUSR1"})

    def stop(self):
        if not self.enabled:
            self.rec.event("voice", "stop_skipped", {"enabled": False})
            return
        self.rec.event("voice", "stop",
                       {"signal": "api_stop" if self.use_api else "SIGUSR2"})

    def shutdown(self):
        self.rec.event("voice", "shutdown", {})


class FakeCamera:
    """In-memory camera source with a finite face stream per follow window."""

    def __init__(self, rec, clock, face_stream, ready=True, window=24):
        self.rec, self.clock = rec, clock
        self.frame_lock = threading.RLock()  # reentrant: latest_status uses it too
        self._state = "waiting_wakeup"
        self._stream = list(face_stream)
        self.ready = ready
        self.window = window
        self._count = 0
        self.none_face_samples = 0

    def open(self):
        with self.frame_lock:
            self._state = "ready" if self.ready else "loading_detector"
        self.rec.event("camera", "logical_open", {"state": self._state})
        self.rec.logical_device_opens.append("camera:scripted")

    @property
    def latest_status(self):
        with self.frame_lock:
            state = self._state
        return {"state": state, "updated_at": self.clock.time(), "fps": 0.0}

    @property
    def latest_face_x(self):
        if self._count >= self.window:
            raise EndOfFollowWindow()
        value = self._stream[self._count % len(self._stream)]
        self._count += 1
        if value is None:
            self.none_face_samples += 1
        return value

    def reset_window(self):
        self._count = 0

    def close(self):
        self.rec.event("camera", "logical_close", {})


class FakeMonitor:
    def __init__(self, rec):
        self.rec = rec
        self.httpd = None

    def start(self):
        self.httpd = object()
        self.rec.event("monitor", "logical_start",
                       {"endpoints": ["/", "/status", "/stream.mjpg"]})

    def stop(self):
        self.rec.event("monitor", "stop", {})


def frame_contract_errors(frame):
    errs = []
    b = frame["bytes"]
    if frame["len"] != 7:
        return ["frame length %d != 7" % frame["len"]]
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


def make_wrapper(rec, name, real, runtime, camera=None):
    """Record enter/exit and frame slices, but always call the real method."""
    def wrapped(*a, **k):
        detail = {"frames_before": len(rec.frames)}
        if a:
            detail["args"] = [str(x) for x in a]
        if k:
            detail["kwargs"] = {kk: str(vv) for kk, vv in k.items()}
        rec.event("control", name + "_enter", detail)
        if name == "follow_loop" and camera is not None:
            camera.reset_window()
        try:
            out = real(runtime, *a, **k)
        except EndOfFollowWindow:
            # Only the follow_loop wrapper may convert a finite synthetic face
            # stream into a normal return. Any other wrapper must not swallow
            # it, or the real state machine would be truncated silently.
            if name != "follow_loop":
                raise
            rec.event("control", "follow_window_exhausted",
                      {"face_samples": camera._count if camera else None})
            out = None
        if name == "_wait_camera_ready":
            rec.event("camera", "readiness_gate", {"ready": bool(out)})
        rec.event("control", name + "_exit",
                  {"frames_after": len(rec.frames)})
        return out
    return wrapped


WRAPPED_METHODS = [
    "run_mic", "run_motor_test", "run_sound_once", "run_follow",
    "run_stdin_wakeup", "run_auto_wakeup", "follow_loop",
    "flush_mic_array", "open_camera_async", "start_monitor_if_enabled",
    "orient_to_sound", "trigger_qt_voice_dialog", "_wait_camera_ready",
]


def configure(runtime, args, mod, ctx):
    """Install recording stubs while leaving all control logic real."""
    rec, clock, cfg = ctx["rec"], ctx["clock"], ctx["cfg"]
    mod_ref = mod
    runtime.args = args
    runtime.mic = ScriptedMic(
        rec, clock, mod_ref, cfg.get("angles", []), cfg.get("cycles", 0),
        no_angle=cfg.get("no_angle", False),
        poll_emits=cfg.get("poll_emits", 0))
    runtime.motor = mod_ref.MotorController(
        args.motor_port, args.motor_baud, args.enable_motors,
        args.max_head_pulse, args.max_body_pulse, verbose=False)
    runtime.motor.serial = FakeSerial(rec, clock, "motor",
                                      args.motor_port, args.motor_baud)
    runtime.angle_mapper = mod_ref.AngleMapper(
        args.mic_angle_offset, args.mic_invert, args.sound_dead_zone_deg,
        args.max_head_pulse, args.max_body_pulse, args.min_sound_body_pulse)
    camera = FakeCamera(rec, clock, cfg.get("face_stream", STREAM_NOMINAL),
                        ready=cfg.get("camera_ready", True),
                        window=cfg.get("window", 24))
    runtime.camera = camera
    ctx["camera"] = camera
    runtime.camera_open_thread = None
    runtime.monitor = FakeMonitor(rec) if args.monitor else None
    runtime.qt_voice_ui_url = args.qt_voice_ui_url.rstrip("/")
    runtime.voice_assistant = FakeVoice(
        rec, clock, args.voice_assistant, args.use_api_voice,
        ready=cfg.get("voice_ready", True))
    runtime.visual_follower = mod_ref.VisualFollower(
        args.center_zone_width, args.command_interval, args.body_interval,
        args.body_track_threshold, args.body_track_pulse)

    for name in WRAPPED_METHODS:
        if hasattr(mod_ref.RobotRuntime, name):
            real = getattr(mod_ref.RobotRuntime, name)
            setattr(runtime, name,
                    make_wrapper(rec, name, real, runtime, camera))


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("gedge_runtime_full_replay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def runtime_class(mod, ctx):
    class ReplayRobotRuntime(mod.RobotRuntime):
        def __init__(self, args):
            configure(self, args, mod, ctx)
    return ReplayRobotRuntime


GUARDED = [
    ("TermiosSerial", "open"),
    ("CameraFaceTracker", "open"), ("CameraFaceTracker", "start"),
    ("MonitorServer", "start"), ("VisionAPIClient", "detect"),
    ("VoiceAssistantProcess", "prewarm"),
    ("VoiceAssistantProcess", "wait_until_ready"),
    ("VoiceAssistantProcess", "start"), ("VoiceAssistantProcess", "stop"),
    ("VoiceAssistantProcess", "shutdown"),
    ("VoiceAssistantProcess", "_start_log_reader"),
]


def counter_head_frames(rec, events):
    """Count 0x09 frames emitted after a body frame inside orient_to_sound."""
    total = 0
    starts = [e for e in events if e["action"] == "orient_to_sound_enter"]
    ends = [e for e in events if e["action"] == "orient_to_sound_exit"]
    for start, end in zip(starts, ends):
        lo = start["detail"]["frames_before"]
        hi = end["detail"]["frames_after"]
        seg_frames = rec.frames[lo:hi]
        body_idx = next((i for i, f in enumerate(seg_frames)
                         if f["cmd"] in (0x01, 0x02)), None)
        if body_idx is not None:
            total += sum(1 for f in seg_frames[body_idx + 1:]
                         if f["cmd"] == 0x09)
    return total


def branch_check(rec, stdout, camera, branch):
    actions = [(e["component"], e["action"], e["detail"]) for e in rec.events]
    if branch == "nominal":
        return True
    if branch == "degraded_readiness_voice":
        return any(a == "wait_until_ready" and d.get("ready") is False
                   for _, a, d in actions)
    if branch == "degraded_readiness_camera":
        return any(c == "camera" and a == "readiness_gate"
                   and d.get("ready") is False for c, a, d in actions)
    if branch == "api_latency_warning":
        return "WARNING: vision is going through HTTP API" in stdout
    if branch == "dry_run_guard":
        return len(rec.frames) == 0 and "dry-run" in stdout
    if branch == "dead_zone":
        return len(rec.frames) == 0 and "within dead zone" in stdout
    if branch == "detector_dropout":
        return camera.none_face_samples > 0
    if branch == "missing_sensor":
        return "no sound angle available" in stdout
    if branch == "optional_motion":
        return "skipping head pre-turn" in stdout
    if branch == "backend_selection":
        return any(a in ("prewarm_skipped", "start_skipped")
                   for _, a, _ in actions)
    return False


def run_scenario(mod, cfg, source_sha):
    clock = VirtualClock()
    rec = Recorder(clock)
    ctx = {"rec": rec, "clock": clock, "cfg": cfg}
    camera_holder = {}

    def fake_urlopen(req, timeout=None, **_kw):
        url = getattr(req, "full_url", None) or str(req)
        rec.http_calls.append({"url": url, "timeout": timeout})
        rec.event("http", "urlopen_stub", {"url": url})
        return FakeResponse()

    saved = {
        "time": mod.time,
        "select": mod.select,
        "RobotRuntime": mod.RobotRuntime,
        "argv": sys.argv,
        "stdin": sys.stdin,
        "urlopen": mod.urllib.request.urlopen,
        "Popen": mod.subprocess.Popen,
    }
    guarded_saved = []
    buf = io.StringIO()
    error = None
    stdout = ""

    try:
        mod.time = clock
        mod.select = FakeSelect()
        mod.RobotRuntime = runtime_class(mod, ctx)
        sys.argv = (["robot_runtime.py", "--mode", cfg["mode"]]
                    + list(cfg.get("cli", [])))
        sys.stdin = io.StringIO("".join(cfg.get("stdin_lines", [])))
        mod.urllib.request.urlopen = fake_urlopen
        mod.subprocess.Popen = guard_factory(rec, "subprocess.Popen")
        for cls_name, method in GUARDED:
            cls = getattr(mod, cls_name)
            if hasattr(cls, method):
                guarded_saved.append((cls, method, getattr(cls, method)))
                setattr(cls, method,
                        guard_factory(rec, "%s.%s" % (cls_name, method)))
        with contextlib.redirect_stdout(buf):
            mod.main()
    except EndOfScenario as exc:
        # main() normally catches KeyboardInterrupt. If one escapes, the
        # scenario still ended at a controlled sentinel, not at an error.
        rec.event("control", "scenario_ended_by_sentinel", {"reason": str(exc)})
    except Exception as exc:  # recorded honestly, never hidden
        error = "%s: %s" % (type(exc).__name__, exc)
    finally:
        stdout = buf.getvalue()
        for cls, method, original in guarded_saved:
            setattr(cls, method, original)
        mod.time = saved["time"]
        mod.select = saved["select"]
        mod.RobotRuntime = saved["RobotRuntime"]
        sys.argv = saved["argv"]
        sys.stdin = saved["stdin"]
        mod.urllib.request.urlopen = saved["urlopen"]
        mod.subprocess.Popen = saved["Popen"]

    contract_errors = []
    for i, f in enumerate(rec.frames):
        for err in frame_contract_errors(f):
            contract_errors.append({"frame_index": i, "error": err})

    events = rec.events
    cam = None
    # The FakeCamera instance is reachable through the last configured runtime.
    # Recover it from the recorder's logical opens is unnecessary; use ctx.
    for obj in [ctx.get("camera")]:
        if obj is not None:
            cam = obj
    observed = branch_check(rec, stdout, cam or SimpleNamespace(none_face_samples=0),
                            cfg.get("branch", "nominal"))

    mode_enters = [e for e in events if e["action"] == "run_%s_enter"
                   % cfg["mode"].replace("-", "_")]
    completed = bool(error is None and not contract_errors
                     and rec.physical_device_open_attempts == 0
                     and mode_enters)

    follow_enters = [e for e in events if e["action"] == "follow_loop_enter"]
    prewarm_idx = next((e["step"] for e in events
                        if e["component"] == "voice"
                        and e["action"] in ("prewarm", "prewarm_skipped")), None)
    first_trigger_idx = next((e["step"] for e in events
                              if e["action"] == "doa_trigger"), None)
    ready_idxs = [e["step"] for e in events
                  if e["action"] in ("wait_until_ready", "readiness_gate")]
    prewarm_before = (prewarm_idx is not None and first_trigger_idx is not None
                      and prewarm_idx < first_trigger_idx)
    gates_before = (bool(ready_idxs) and first_trigger_idx is not None
                    and max(ready_idxs) < first_trigger_idx)

    voice_starts = [e["step"] for e in events
                    if e["component"] == "voice" and e["action"] == "start"]
    voice_stops = [e["step"] for e in events
                   if e["component"] == "voice" and e["action"] == "stop"]
    gating_ordered = (len(voice_starts) == len(voice_stops)
                      and all(s < t for s, t in zip(voice_starts, voice_stops))
                      and all(voice_stops[i] < voice_starts[i + 1]
                              for i in range(len(voice_starts) - 1)))

    return {
        "scenario_id": cfg["id"],
        "mode": cfg["mode"],
        "cli_suffix": list(cfg.get("cli", [])),
        "branch": cfg.get("branch", "nominal"),
        "note": cfg.get("note", ""),
        "completed": completed,
        "error": error,
        "branch_observed": observed,
        "virtual_duration_s": round(clock.time() - 10000.0, 6),
        "virtual_sleep_calls": clock.sleep_calls,
        "frames": rec.frames,
        "frames_count": len(rec.frames),
        "contract_errors": contract_errors,
        "physical_device_open_attempts": rec.physical_device_open_attempts,
        "logical_device_opens": rec.logical_device_opens,
        "control_events": events,
        "http_calls": rec.http_calls,
        "stdout_markers": sorted({m for m in (
            "WARNING: vision is going through HTTP API", "dry-run",
            "within dead zone", "no sound angle available",
            "skipping head pre-turn", "degraded readiness",
            "all subsystems ready") if m in stdout}),
        "derived": {
            "follow_windows": len(follow_enters),
            "counter_head_frames": counter_head_frames(rec, events),
            "prewarm_before_first_trigger": prewarm_before,
            "readiness_gates_before_first_trigger": gates_before,
            "voice_gating_ordered": gating_ordered,
            "qt_ui_triggers": sum(1 for e in events
                                  if e["action"] == "trigger_qt_voice_dialog_enter"),
            "mic_flushes": sum(1 for e in events
                               if e["action"] == "flush_mic_array_enter"),
            "interrupt_on_sound_enabled": sum(
                1 for e in follow_enters
                if e["detail"].get("kwargs", {}).get("interrupt_on_sound") == "True"),
            "face_none_samples": cam.none_face_samples if cam else 0,
        },
    }


def scenarios():
    return [
        {"id": "S01_mic", "mode": "mic", "cli": ["--duration", "0.20"],
         "angles": [42, 137, 275, 8], "cycles": 4, "poll_emits": 4,
         "branch": "nominal", "note": "real run_mic loop with a finite DOA stream"},
        {"id": "S02_motor_test", "mode": "motor-test",
         "cli": ["--enable-motors", "--test-part", "body",
                 "--test-direction", "right", "--test-pulse", "18"],
         "angles": [], "cycles": 0, "branch": "nominal",
         "note": "manual motor-test path and real host clamp"},
        {"id": "S03_sound_once", "mode": "sound-once",
         "cli": ["--enable-motors"], "angles": [90], "cycles": 1,
         "branch": "nominal", "note": "single DOA trigger -> orient_to_sound"},
        {"id": "S04_follow", "mode": "follow",
         "cli": ["--enable-motors", "--duration", "0.20"],
         "angles": [], "cycles": 0, "branch": "nominal",
         "note": "real follow_loop with a synthetic face stream"},
        {"id": "S05_stdin_wakeup", "mode": "stdin-wakeup",
         "cli": ["--enable-motors", "--duration", "0.10"],
         "angles": [-53], "cycles": 1, "stdin_lines": ["hey\n", "q\n"],
         "branch": "nominal", "note": "operator-triggered wakeup path"},
        {"id": "S06_auto_wakeup", "mode": "auto-wakeup",
         "cli": ["--enable-motors"], "angles": DOA_SEEDS[:3], "cycles": 3,
         "branch": "nominal", "note": "three full product cycles of the real state machine"},
        {"id": "S07_voice_not_ready", "mode": "auto-wakeup",
         "cli": ["--enable-motors"], "angles": [90, -53], "cycles": 2,
         "voice_ready": False, "branch": "degraded_readiness_voice",
         "note": "voice readiness gate returns False; source proceeds degraded"},
        {"id": "S08_camera_not_ready", "mode": "auto-wakeup",
         "cli": ["--enable-motors"], "angles": [90, -53], "cycles": 2,
         "camera_ready": False, "branch": "degraded_readiness_camera",
         "note": "camera readiness gate returns False; source proceeds degraded"},
        {"id": "S09_api_vision", "mode": "auto-wakeup",
         "cli": ["--enable-motors", "--use-api-vision", "--no-voice-assistant"],
         "angles": [104], "cycles": 1, "branch": "api_latency_warning",
         "note": "source prints the API-vision latency warning; voice disabled"},
        {"id": "S10_dry_run", "mode": "auto-wakeup", "cli": [],
         "angles": [90], "cycles": 1, "branch": "dry_run_guard",
         "note": "default enable_motors=False -> zero serial writes"},
        {"id": "S11_dead_zone", "mode": "sound-once",
         "cli": ["--enable-motors"], "angles": [7], "cycles": 1,
         "branch": "dead_zone", "note": "7 deg is inside the 15 deg dead zone"},
        {"id": "S12_face_dropout", "mode": "follow",
         "cli": ["--enable-motors", "--duration", "0.20"],
         "angles": [], "cycles": 0, "face_stream": STREAM_DROPOUT,
         "branch": "detector_dropout", "note": "synthetic detector dropout stream"},
        {"id": "S13_no_angle", "mode": "stdin-wakeup",
         "cli": ["--enable-motors", "--duration", "0.10"],
         "angles": [], "cycles": 0, "no_angle": True,
         "stdin_lines": ["hey\n", "q\n"], "branch": "missing_sensor",
         "note": "no recent DOA angle -> source skips sound orientation"},
        {"id": "S14_skip_sound_head", "mode": "auto-wakeup",
         "cli": ["--enable-motors", "--skip-sound-head"], "angles": [110],
         "cycles": 1, "branch": "optional_motion",
         "note": "head pre-turn and counter-head callback are skipped"},
    ]


def build_result(path, firmware_path):
    mod = load_runtime(path)
    traces = []
    for cfg in scenarios():
        # Expose the FakeCamera to branch_check by injecting a post-hoc hook.
        trace = run_scenario(mod, cfg, sha256_file(path))
        traces.append(trace)

    state_pairs = sorted({(e["component"], e["action"])
                          for t in traces for e in t["control_events"]})
    modes_covered = sorted({t["mode"] for t in traces if t["completed"]})
    branches_observed = sorted({t["branch"] for t in traces
                                if t["branch"] != "nominal" and t["branch_observed"]})
    failed = [{"id": t["scenario_id"], "error": t["error"]}
              for t in traces if not t["completed"]]

    s08 = next((t for t in traces
                if t["scenario_id"] == "S08_camera_not_ready"), None)
    camera_gate_false = bool(s08) and any(
        e["component"] == "camera" and e["action"] == "readiness_gate"
        and e["detail"].get("ready") is False for e in s08["control_events"])
    follows_anyway = bool(s08) and any(
        e["action"] == "follow_loop_enter" for e in s08["control_events"])

    summary = {
        "scenarios_total": len(traces),
        "scenarios_completed": sum(1 for t in traces if t["completed"]),
        "modes_covered": modes_covered,
        "modes_total": 6,
        "frames_total": sum(t["frames_count"] for t in traces),
        "contract_errors_total": sum(len(t["contract_errors"]) for t in traces),
        "physical_device_open_attempts_total": sum(
            t["physical_device_open_attempts"] for t in traces),
        "logical_device_opens_total": sum(len(t["logical_device_opens"])
                                          for t in traces),
        "follow_windows_total": sum(t["derived"]["follow_windows"] for t in traces),
        "auto_wakeup_cycles_total": sum(
            t["derived"]["follow_windows"] for t in traces
            if t["mode"] == "auto-wakeup"),
        "counter_head_frames_total": sum(t["derived"]["counter_head_frames"]
                                         for t in traces),
        "distinct_state_transitions": len(state_pairs),
        "state_transition_pairs": ["%s.%s" % p for p in state_pairs],
        "degraded_branches_observed": branches_observed,
        "interrupt_on_sound_enabled_total": sum(
            t["derived"]["interrupt_on_sound_enabled"] for t in traces),
        "prewarm_before_first_trigger_all_auto": all(
            t["derived"]["prewarm_before_first_trigger"] for t in traces
            if t["mode"] == "auto-wakeup"),
        "readiness_gates_before_first_trigger_all_auto": all(
            t["derived"]["readiness_gates_before_first_trigger"] for t in traces
            if t["mode"] == "auto-wakeup"),
        "voice_gating_ordered_all_auto": all(
            t["derived"]["voice_gating_ordered"] for t in traces
            if t["mode"] == "auto-wakeup"),
        "camera_readiness_gate_false_still_enters_follow_loop": bool(
            camera_gate_false and follows_anyway),
        "failed_scenarios": failed,
    }

    cross_checks = [
        {"name": "six_cli_modes_completed",
         "observed": len(modes_covered), "expected": 6},
        {"name": "all_scenarios_completed",
         "observed": summary["scenarios_completed"], "expected": len(traces)},
        {"name": "no_physical_device_open",
         "observed": summary["physical_device_open_attempts_total"], "expected": 0},
        {"name": "all_frames_pass_contract",
         "observed": summary["contract_errors_total"], "expected": 0},
        {"name": "sound_interrupt_never_enabled_matches_E7",
         "observed": summary["interrupt_on_sound_enabled_total"], "expected": 0},
        {"name": "prewarm_precedes_first_trigger",
         "observed": summary["prewarm_before_first_trigger_all_auto"],
         "expected": True},
        {"name": "readiness_gates_precede_first_trigger",
         "observed": summary["readiness_gates_before_first_trigger_all_auto"],
         "expected": True},
        {"name": "voice_gating_ordered",
         "observed": summary["voice_gating_ordered_all_auto"], "expected": True},
        {"name": "degraded_branches_observed",
         "observed": len(branches_observed), "expected": 8},
        {"name": "dry_run_writes_zero_frames",
         "observed": next((t["frames_count"] for t in traces
                           if t["scenario_id"] == "S10_dry_run"), None),
         "expected": 0},
    ]
    for c in cross_checks:
        c["pass"] = c["observed"] == c["expected"]

    canonical = {
        "summary": {k: v for k, v in summary.items()
                    if k != "state_transition_pairs"},
        "cross_checks": [{k: v for k, v in c.items()} for c in cross_checks],
        "per_scenario": [{
            "id": t["scenario_id"], "mode": t["mode"], "branch": t["branch"],
            "completed": t["completed"], "branch_observed": t["branch_observed"],
            "frames_count": t["frames_count"],
            "contract_errors": t["contract_errors"],
            "physical_device_open_attempts": t["physical_device_open_attempts"],
            "virtual_duration_s": t["virtual_duration_s"],
            "derived": t["derived"],
            "stdout_markers": t["stdout_markers"],
        } for t in traces],
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                   default=str).encode()).hexdigest()

    return {
        "schema": "gedgebot.full_runtime_replay.v1",
        "generated_at_utc": utc_now_iso(),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(__file__),
        "evidence_status": "full_runtime_software_in_the_loop_replay",
        "device_measurement": False,
        "physical_actuation": False,
        "no_physical_demonstration": True,
        "citation_allowed": False,
        "evidence_note": (
            "Executes the real main() dispatcher and real run_* / "
            "run_auto_wakeup control methods from the audited source, with "
            "all peripherals replaced by recording stubs and a virtual clock. "
            "DOA seeds are transcribed historical turn angles; face streams "
            "are synthetic probes. No serial/camera/microphone/voice process "
            "or HTTP service was opened, and no motor frame reached hardware. "
            "Timings are source-derived virtual budgets, not wall-clock "
            "measurements. Not a physical demonstration, not a dialogue "
            "success rate, and not a reproduction of the October N=6 runs."),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
            "hostname": platform.node(),
            "is_jetson": Path("/etc/nv_tegra_release").exists()
                         or platform.machine() == "aarch64",
        },
        "inputs": {
            "runtime_path": str(path),
            "runtime_sha256": sha256_file(path),
            "firmware_path": str(firmware_path),
            "firmware_sha256": sha256_file(firmware_path),
            "remote_path": "/root/src/gedge_bot/RobotControl/robot_runtime.py",
            "source_commit": "c25b444",
            "doa_seed_angles_deg": DOA_SEEDS,
            "face_stream_nominal_len": len(STREAM_NOMINAL),
            "face_stream_dropout_len": len(STREAM_DROPOUT),
            "face_window_frames": 24,
        },
        "guardrails": {
            "guarded_source_methods": ["%s.%s" % (c, m) for c, m in GUARDED]
            + ["subprocess.Popen"],
            "wrapped_real_control_methods": WRAPPED_METHODS,
            "virtual_clock": "mod.time replaced; only sleeps present in source advance it",
            "network": "urllib.request.urlopen replaced by an in-memory 200 response",
            "select": "select.select replaced by an always-readable stub",
            "stdin": "io.StringIO with scripted operator lines",
        },
        "summary": summary,
        "cross_checks": cross_checks,
        "traces": traces,
        "fingerprint_sha256": fingerprint,
    }


def render_markdown(r, md_path):
    s = r["summary"]
    lines = [
        "# E8：整体机器人控制状态机 replay（Q3 扩展）",
        "",
        "自动生成于 `%s`，由 `scripts/full_runtime_replay.py` 从 `%s` 渲染。"
        % (r["generated_at_utc"], r["inputs"]["runtime_path"]),
        "**不要手改本文件**；改脚本后重跑即可。",
        "",
        "## 1. 这是什么 / 不是什么",
        "",
        "**是**：加载 audited 源码 `evidence/robot_runtime.current.py`",
        "（gedge_bot HEAD `c25b444`，SHA-256 `%s`），" % r["inputs"]["runtime_sha256"],
        "执行**真实的** `main()` CLI 分发与 6 个 `run_*` 控制方法，重点是",
        "`run_auto_wakeup`：语音子进程预热 → 相机/YOLO 并行异步打开 → 两个 60 s",
        "就绪门 → 声源触发 → `orient_to_sound` → Qt UI 触发 → SIGUSR1 语音启动 →",
        "120 s 跟随窗口 → SIGUSR2 语音停止 → 0.5 s 麦克风清空 → 回到等待。",
        "麦克风、相机、串口、监控服务、语音子进程全部替换为**记录桩**；",
        "模块内 `time` 换成虚拟时钟。",
        "",
        "**不是**：",
        "",
        "- **不是物理演示**。源码中真实打开设备的方法",
        "  （`TermiosSerial.open`、`CameraFaceTracker.open/start`、",
        "  `MonitorServer.start`、`VisionAPIClient.detect`、",
        "  `VoiceAssistantProcess.*`、`subprocess.Popen`）被换成抛错守卫；",
        "  `physical_device_open_attempts` 由 recorder 实测累加。",
        "- **不是 wall-clock 测量**。所有时间都是源码里 `sleep` 推出的虚拟预算。",
        "- **不是恢复的十月 trace**。6 个 DOA 种子是历史转录角度，face 流是合成探测序列。",
        "- **不是对话成功率**。语音子系统是记录桩，历史十月 `voice=False`。",
        "",
        "证据信封：`evidence_status=%s`，`device_measurement=%s`，"
        "`physical_actuation=%s`，`citation_allowed=%s`。" % (
            r["evidence_status"], r["device_measurement"],
            r["physical_actuation"], r["citation_allowed"]),
        "",
        "## 2. 场景设计",
        "",
        "| 场景 | 模式 | 注入条件 | 完成 | 分支观测 | 帧数 | 契约错误 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in r["traces"]:
        lines.append("| `%s` | `%s` | %s | %s | %s | %d | %d |" % (
            t["scenario_id"], t["mode"], t["note"],
            "yes" if t["completed"] else "no",
            "yes" if t["branch_observed"] else "no",
            t["frames_count"], len(t["contract_errors"])))
    lines += [
        "",
        "默认 face 流长度 %d，其中 dropout 流 %d；每个跟随窗口最多 %d 个 face 样本。"
        % (r["inputs"]["face_stream_nominal_len"],
           r["inputs"]["face_stream_dropout_len"],
           r["inputs"]["face_window_frames"]),
        "",
        "## 3. 结果",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        "| 场景完成 | %d/%d |" % (s["scenarios_completed"], s["scenarios_total"]),
        "| CLI 模式覆盖 | %d/%d（%s） |" % (
            len(s["modes_covered"]), s["modes_total"],
            ", ".join("`%s`" % m for m in s["modes_covered"])),
        "| 完整自动唤醒周期 / 跟随窗口 | %d / %d |" % (
            s["auto_wakeup_cycles_total"], s["follow_windows_total"]),
        "| 七字节帧 / 契约错误 | %d / %d |" % (
            s["frames_total"], s["contract_errors_total"]),
        "| 物理设备打开尝试 | %d |" % s["physical_device_open_attempts_total"],
        "| 逻辑（桩）设备打开 | %d |" % s["logical_device_opens_total"],
        "| 不同状态迁移 `(component, action)` | %d |" % s["distinct_state_transitions"],
        "| counter-head 帧 | %d |" % s["counter_head_frames_total"],
        "| `interrupt_on_sound=True` 调用 | %d |" % s["interrupt_on_sound_enabled_total"],
        "| 降级分支被实际触发 | %s |" % (
            ", ".join("`%s`" % b for b in s["degraded_branches_observed"]) or "none"),
        "",
        "结构校验：所有 `auto-wakeup` 场景中，预热早于首个触发 = %s；"
        "两个就绪门早于首个触发 = %s；SIGUSR1/SIGUSR2 门控顺序正确 = %s。"
        % (s["prewarm_before_first_trigger_all_auto"],
           s["readiness_gates_before_first_trigger_all_auto"],
           s["voice_gating_ordered_all_auto"]),
        "",
        "> 所有时间都是**虚拟时钟下的源码延迟预算**，不是物理秒表读数；",
        "> 不得与历史 4.35 s trigger-to-follow 延迟合并、对比或当作复现。",
        "",
        "## 4. 五个可复核发现",
        "",
        "1. **六个 CLI 模式全部可运行且全部完成**，共用同一份 audited 控制源码。",
        "   E3 只覆盖其中一条决策链；本次把 `main()` 分发、手动模式、",
        "   stdin 唤醒与自动唤醒状态机纳入同一可复核证据体。",
        "2. **预热与就绪门在关键路径之外**。`voice.prewarm` 与",
        "   `camera.open_async` 都早于首个 `mic.doa_trigger`，两个 60 s 就绪门",
        "   在唤醒循环之前完成；语音子系统的冷启动被前移到空闲期，",
        "   与正文关于「盒子与机器人结合的价值在于编排」一致。",
        "3. **降级是源码的一等分支，不是测试补丁**。语音未就绪、相机未就绪、",
        "   API 视觉告警、DOA 死区、无角度、检测丢失、dry-run、跳过云台预转",
        "   都由真实源码分支处理并被 replay 实际触发；其中 dry-run 场景",
        "   `enable_motors=False` 时串口写入为 0。",
        "4. **`interrupt_on_sound=True` 在整个 replay 中为 0**，与 E7 静态审计一致：",
        "   三个 `follow_loop` 调用点都不启用声源中断，自动唤醒改用 120 s 上限",
        "   和周期后麦克风清空。这是源码结构结论，不是运行故障。",
        "5. **就绪门与跟随循环的判据不一致**。`S08` 中 `_wait_camera_ready`",
        "   明确返回 `False`（源码只打印 `degraded readiness` 后继续），而",
        "   `follow_loop` 里的 `wait_for_camera_ready` 只检查打开线程是否存活、",
        "   不检查 `latest_status['state']`，因此跟随循环仍然进入。",
        "   这是可复核的源码行为，说明「降级继续」不等于「视觉已就绪」；",
        "   论文不能把它当作相机就绪下的演示成功。",
        "",
        "## 4b. 交叉自检",
        "",
        "| 检查 | 观测 | 期望 | 结果 |",
        "| --- | --- | --- | --- |",
    ]
    for c in r["cross_checks"]:
        lines.append("| `%s` | `%s` | `%s` | %s |" % (
            c["name"], str(c["observed"]), str(c["expected"]),
            "PASS" if c["pass"] else "FAIL"))
    lines += [
        "",
        "通过 %d/%d。" % (sum(1 for c in r["cross_checks"] if c["pass"]),
                        len(r["cross_checks"])),
        "",
        "## 5. 帧契约与安全守卫",
        "",
        "每帧按 `AA 55 | cmd | pulse_lo | pulse_hi | 0D 0A` 解码，检查 header/tail、",
        "已知 opcode 与主机侧 clamp（head 5–15、body 5–25）。",
        "%d 帧全部通过（%d 处错误）。" % (
            s["frames_total"], s["contract_errors_total"]),
        "",
        "被守卫的源码方法（任何意外调用都会抛错并计数）：",
    ]
    for g in r["guardrails"]["guarded_source_methods"]:
        lines.append("- `%s`" % g)
    lines += [
        "",
        "被记录但**真实执行**的控制方法：%s。" % ", ".join(
            "`%s`" % m for m in r["guardrails"]["wrapped_real_control_methods"]),
        "",
        "## 6. 边界",
        "",
        "- 记录桩不是设备：`logical_device_opens` 只表示源码调用了打开方法。",
        "- 语音、HTTP、相机都是桩；不主张对话、网络或检测精度结果。",
        "- counter-head 帧仍是主机侧意图；MCU 无可靠排队路径，见",
        "  `BOUNDARY_ISOLATION.md` / `PRODUCT_LOOP_REPLAY.md`。",
        "- 需要物理验证的仍是：运动态 F1/F2、N=6 真机演示、MCU 脉冲脚波形、",
        "  云台机械角度与干净 A/B 基线。",
        "",
        "指纹 `%s`，可在 Xavier 上重跑比对。" % r["fingerprint_sha256"],
        "",
        "相关：`RUNTIME_CONTROL_GRAPH.md`、`PRODUCT_LOOP_REPLAY.md`、",
        "`INTEGRATION_VALUE.md`。",
        "",
    ]
    md_path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime", default=str(RUNTIME))
    ap.add_argument("--firmware", default=str(FIRMWARE))
    ap.add_argument("--output", default=str(DEFAULT_JSON))
    ap.add_argument("--markdown", default=str(DEFAULT_MD))
    ap.add_argument("--skip-markdown", action="store_true")
    args = ap.parse_args()

    result = build_result(Path(args.runtime), Path(args.firmware))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False,
                              default=str) + "\n")
    if not args.skip_markdown:
        render_markdown(result, Path(args.markdown))

    s = result["summary"]
    print("full runtime replay: %d/%d scenarios, %d/%d modes, "
          "%d frames, %d contract errors, %d physical opens" % (
              s["scenarios_completed"], s["scenarios_total"],
              len(s["modes_covered"]), s["modes_total"],
              s["frames_total"], s["contract_errors_total"],
              s["physical_device_open_attempts_total"]))
    if s["failed_scenarios"]:
        print("FAILED: %s" % s["failed_scenarios"])
    print("fingerprint=%s" % result["fingerprint_sha256"])
    print("wrote %s%s" % (out, "" if args.skip_markdown else
                          " and %s" % args.markdown))


if __name__ == "__main__":
    main()
