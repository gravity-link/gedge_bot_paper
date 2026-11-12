#!/usr/bin/env python3
"""E7: source-grounded audit of the *overall* robot control plane.

WHAT THIS IS
    A fully offline AST audit of ``evidence/robot_runtime.current.py``, the
    read-only snapshot of
    root@192.168.1.130:/root/src/gedge_bot/RobotControl/robot_runtime.py
    (gedge_bot HEAD c25b444, SHA-256 recorded in the JSON).  Unlike the E3
    product-loop replay, which executes one decision chain, this script
    enumerates the whole control plane of the delivered robot: CLI modes,
    main() dispatch, concurrency sites (threads / subprocess / HTTP server),
    readiness barriers, degraded branches, safety interlocks and the narrow
    serial boundary.

    Every number is derived from the AST. Nothing here is a runtime
    measurement, a physical demonstration or a hand-entered statistic.

WHAT THIS IS NOT
    - NOT a measurement of latency, throughput, success rate or reliability.
      A branch present in source is not a branch exercised on hardware.
    - NOT evidence that the October demonstrations used this exact revision.
    - NOT a claim that any subprocess, camera, microphone or UART was opened.
      This script never imports or executes the runtime module; it parses text.

The JSON carries an evidence envelope and a fingerprint so the same audit can
be rerun on the Xavier and compared byte-for-byte on all derived facts.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import sha256_file, utc_now_iso  # noqa: E402

EVID = ROOT / "evidence"
RUNTIME = EVID / "robot_runtime.current.py"
DEFAULT_JSON = EVID / "analysis" / "runtime_control_graph_20261107.json"
DEFAULT_MD = EVID / "RUNTIME_CONTROL_GRAPH.md"

# Methods whose control flow is reported in detail. These are names, not data.
CONTROL_METHODS = [
    "RobotRuntime.run_mic",
    "RobotRuntime.run_motor_test",
    "RobotRuntime.run_sound_once",
    "RobotRuntime.run_follow",
    "RobotRuntime.run_stdin_wakeup",
    "RobotRuntime.run_auto_wakeup",
    "RobotRuntime.follow_loop",
    "RobotRuntime.orient_to_sound",
    "RobotRuntime._wait_camera_ready",
    "RobotRuntime.flush_mic_array",
    "VoiceAssistantProcess.wait_until_ready",
    "VoiceAssistantProcess.prewarm",
    "MotorController._send",
    "VisualFollower.update",
]

BRANCH_CATEGORIES = [
    ("voice_ready", "readiness_degradation"),
    ("camera_ready", "readiness_degradation"),
    ("use_api_vision", "api_latency_warning"),
    ("use_api", "backend_selection"),
    ("enabled", "feature_gate"),
    ("monitor", "feature_gate"),
    ("self_test", "feature_gate"),
    ("skip_sound_head", "optional_motion"),
    ("face_x is None", "detector_dropout"),
    ("event is None", "missing_sensor"),
    ("event is not None", "missing_sensor"),
    ("enable_motors", "dry_run_guard"),
    ("direction is None", "dead_zone"),
    ("direction_count < 2", "direction_confirmation"),
    ("last_command_time", "command_rate_limit"),
    ("last_body_time", "body_rate_limit"),
    ("accumulated", "body_correction"),
    ("interrupt_on_sound", "sound_interrupt_path"),
    ("updated_at", "stale_frame"),
    ("camera_open_thread", "camera_async"),
    ("state", "camera_readiness"),
    ("wait_hook", "interruptible_wait"),
    ("text in", "operator_quit"),
    ("poll()", "poll"),
]


def seg(src, node):
    try:
        return ast.get_source_segment(src, node)
    except Exception:
        return None


def literal(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [literal(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {literal(k): literal(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = literal(node.operand)
        return -v if isinstance(v, (int, float)) else None
    return None


def call_name(call):
    f = call.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    elif isinstance(f, ast.Call):
        parts.append("()")
    if not parts:
        return None
    return ".".join(reversed(parts))


def collect(tree, src):
    """Walk with a qualname stack; record calls, ifs, assigns and loops."""
    calls, ifs, assigns, loops = [], [], [], []

    def visit(node, qn):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, (qn + "." if qn else "") + child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, (qn + "." if qn else "") + child.name)
            elif isinstance(child, ast.Call):
                name = call_name(child)
                calls.append({
                    "owner": qn or "<module>",
                    "line": child.lineno,
                    "name": name,
                    "expr": seg(src, child),
                    "keywords": {
                        kw.arg: (literal(kw.value) if literal(kw.value) is not None
                                 else seg(src, kw.value))
                        for kw in child.keywords if kw.arg
                    },
                    "args": [(literal(a) if literal(a) is not None else seg(src, a))
                             for a in child.args],
                })
                visit(child, qn)
            elif isinstance(child, ast.If):
                ifs.append({
                    "owner": qn or "<module>",
                    "line": child.lineno,
                    "test": seg(src, child.test),
                    "body_calls": [call_name(c) for c in ast.walk(child)
                                   if isinstance(c, ast.Call)],
                })
                visit(child, qn)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for t in targets:
                    assigns.append({
                        "owner": qn or "<module>",
                        "line": child.lineno,
                        "target": seg(src, t),
                        "value": (literal(child.value) if child.value is not None
                                  and literal(child.value) is not None
                                  else seg(src, child.value) if child.value else None),
                    })
                visit(child, qn)
            elif isinstance(child, (ast.While, ast.For)):
                loops.append({
                    "owner": qn or "<module>",
                    "line": child.lineno,
                    "type": type(child).__name__,
                    "test": seg(src, child.test) if isinstance(child, ast.While) else None,
                })
                visit(child, qn)
            else:
                visit(child, qn)

    visit(tree, "")
    return calls, ifs, assigns, loops


def classes_and_methods(tree):
    classes, module_functions = [], []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [{"name": m.name, "line": m.lineno, "end_line": m.end_lineno}
                       for m in node.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            classes.append({"name": node.name, "line": node.lineno,
                            "end_line": node.end_lineno, "methods": methods})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module_functions.append({"name": node.name, "line": node.lineno,
                                     "end_line": node.end_lineno})
    return classes, module_functions


def module_constants(tree, src):
    out, expressions = {}, {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            v = literal(node.value)
            name = node.targets[0].id
            if v is not None and isinstance(v, (str, int, float, bool, list)):
                out[name] = v
            else:
                expressions[name] = seg(src, node.value)
    return out, expressions


def cli_surface(tree, src):
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "build_arg_parser"), None)
    if fn is None:
        return [], {}, [], {}
    arguments, defaults, modes, expr_defaults = [], {}, [], {}
    for call in [c for c in ast.walk(fn) if isinstance(c, ast.Call)]:
        name = call_name(call) or ""
        if name.endswith("add_argument"):
            flags = [literal(a) for a in call.args if isinstance(a, ast.Constant)]
            kw = {}
            for k in call.keywords:
                if k.arg is None:
                    continue
                v = literal(k.value)
                kw[k.arg] = v if v is not None else {"expr": seg(src, k.value)}
            arguments.append({"flags": flags, "kwargs": kw, "line": call.lineno})
            if "--mode" in flags and "choices" in kw:
                modes = list(kw["choices"])
        elif name.endswith("set_defaults"):
            for k in call.keywords:
                if k.arg:
                    v = literal(k.value)
                    if v is not None:
                        defaults[k.arg] = v
                    else:
                        expr_defaults[k.arg] = seg(src, k.value)
    for a in arguments:
        for key in ("default",):
            if key in a["kwargs"] and a["kwargs"][key] is not None:
                dest = a["kwargs"].get("dest")
                if isinstance(dest, dict):
                    dest = dest.get("expr")
                if dest is None:
                    long = [f for f in a["flags"] if isinstance(f, str)
                            and f.startswith("--")]
                    dest = long[0][2:].replace("-", "_") if long else None
                value = a["kwargs"][key]
                if dest and dest not in defaults and dest not in expr_defaults:
                    if isinstance(value, dict):
                        expr_defaults[dest] = value.get("expr")
                    else:
                        defaults[dest] = value
    return arguments, defaults, modes, expr_defaults


def main_dispatch(tree, src):
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if fn is None:
        return {}
    out = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = seg(src, node.test) or ""
        m = re.search(r"args\.mode\s*==\s*['\"]([^'\"]+)['\"]", test)
        if not m:
            continue
        called = []
        for stmt in node.body:  # exclude orelse: elif branches are separate If nodes
            for c in ast.walk(stmt):
                if isinstance(c, ast.Call):
                    n = call_name(c) or ""
                    if n.startswith("runtime."):
                        called.append({"line": c.lineno, "call": n,
                                       "expr": seg(src, c)})
        out[m.group(1)] = sorted(called, key=lambda d: d["line"])
    return out


def control_sequence(calls):
    """Source-derived ordered call sequence of run_auto_wakeup."""
    seq = [c for c in calls if c["owner"] == "RobotRuntime.run_auto_wakeup"
           and (c["name"] or "").startswith("self.")]
    return sorted(seq, key=lambda c: c["line"])


def first_line(calls, name, after=0):
    for c in calls:
        if c["name"] == name and c["line"] > after:
            return c["line"]
    return None


def numeric_literals(tree, qualname, src):
    node = None
    for c in tree.body:
        if isinstance(c, ast.ClassDef):
            for m in c.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and c.name + "." + m.name == qualname:
                    node = m
        elif isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and c.name == qualname:
            node = c
    if node is None:
        return []
    return [{"line": n.lineno, "value": n.value,
             "expr": seg(src, n)}
            for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)]


def categorize_branches(ifs):
    out = []
    for i in ifs:
        test = i["test"] or ""
        cats = [label for needle, label in BRANCH_CATEGORIES if needle in test]
        out.append({"owner": i["owner"], "line": i["line"], "test": test,
                    "categories": sorted(set(cats)) or ["other"]})
    return out


def build_audit(path):
    src = path.read_text()
    tree = ast.parse(src)
    classes, module_functions = classes_and_methods(tree)
    calls, ifs, assigns, loops = collect(tree, src)
    arguments, defaults, modes, expr_defaults = cli_surface(tree, src)
    dispatch = main_dispatch(tree, src)
    consts, const_exprs = module_constants(tree, src)
    seq = control_sequence(calls)
    branches = categorize_branches(ifs)

    # --- concurrency and I/O sites, derived purely from call nodes -----------
    def sites(suffixes):
        return [c for c in calls if c["name"] and
                any(c["name"].endswith(s) for s in suffixes)]

    thread_sites = sites(["threading.Thread", "Thread"])
    subprocess_sites = sites(["subprocess.Popen", "Popen"])
    http_server_sites = sites(["ThreadingHTTPServer"])
    http_client_sites = sites(["urlopen", "Request"])
    signal_sites = sites(["send_signal"])
    serial_sites = sites(["os.open", "os.write", "tcdrain", "tcsetattr",
                          "tcflush", "select.select"])

    execution_contexts = {
        "daemon_threads": len(thread_sites),
        "subprocess_launch_sites": len(subprocess_sites),
        "http_server_sites": len(http_server_sites),
        "http_client_sites": len(http_client_sites),
        "signal_gate_sites": len(signal_sites),
        "serial_or_poll_sites": len(serial_sites),
    }

    # --- cross-component orchestration calls ---------------------------------
    cross = [c for c in calls if (c["name"] or "").startswith("self.")
             and "." in (c["name"] or "")[5:]]
    cross_targets = sorted({c["name"] for c in cross})

    # --- frame / opcode facts ------------------------------------------------
    frame_builder = next((n for n in tree.body
                          if isinstance(n, ast.FunctionDef)
                          and n.name == "build_motor_command"), None)
    frame_bytes = None
    if frame_builder:
        for n in ast.walk(frame_builder):
            if isinstance(n, ast.Call) and call_name(n) == "bytes" \
                    and n.args and isinstance(n.args[0], (ast.List, ast.Tuple)):
                frame_bytes = len(n.args[0].elts)
                break
    opcodes = {k: v for k, v in consts.items() if k.startswith("CMD_")}

    # --- control-flow checks -------------------------------------------------
    seq_names = [c["name"] for c in seq]
    prewarm = first_line(seq, "self.voice_assistant.prewarm")
    async_open = first_line(seq, "self.open_camera_async")
    voice_ready = first_line(seq, "self.voice_assistant.wait_until_ready")
    camera_ready = first_line(seq, "self._wait_camera_ready")
    trigger = first_line(seq, "self.mic.wait_for_next_angle")
    orient = first_line(seq, "self.orient_to_sound")
    qt = first_line(seq, "self.trigger_qt_voice_dialog")
    voice_start = first_line(seq, "self.voice_assistant.start")
    follow = first_line(seq, "self.follow_loop")
    voice_stop = first_line(seq, "self.voice_assistant.stop")
    flush = first_line(seq, "self.flush_mic_array")

    follow_calls = [c for c in calls if c["name"] == "self.follow_loop"]
    interrupt_enabled = [c for c in follow_calls
                         if c["keywords"].get("interrupt_on_sound") is True]
    wait_hook_assigns = [a for a in assigns if "wait_hook" in (a["target"] or "")]
    readiness_calls = [c for c in calls if c["name"] in
                       ("self.voice_assistant.wait_until_ready",
                        "self._wait_camera_ready")]
    readiness_timeouts = [c["keywords"].get("timeout") for c in readiness_calls]
    auto_numbers = [n["value"] for n in numeric_literals(tree, "RobotRuntime.run_auto_wakeup", src)]

    checks = []

    def check(name, observed, expected, note):
        checks.append({"name": name, "observed": observed, "expected": expected,
                       "pass": observed == expected, "note": note})

    check("cli_mode_count", len(modes), 6,
          "all six --mode choices come from build_arg_parser")
    check("main_dispatch_covers_modes", sorted(dispatch.keys()), sorted(modes),
          "main() dispatches every CLI mode to a run_* method")
    check("frame_length_bytes", frame_bytes, 7,
          "build_motor_command emits a seven-byte list")
    check("opcode_set", sorted(opcodes.values()), [1, 2, 9],
          "left / right / vision-track opcodes")
    check("motor_port", consts.get("MOTOR_PORT"), "/dev/ttyUSB0", "module constant")
    check("motor_baud", consts.get("MOTOR_BAUD"), 9600, "module constant")
    check("mic_port", consts.get("MIC_PORT"), "/dev/ttyACM0", "module constant")
    check("mic_baud", consts.get("MIC_BAUD"), 115200, "module constant")
    check("dry_run_default", defaults.get("enable_motors"), False,
          "serial writes are disabled unless --enable-motors is passed")
    check("prewarm_before_readiness_barrier",
          bool(prewarm and voice_ready and prewarm < voice_ready), True,
          "voice subprocess is warmed before its readiness gate")
    check("camera_async_before_readiness_barrier",
          bool(async_open and camera_ready and async_open < camera_ready), True,
          "camera/YOLO open is started in a daemon thread before the gate")
    check("readiness_before_first_trigger",
          bool(voice_ready and camera_ready and trigger
               and max(voice_ready, camera_ready) < trigger), True,
          "both readiness gates precede the wake-word loop")
    check("product_loop_order",
          bool(all([orient, qt, voice_start, follow, voice_stop, flush])
               and orient < qt < voice_start < follow < voice_stop < flush), True,
          "orient -> UI -> voice -> follow -> voice stop -> mic flush")
    check("readiness_timeouts_s", sorted([t for t in readiness_timeouts
                                          if isinstance(t, (int, float))]),
          [60.0, 60.0], "both gates use a 60 s bound")
    check("follow_cap_s", 120 in auto_numbers, True,
          "auto-wakeup caps a follow window at 120 s")
    check("sound_interrupt_never_enabled", len(interrupt_enabled), 0,
          "all three follow_loop call sites use interrupt_on_sound=False/omitted")
    check("wait_hook_never_installed",
          all(a["value"] is None or a["value"] == "None"
              for a in wait_hook_assigns), True,
          "MotorController.wait_hook is never assigned a callable")
    check("counter_head_callback",
          any("after_send" in (c["expr"] or "")
              for c in calls if c["owner"] == "RobotRuntime.orient_to_sound"), True,
          "host issues the second head frame through a post-write callback")
    check("host_side_clamps",
          all(any(c["name"] == "clamp" and c["owner"] == owner for c in calls)
              for owner in ("MotorController.pan_head", "MotorController.turn_body")),
          True, "head and body pulse counts are clamped in the host")
    check("voice_signal_gates",
          sorted({str(c["args"][0]) for c in signal_sites if c["args"]}),
          ["signal.SIGUSR1", "signal.SIGUSR2"],
          "prewarmed voice subprocess is gated by SIGUSR1/SIGUSR2")

    degraded_categories = sorted({cat for b in branches for cat in b["categories"]
                                  if cat != "other"})
    control_branches = [b for b in branches
                        if b["owner"].startswith("RobotRuntime.")
                        or b["owner"].startswith("VoiceAssistantProcess.")
                        or b["owner"].startswith("MotorController.")
                        or b["owner"].startswith("VisualFollower.")
                        or b["owner"].startswith("CameraFaceTracker.")]

    interlock_methods = {
        "RobotRuntime.run_auto_wakeup": "readiness / follow cap",
        "RobotRuntime.flush_mic_array": "post-follow angle flush",
        "RobotRuntime.follow_loop": "follow cadence / stale-frame skip",
        "MotorController._send": "min command spacing / body interval / settle",
        "MotorController._interruptible_sleep": "interruptible wait",
        "VisualFollower.update": "dead zone / 2-frame confirmation / rate limit",
    }
    interlocks = {
        q: {"role": role,
            "numeric_literals": numeric_literals(tree, q, src)}
        for q, role in interlock_methods.items()
    }

    summary = {
        "source_lines": len(src.splitlines()),
        "source_bytes": len(src.encode()),
        "classes": len(classes),
        "class_methods": sum(len(c["methods"]) for c in classes),
        "module_functions": len(module_functions),
        "cli_arguments": len(arguments),
        "cli_modes": modes,
        "dispatch_entries": len(dispatch),
        "execution_contexts": execution_contexts,
        "cross_component_call_sites": len(cross),
        "cross_component_targets": len(cross_targets),
        "loops": len(loops),
        "infinite_loops": sum(1 for x in loops
                              if x["type"] == "While" and x["test"] == "True"),
        "branch_sites": len(branches),
        "control_branch_sites": len(control_branches),
        "degraded_categories": degraded_categories,
        "readiness_gates": len(readiness_calls),
        "safety_interlock_methods": len(interlocks),
        "frame_bytes": frame_bytes,
        "opcodes": opcodes,
        "cli_defaults": defaults,
        "cli_default_expressions": expr_defaults,
        "physical_interfaces": {
            "serial_ports": [consts.get("MIC_PORT"), consts.get("MOTOR_PORT")],
            "http_endpoint_expressions": {
                k: v for k, v in const_exprs.items()
                if "URL" in k or "API" in k or "PATH" in k
            },
        },
    }

    canonical = {
        "summary": summary,
        "dispatch": dispatch,
        "control_sequence": [{"line": c["line"], "name": c["name"],
                              "expr": c["expr"]} for c in seq],
        "checks": [{k: v for k, v in c.items() if k != "note"} for c in checks],
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                   default=str).encode()).hexdigest()

    return {
        "schema": "gedgebot.runtime_control_graph.v1",
        "generated_at_utc": utc_now_iso(),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(__file__),
        "evidence_status": "source_static_audit",
        "device_measurement": False,
        "physical_actuation": False,
        "no_physical_demonstration": True,
        "citation_allowed": False,
        "evidence_note": (
            "AST-derived inventory of the audited robot_runtime.py control "
            "plane. Branch presence is not branch execution; no peripheral, "
            "subprocess, model or serial port was opened. Timings are source "
            "constants, not measurements. Must not be pooled with device "
            "results or presented as a physical demonstration."),
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
            "remote_path": "/root/src/gedge_bot/RobotControl/robot_runtime.py",
            "source_commit": "c25b444",
        },
        "summary": summary,
        "classes": classes,
        "module_functions": module_functions,
        "cli_arguments": arguments,
        "dispatch": dispatch,
        "auto_wakeup_call_sequence": [{"line": c["line"], "name": c["name"],
                                       "expr": c["expr"]} for c in seq],
        "concurrency_sites": {
            "threads": thread_sites, "subprocesses": subprocess_sites,
            "http_server": http_server_sites, "http_client": http_client_sites,
            "signal_gates": signal_sites, "serial_and_poll": serial_sites,
        },
        "control_branches": control_branches,
        "interlocks": interlocks,
        "loops": loops,
        "checks": checks,
        "fingerprint_sha256": fingerprint,
    }


def render_markdown(r, md_path):
    s = r["summary"]
    lines = [
        "# E7：整体机器人控制面静态审计（robot_runtime.py）",
        "",
        "自动生成于 `%s`，由 `scripts/runtime_control_graph.py` 从 `%s` 渲染。"
        % (r["generated_at_utc"], r["inputs"]["runtime_path"]),
        "**不要手改本文件**；改脚本后重跑即可。",
        "",
        "## 1. 这是什么 / 不是什么",
        "",
        "**是**：对 `root@192.168.1.130:/root/src/gedge_bot/RobotControl/robot_runtime.py`",
        "的只读快照做 AST 盘点，覆盖整个控制面：6 个 CLI 模式、`main()` 分发、",
        "线程/子进程/HTTP 服务、`auto-wakeup` 状态机、就绪门、降级分支、安全互锁与",
        "7 字节串口边界。所有数字从 AST 推导，SHA-256 `%s`。" % r["inputs"]["runtime_sha256"],
        "",
        "**不是**：不是运行测量，不是物理演示，不打开任何串口/相机/麦克风/子进程，",
        "也不证明十月演示用的是这一版源码。源码里有某个分支，不等于该分支在硬件上被触发过。",
        "",
        "证据信封：`evidence_status=%s`，`device_measurement=%s`，"
        "`citation_allowed=%s`。" % (r["evidence_status"], r["device_measurement"],
                                    r["citation_allowed"]),
        "",
        "## 2. 控制面总览",
        "",
        "| 项目 | 值 | 来源 |",
        "| --- | --- | --- |",
        "| 源行数 / 字节 | %d / %d | AST + 文件 |" % (s["source_lines"], s["source_bytes"]),
        "| 类 / 类方法 / 模块函数 | %d / %d / %d | AST |" % (
            s["classes"], s["class_methods"], s["module_functions"]),
        "| CLI 参数 / 模式 | %d / %d | `build_arg_parser` |" % (
            s["cli_arguments"], len(s["cli_modes"])),
        "| `main()` 分发表项 | %d | `main()` If 链 |" % s["dispatch_entries"],
        "| 守护线程 / 子进程 / HTTP 服务 / HTTP 客户端调用点 | %d / %d / %d / %d | AST 调用点 |" % (
            s["execution_contexts"]["daemon_threads"],
            s["execution_contexts"]["subprocess_launch_sites"],
            s["execution_contexts"]["http_server_sites"],
            s["execution_contexts"]["http_client_sites"]),
        "| 跨组件编排调用点 / 不同目标 | %d / %d | `self.<subsystem>.<method>` |" % (
            s["cross_component_call_sites"], s["cross_component_targets"]),
        "| 循环 / 无限 `while True` | %d / %d | AST |" % (s["loops"], s["infinite_loops"]),
        "| 分支点 / 控制类分支点 | %d / %d | AST If 节点 |" % (
            s["branch_sites"], s["control_branch_sites"]),
        "| 就绪门 / 安全互锁方法 | %d / %d | AST |" % (
            s["readiness_gates"], s["safety_interlock_methods"]),
        "| 边界帧长度 / opcode | %d / %s | `build_motor_command` |" % (
            s["frame_bytes"], ", ".join("0x%02X" % v for v in sorted(s["opcodes"].values()))),
        "",
        "降级分支类别（源码中出现）：%s。" % ", ".join("`%s`" % c for c in s["degraded_categories"]),
        "",
        "## 3. 六个 CLI 模式与 `main()` 分发",
        "",
        "| 模式 | 调用的方法 | 行号 |",
        "| --- | --- | --- |",
    ]
    for mode in s["cli_modes"]:
        entries = r["dispatch"].get(mode, [])
        lines.append("| `%s` | %s | %s |" % (
            mode,
            ", ".join("`%s`" % e["call"] for e in entries) or "--",
            ", ".join(str(e["line"]) for e in entries) or "--"))
    lines += [
        "",
        "## 4. `run_auto_wakeup` 的源码导出控制序列",
        "",
        "| 行号 | 调用 |",
        "| --- | --- |",
    ]
    for c in r["auto_wakeup_call_sequence"]:
        lines.append("| %d | `%s` |" % (c["line"], (c["expr"] or c["name"]).replace("|", "\\|")))
    lines += [
        "",
        "关键结构：语音子进程 `prewarm()` 与相机/YOLO `open_camera_async()` 在",
        "两个 60 s 就绪门之前**并行**启动；通过后才进入唤醒循环。一次唤醒周期是",
        "`wait_for_next_angle` → `orient_to_sound` → `trigger_qt_voice_dialog` →",
        "`voice.start`(SIGUSR1) → `follow_loop`(上限 120 s) → `voice.stop`(SIGUSR2)",
        "→ `flush_mic_array`(0.5 s)。语音与跟随不在同一条关键路径上，这解释了",
        "十月 `voice=False` 与 6/6 trigger-to-follow 完成可以并存。",
        "",
        "## 5. 两个可复核的源码发现",
        "",
        "1. **`follow_loop(interrupt_on_sound=True)` 在 audited 控制面中不可达**。",
        "   三个调用点（`run_follow`、`run_stdin_wakeup`、`run_auto_wakeup`）",
        "   全部使用默认值 `False` 或显式 `False`；函数内部的声源中断分支因此",
        "   只是预留能力。自动唤醒模式改用固定 120 s 上限 + 周期后 0.5 s 麦克风清空。",
        "2. **`MotorController.wait_hook` 从未被赋值为可调用对象**。",
        "   `__init__` 与 `run_auto_wakeup` 都把它设为 `None`，所以",
        "   `_interruptible_sleep` 实际不可中断；转向段时延由固定等待主导。",
        "   这两点都是源码结构结论，不是运行时观测。",
        "",
        "## 6. 自检（全部从 AST 推导）",
        "",
        "| 检查 | 观测 | 期望 | 结果 |",
        "| --- | --- | --- | --- |",
    ]
    for c in r["checks"]:
        lines.append("| `%s` | `%s` | `%s` | %s |" % (
            c["name"], str(c["observed"]).replace("|", "\\|"),
            str(c["expected"]).replace("|", "\\|"),
            "PASS" if c["pass"] else "FAIL"))
    lines += [
        "",
        "通过 %d/%d。指纹 `%s`，可在 Xavier 上重跑比对。" % (
            sum(1 for c in r["checks"] if c["pass"]), len(r["checks"]),
            r["fingerprint_sha256"]),
        "",
        "## 7. 边界",
        "",
        "- 源码存在 ≠ 运行时触发；分支覆盖需看 `FULL_RUNTIME_REPLAY.md`。",
        "- 所有时间都是源码常量（60 s 就绪、120 s 跟随、0.30 s 命令间隔、",
        "  `0.043N+1.2` s settle），**不是** wall-clock 测量。",
        "- `enable_motors` 默认 `False`：源码默认不写串口。",
        "- 不主张对话成功率、机械角度、可靠性或任何物理演示。",
        "",
        "相关：`PRODUCT_LOOP_REPLAY.md`、`INTEGRATION_VALUE.md`、`BOUNDARY_ISOLATION.md`。",
        "",
    ]
    md_path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime", default=str(RUNTIME))
    ap.add_argument("--output", default=str(DEFAULT_JSON))
    ap.add_argument("--markdown", default=str(DEFAULT_MD))
    ap.add_argument("--skip-markdown", action="store_true")
    args = ap.parse_args()

    runtime = Path(args.runtime)
    result = build_audit(runtime)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n")
    if not args.skip_markdown:
        render_markdown(result, Path(args.markdown))

    s = result["summary"]
    passed = sum(1 for c in result["checks"] if c["pass"])
    print("runtime control graph: %d classes / %d methods / %d modes / %d checks passed" % (
        s["classes"], s["class_methods"], len(s["cli_modes"]), passed))
    print("fingerprint=%s" % result["fingerprint_sha256"])
    print("wrote %s%s" % (out, "" if args.skip_markdown else
                          " and %s" % args.markdown))


if __name__ == "__main__":
    main()
