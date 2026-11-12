#!/usr/bin/env python3
"""Measure Xavier user-space motor-frame timing without actuating a robot.

The production seven-byte frames are written through the production
TermiosSerial implementation to a POSIX pseudo-terminal, never to the motor
controller. Optionally run yolo_load.py as a child process during measurement.
"""
import argparse
import importlib.util
import json
import math
import os
import platform
import shlex
import statistics
import subprocess
import sys
import tempfile
import threading
import select
import time
from pathlib import Path


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("robot_runtime_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def percentile_nearest(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def summarize(post_write_ns, period_ms):
    intervals = [(b - a) / 1e6 for a, b in zip(post_write_ns, post_write_ns[1:])]
    deviations = [value - period_ms for value in intervals]
    absolute = [abs(value) for value in deviations]
    return {
        "interval_count": len(intervals),
        "period_ms": period_ms,
        "interval_mean_ms": statistics.fmean(intervals) if intervals else None,
        "interval_std_ms": statistics.pstdev(intervals) if len(intervals) > 1 else 0.0,
        "interval_p50_ms": percentile_nearest(intervals, 0.50),
        "interval_p99_ms": percentile_nearest(intervals, 0.99),
        "jitter_p99_abs_deviation_ms": percentile_nearest(absolute, 0.99),
        "jitter_max_abs_deviation_ms": max(absolute) if absolute else None,
        "max_interval_ms": max(intervals) if intervals else None,
        "min_interval_ms": min(intervals) if intervals else None,
        "intervals_ms": intervals,
        "signed_deviations_ms": deviations,
    }


def command_output(argv):
    try:
        return subprocess.check_output(argv, stderr=subprocess.STDOUT, timeout=5,
                                       text=True).strip()
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_runtime = Path(__file__).resolve().parent.parent / "evidence" / "robot_runtime.current.py"
    parser.add_argument("--runtime", default=str(default_runtime))
    parser.add_argument("--condition", required=True)
    parser.add_argument("--intervals", type=int, default=200,
                        help="number of inter-frame intervals; sends intervals+1 frames")
    parser.add_argument("--period-ms", type=float, default=50.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--load-python", help="Python executable for the optional background load")
    parser.add_argument("--load-script",
                        help="loader script (default: yolo_load.py next to this file); "
                             "use scripts/cpu_load.py for a CPU-only control")
    parser.add_argument("--load-model", default="/root/src/sample/yolov8n.pt")
    parser.add_argument("--load-imgsz", type=int, default=640)
    parser.add_argument("--load-timeout", type=float, default=120.0)
    parser.add_argument("--load-warmup", type=float, default=2.0)
    args = parser.parse_args()
    if args.intervals < 1 or args.period_ms <= 0:
        parser.error("intervals must be positive and period-ms must be > 0")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    load_process = None
    load_metadata = None
    load_log = None
    ready_file = None
    load_script_path = None
    if args.load_python:
        ready_file = Path(tempfile.mkstemp(prefix="gedgebot_yolo_ready_", suffix=".json")[1])
        load_log_path = output.with_suffix(output.suffix + ".load.log")
        load_log = open(load_log_path, "w")
        loader = Path(args.load_script) if args.load_script else Path(__file__).with_name("yolo_load.py")
        load_script_path = str(loader.resolve())
        load_argv = [args.load_python, str(loader), "--model", args.load_model,
                     "--imgsz", str(args.load_imgsz), "--ready-file", str(ready_file)]
        env = os.environ.copy()
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        load_process = subprocess.Popen(load_argv, stdout=load_log,
                                        stderr=subprocess.STDOUT, env=env)
        deadline = time.monotonic() + args.load_timeout
        while time.monotonic() < deadline:
            if load_process.poll() is not None:
                raise RuntimeError(f"YOLO load exited with code {load_process.returncode}; see {load_log_path}")
            try:
                load_metadata = json.loads(ready_file.read_text())
                if load_metadata.get("status") == "ready":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        else:
            raise TimeoutError(f"YOLO load was not ready in {args.load_timeout}s; see {load_log_path}")
        time.sleep(args.load_warmup)

    runtime = load_runtime(Path(args.runtime).resolve())
    master_fd, slave_fd = os.openpty()
    slave_name = os.ttyname(slave_fd)
    os.close(slave_fd)
    os.set_blocking(master_fd, False)
    serial = runtime.TermiosSerial(slave_name, 9600)
    serial.open()
    frame = runtime.build_motor_command(runtime.CMD_TURN_LEFT, 5)
    scheduled = []
    pre_write = []
    post_write = []
    received = bytearray()
    reader_stop = threading.Event()

    def drain_pty():
        while not reader_stop.is_set():
            readable, _, _ = select.select([master_fd], [], [], 0.01)
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 65536)
                if chunk:
                    received.extend(chunk)
            except BlockingIOError:
                pass

    reader = threading.Thread(target=drain_pty, name="pty-drain", daemon=True)
    reader.start()
    period_ns = round(args.period_ms * 1e6)
    origin = time.perf_counter_ns() + 100_000_000
    try:
        for index in range(args.intervals + 1):
            target = origin + index * period_ns
            while True:
                remaining = (target - time.perf_counter_ns()) / 1e9
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.002))
            scheduled.append(target)
            pre_write.append(time.perf_counter_ns())
            # tcdrain() has no meaningful UART equivalent on a pty and can
            # block indefinitely on macOS. Measure scheduling plus os.write.
            os.write(serial.fd, frame)
            post_write.append(time.perf_counter_ns())
        time.sleep(0.05)
        reader_stop.set()
        reader.join(timeout=1.0)
        while True:
            try:
                chunk = os.read(master_fd, 65536)
                if not chunk:
                    break
                received.extend(chunk)
            except BlockingIOError:
                break
    finally:
        reader_stop.set()
        reader.join(timeout=1.0)
        serial.close()
        os.close(master_fd)
        if load_process is not None:
            load_process.terminate()
            try:
                load_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                load_process.kill()
                load_process.wait(timeout=5)
        if load_log is not None:
            load_log.close()

    result = {
        "schema": "gedgebot.ab_control.no_motion.v1",
        "condition": args.condition,
        "timestamp_unix": time.time(),
        "physical_motor_writes": False,
        "transport": "POSIX pseudo-terminal; os.write only, no tcdrain or physical UART",
        "frame_hex": frame.hex(" ").upper(),
        "frames_sent": len(post_write),
        "frames_received": len(received) // len(frame),
        "payload_bytes_received": len(received),
        "runtime_path": str(Path(args.runtime).resolve()),
        "runtime_sha256": __import__("hashlib").sha256(Path(args.runtime).read_bytes()).hexdigest(),
        "platform": platform.platform(),
        "python": sys.version,
        "l4t_release": Path("/etc/nv_tegra_release").read_text().strip() if Path("/etc/nv_tegra_release").exists() else None,
        "power_mode": command_output(["nvpmodel", "-q"]) if platform.machine() == "aarch64" else "not Jetson",
        "yolo_load": load_metadata,
        "load_script": load_script_path,
        "load_returncode": load_process.returncode if load_process is not None else None,
        "scheduled_ns": scheduled,
        "pre_write_ns": pre_write,
        "post_write_ns": post_write,
        "write_latency_ms": [(end - start) / 1e6 for start, end in zip(pre_write, post_write)],
        "summary": summarize(post_write, args.period_ms),
    }
    if bytes(received) != frame * len(post_write):
        result["payload_verification"] = "FAILED"
    else:
        result["payload_verification"] = "exact frame sequence"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result["summary"].items() if not k.endswith("s_ms")}, indent=2))
    print("payload:", result["payload_verification"])
    print("output:", output)


if __name__ == "__main__":
    main()
