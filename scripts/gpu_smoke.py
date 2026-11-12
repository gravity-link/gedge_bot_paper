#!/usr/bin/env python3
"""Verify the isolated NVIDIA PyTorch environment with a real CUDA operation."""
import argparse
import json
import platform
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = {
        "schema": "gedgebot.gpu_smoke.v1",
        "timestamp_unix": time.time(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "size": args.size,
    }
    try:
        import torch
        result.update({
            "torch": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "device_count": torch.cuda.device_count(),
        })
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        result["device_name"] = torch.cuda.get_device_name(0)
        generator = torch.Generator(device="cuda")
        generator.manual_seed(20261107)
        a = torch.randn((args.size, args.size), device="cuda", generator=generator)
        b = torch.randn((args.size, args.size), device="cuda", generator=generator)
        torch.cuda.synchronize()
        start = time.perf_counter()
        c = a @ b
        torch.cuda.synchronize()
        result.update({
            "first_matmul_ms": (time.perf_counter() - start) * 1000,
            "matmul_shape": list(c.shape),
            "matmul_checksum": float(c.sum().cpu()),
            "gpu_smoke": True,
        })
    except Exception as exc:
        result.update({"gpu_smoke": False, "error": f"{type(exc).__name__}: {exc}"})
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result.get("gpu_smoke"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
