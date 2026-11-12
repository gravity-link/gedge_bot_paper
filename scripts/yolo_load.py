#!/usr/bin/env python3
"""Run continuous YOLO inference for ab_control.py, with a readiness marker."""
import argparse
import json
import os
import signal
import time
from pathlib import Path

running = True


def stop(_signum, _frame):
    global running
    running = False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    import numpy as np
    import torch
    from ultralytics import YOLO

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the AI-loaded condition")
    model = YOLO(args.model)
    model.to("cuda:0")
    model.model.fuse()
    frame = np.random.default_rng(20261107).integers(
        0, 256, size=(480, 640, 3), dtype=np.uint8)
    for _ in range(3):
        model.predict(frame, device="cuda:0", imgsz=args.imgsz,
                      classes=[0], verbose=False)
    torch.cuda.synchronize()
    metadata = {
        "status": "ready",
        "model": str(Path(args.model).resolve()),
        "imgsz": args.imgsz,
        "pid": os.getpid(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "device": torch.cuda.get_device_name(0),
        "memory_allocated_bytes": torch.cuda.memory_allocated(0),
        "timestamp_unix": time.time(),
    }
    Path(args.ready_file).write_text(json.dumps(metadata, indent=2) + "\n")
    iterations = 0
    while running:
        model.predict(frame, device="cuda:0", imgsz=args.imgsz,
                      classes=[0], verbose=False)
        torch.cuda.synchronize()
        iterations += 1
    print(json.dumps({"status": "stopped", "iterations_after_ready": iterations}), flush=True)


if __name__ == "__main__":
    main()
