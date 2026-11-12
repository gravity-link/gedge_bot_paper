#!/usr/bin/env python3
"""Capture ASTRA color frames and record timing plus nonblank-frame evidence."""
import argparse
import hashlib
import json
import math
import os
import platform
import time
from pathlib import Path


def percentile_nearest(values, probability):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)] if ordered else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-path", required=True)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--output", required=True)
    parser.add_argument("--image", help="optional last-frame JPEG; treat as private visual evidence")
    args = parser.parse_args()
    if args.frames < 2:
        parser.error("--frames must be at least 2")

    import cv2
    import numpy as np
    from openni import openni2

    redist = str(Path(args.sdk_path).resolve() / "lib" / "Plugins" / "openni2")
    os.environ["OPENNI2_REDIST"] = redist
    os.environ["OPENNI2_DRIVERS_PATH"] = redist
    openni2.initialize(redist)
    device = None
    stream = None
    timestamps_ns = []
    frame_means = []
    frame_stds = []
    frame_mins = []
    frame_maxs = []
    width = height = None
    last_frame = None
    error = None
    try:
        device = openni2.Device.open_any()
        stream = device.create_color_stream()
        stream.start()
        for _ in range(args.frames):
            color_frame = stream.read_frame()
            if not color_frame:
                raise RuntimeError("OpenNI returned an empty color frame")
            frame = np.frombuffer(color_frame.get_buffer_as_triplet(), dtype=np.uint8)
            frame = frame.reshape((color_frame.height, color_frame.width, 3))
            width, height = color_frame.width, color_frame.height
            timestamps_ns.append(time.perf_counter_ns())
            frame_means.append(float(frame.mean()))
            frame_stds.append(float(frame.std()))
            frame_mins.append(int(frame.min()))
            frame_maxs.append(int(frame.max()))
            last_frame = frame.copy()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if stream is not None:
            stream.stop()
        if device is not None:
            device.close()
        openni2.unload()

    intervals_ms = [(b - a) / 1e6 for a, b in zip(timestamps_ns, timestamps_ns[1:])]
    duration_s = sum(intervals_ms) / 1000.0
    result = {
        "schema": "gedgebot.astra_smoke.v1",
        "timestamp_unix": time.time(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sdk_path": str(Path(args.sdk_path).resolve()),
        "frames_requested": args.frames,
        "frames_captured": len(timestamps_ns),
        "width": width,
        "height": height,
        "duration_s": duration_s,
        "capture_fps": (len(timestamps_ns) - 1) / duration_s if duration_s > 0 else None,
        "interval_mean_ms": sum(intervals_ms) / len(intervals_ms) if intervals_ms else None,
        "interval_p50_ms": percentile_nearest(intervals_ms, 0.50),
        "interval_p99_ms": percentile_nearest(intervals_ms, 0.99),
        "frame_mean_of_means": sum(frame_means) / len(frame_means) if frame_means else None,
        "frame_mean_of_stds": sum(frame_stds) / len(frame_stds) if frame_stds else None,
        "frame_std_min": min(frame_stds) if frame_stds else None,
        "frame_std_max": max(frame_stds) if frame_stds else None,
        "nonblank_frame": bool(frame_stds and max(frame_stds) > 5.0 and max(frame_maxs) - min(frame_mins) > 20),
        "intervals_ms": intervals_ms,
        "frame_means": frame_means,
        "frame_stds": frame_stds,
        "frame_mins": frame_mins,
        "frame_maxs": frame_maxs,
        "error": error,
    }
    if args.image and last_frame is not None:
        image_path = Path(args.image)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        bgr = cv2.cvtColor(last_frame, cv2.COLOR_RGB2BGR)
        if not cv2.imwrite(str(image_path), bgr):
            raise RuntimeError(f"failed to write {image_path}")
        result["image_path"] = str(image_path)
        result["image_sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if not isinstance(v, list)}, indent=2))
    if error or len(timestamps_ns) != args.frames:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
