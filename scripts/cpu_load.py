#!/usr/bin/env python3
"""CPU-only background load for ab_control.py, matching yolo_load.py's protocol.

Purpose: the 15 W idle baseline in the first A/B batch showed sporadic
scheduling spikes (p99 5.670 / 0.326 / 0.095 ms). A GPU-free CPU load
condition isolates generic Linux scheduler interference from effects that
require the CUDA/YOLO stack. It never touches a serial port or a model.
"""
import argparse
import json
import multiprocessing
import os
import platform
import signal
import time
from pathlib import Path

running = True


def stop(_signum, _frame):
    global running
    running = False


def burn(stop_event, worker_id):
    """Tight floating-point loop; one process per worker so the GIL is bypassed."""
    value = 0.0001 + worker_id
    while not stop_event.is_set():
        for _ in range(20000):
            value = value * 1.0000001 + 0.0000003
            if value > 1e9:
                value = 0.0001
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # --model/--imgsz are accepted but ignored: ab_control.py passes the same
    # argv to every loader so the A/B driver stays unchanged.
    parser.add_argument("--model", default=None)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--threads", type=int, default=4,
                        help="number of CPU-bound worker processes")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    ctx = multiprocessing.get_context("fork")
    stop_event = ctx.Event()
    workers = [ctx.Process(target=burn, args=(stop_event, i), daemon=True)
               for i in range(args.threads)]
    for worker in workers:
        worker.start()
    time.sleep(0.3)
    metadata = {
        "status": "ready",
        "loader": "cpu_load.py",
        "load_type": "cpu_bound_processes_no_cuda_no_model",
        "workers": args.threads,
        "cpu_count": os.cpu_count(),
        "pid": os.getpid(),
        "platform": platform.platform(),
        "timestamp_unix": time.time(),
    }
    Path(args.ready_file).write_text(json.dumps(metadata, indent=2) + "\n")
    while running:
        time.sleep(0.05)
    stop_event.set()
    for worker in workers:
        worker.join(timeout=2.0)
        if worker.is_alive():
            worker.terminate()
    print(json.dumps({"status": "stopped", "load_type": metadata["load_type"]}), flush=True)


if __name__ == "__main__":
    main()
