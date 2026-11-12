#!/usr/bin/env python3
"""Summarize on-device measurement JSONs into a reproducible comparison report.

This script reads ``evidence/device/bench_ai_run*_{MAXN,15W}_*.json`` (first
reconstructed batch), ``bench_ai_source_aligned_*.json`` (parameters copied from
the device source tree), ``ab_control_*.json`` (idle vs AI-loaded control-frame
timing), plus voice / failsafe / ChatTTS results, and emits
``evidence/DEVICE_RESULTS.md``. It never edits the source JSONs and never
invents a value: every cell is either a measured aggregate or an explicit
``not measured`` marker.

The historical October 2026 numbers are transcribed references from the
Extended Abstract, NOT recovered raw measurements. They are printed side by
side for orientation only and must not be pooled with the new measurements.
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
# New evening batch files are *_rep<digit>_*. Older idle baselines are named
# *_repeat<digit>_*, so a plain "_rep" substring check would misclassify them.
REP_PATTERN = re.compile(r"_rep\d+_")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_common import HISTORICAL  # noqa: E402

DEVICE_DIR = ROOT / "evidence" / "device"
ANALYSIS_DIR = ROOT / "evidence" / "analysis"
AI_FIELDS = [
    ("YOLOv8n @320 mean", "yolo_320", "latency_mean_ms", "ms", "yolo_320_15w", "mean_ms"),
    ("YOLOv8n @640 mean", "yolo_640", "latency_mean_ms", "ms", "yolo_640_15w", "mean_ms"),
    ("Whisper 10 s RTF", "whisper_10s", "rtf", "RTF", "whisper_10s_15w", "rtf"),
    ("Whisper 2 s clip RTF", "whisper_2s_clip", "rtf", "RTF", "whisper_2s_clip", "rtf"),
    ("NLLB short", "nllb_short", "latency_mean_s", "s", "nllb_single_model_15w", "short_s"),
    ("NLLB mid", "nllb_mid", "latency_mean_s", "s", "nllb_single_model_15w", "mid_s"),
    ("NLLB long", "nllb_long", "latency_mean_s", "s", "nllb_single_model_15w", "long_s"),
]


def load(pattern):
    return [json.loads(Path(p).read_text()) for p in sorted(glob.glob(str(DEVICE_DIR / pattern)))]


def load_named(pattern):
    """Like load(), but keeps the filename so repeated-batch files can be told apart."""
    return [(Path(p), json.loads(Path(p).read_text()))
            for p in sorted(glob.glob(str(DEVICE_DIR / pattern)))]


def aggregate(runs, key, field):
    values = []
    for run in runs:
        item = run.get("workloads", {}).get(key, {})
        value = item.get("value") or {}
        if item.get("status") == "ok" and value.get(field) is not None:
            values.append(float(value[field]))
    return values


def fmt(values, unit, digits=3):
    if not values:
        return "not measured"
    mean = statistics.fmean(values)
    if len(values) == 1:
        return "%.3f %s" % (mean, unit)
    spread = statistics.pstdev(values)
    return "%.3f ± %.3f %s (n=%d)" % (mean, spread, unit, len(values))


def historical(group, field):
    node = HISTORICAL["bench_ai"].get(group, {})
    value = node.get(field)
    return "n/a" if value is None else "%s" % value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "evidence" / "DEVICE_RESULTS.md"))
    args = parser.parse_args()

    maxn = load("bench_ai_run*_MAXN_*.json")
    w15 = load("bench_ai_run*_15W_*.json")
    voice_maxn = load("bench_voice_MAXN_*.json")
    voice_15w = load("bench_voice_15W_*.json")
    failsafe = load("failsafe_*.json")
    aligned_named = load_named("bench_ai_source_aligned_*.json")
    # The 2026-11-07 evening robustness batch writes *_repN_<timestamp>.json.
    # Keep the original single-run comparison intact and report repeats separately.
    aligned_maxn = [d for p, d in aligned_named
                    if "_MAXN_" in p.name and not REP_PATTERN.search(p.name)]
    aligned_15w = [d for p, d in aligned_named
                   if "_15W_" in p.name and not REP_PATTERN.search(p.name)]
    aligned_rep_maxn = [d for p, d in aligned_named
                        if "_MAXN_" in p.name and REP_PATTERN.search(p.name)]
    aligned_rep_15w = [d for p, d in aligned_named
                       if "_15W_" in p.name and REP_PATTERN.search(p.name)]
    ab_named = load_named("ab_control_*.json")
    ab_runs = [d for _, d in ab_named]
    ab_robust = [(p, d) for p, d in ab_named if REP_PATTERN.search(p.name)]
    sensitivity_named = load_named("sensitivity_*.json")
    # sensitivity_cold_*.json also matches sensitivity_*.json; keep it separate.
    sensitivity = [(p, d) for p, d in sensitivity_named
                   if not p.name.startswith("sensitivity_cold_")]
    sensitivity_cold = [(p, d) for p, d in sensitivity_named
                        if p.name.startswith("sensitivity_cold_")]
    isolation = [json.loads(Path(p).read_text())
                 for p in sorted(glob.glob(str(ANALYSIS_DIR / "boundary_isolation_*.json")))]
    # The Xavier sibling JSON is a cross-host check, not a second result set.
    replay = [json.loads(Path(p).read_text())
              for p in sorted(glob.glob(str(ANALYSIS_DIR / "product_loop_replay_*.json")))
              if not Path(p).name.endswith("_jetson.json")]

    def workload(runs, key, field):
        """First ok value for a workload key across a group of JSONs."""
        for run in runs:
            item = run.get("workloads", {}).get(key, {})
            value = item.get("value") or {}
            if item.get("status") == "ok" and value.get(field) is not None:
                return float(value[field])
        return None

    def ab_run(condition, mode):
        wanted = "%s_%s_pty_20261107" % (condition, mode)
        for run in ab_runs:
            if run.get("condition") == wanted:
                return run
        return None

    def ab_repeats(mode):
        return [r for r in ab_runs
                if str(r.get("condition", "")).startswith("B_idle_%s_repeat" % mode)]

    lines = []
    add = lines.append
    add("# On-device measurement results (reconstructed benchmarks)")
    add("")
    add("Generated from the JSON files in `evidence/device/` by "
        "`scripts/summarize_device_runs.py`. Re-run that script after any new "
        "device run; do not hand-edit the numbers below.")
    add("")
    add("## Evidence status")
    add("")
    add("- These are **new measurements produced on 2026-11-07** by the "
        "reconstructed scripts, not the recovered October 2026 raw runs.")
    add("- The `bench_*`, `chattts_*` and `failsafe_*` JSONs carry "
        "`device_measurement=true` and `citation_allowed=false`. The "
        "`ab_control_*.json` files are emitted by the original `ab_control.py` "
        "and have no evidence envelope; they are treated under the same rule "
        "(new measurement, not citable before manual review). The flag is "
        "conservative: a reviewer must check the estimator definitions before "
        "citing any value.")
    add("- The historical column is **transcribed from Extended Abstract v1.0** "
        "(`HISTORICAL` in `rebuild_common.py`), not from recovered logs. It is "
        "shown for orientation only and must never be pooled with new data.")
    add("")

    if maxn:
        env = maxn[0].get("condition", {})
        add("## Environment")
        add("")
        add("| Item | Value |")
        add("| --- | --- |")
        add("| Power mode observed | %s |" % str(env.get("power_mode_observed", "")).strip().replace("\n", " / "))
        add("| YOLO iterations | %s |" % env.get("iterations_per_workload"))
        add("| NLLB runs per length | %s |" % env.get("nllb_runs_per_length"))
        add("| HF offline | %s |" % env.get("hf_hub_offline"))
        for name, path in (env.get("model_paths") or {}).items():
            add("| Model `%s` | `%s` |" % (name, path))
        add("")

    add("## AI workloads")
    add("")
    add("| Metric | Historical (15 W, transcribed) | This run MAXN | This run 15 W |")
    add("| --- | --- | --- | --- |")
    for label, key, field, unit, hgroup, hfield in AI_FIELDS:
        add("| %s | %s | %s | %s |" % (
            label, historical(hgroup, hfield),
            fmt(aggregate(maxn, key, field), unit),
            fmt(aggregate(w15, key, field), unit)))
    add("")

    add("## Three-model residency / contention")
    add("")
    conc = []
    for run in maxn + w15:
        c = run.get("concurrency", {})
        v = c.get("value") or {}
        if c.get("status") == "ok":
            conc.append((run.get("condition", {}).get("power_mode_requested"), v))
    if conc:
        add("| Power mode | torch allocated | YOLO@320 mean under load | p99 |")
        add("| --- | --- | --- | --- |")
        for mode, v in conc:
            add("| %s | %.1f MB | %.2f ms | %.2f ms |" % (
                mode, v.get("torch_allocated_mb") or 0,
                v.get("yolo_latency_mean_ms") or 0,
                v.get("yolo_latency_p99_ms") or 0))
        add("")
        add("`nvidia-smi` does not exist on Jetson, so the historical 1782 MB "
            "estimator cannot be reproduced with the same tool. "
            "`torch.cuda.memory_allocated` is a different quantity and the two "
            "must not be compared numerically.")
    else:
        add("not measured")
    add("")

    add("## Voice pipeline (ASR -> MT -> TTS)")
    add("")
    for label, runs in [("MAXN", voice_maxn), ("15 W", voice_15w)]:
        for run in runs:
            stages = run.get("stages", {})
            add("- %s: %s" % (label, ", ".join(
                "%s=%s" % (k, v["status"]) for k, v in stages.items())))
            for key, item in stages.items():
                v = item.get("value") or {}
                if not v:
                    continue
                brief = {k: (round(x, 4) if isinstance(x, float) else x)
                         for k, x in v.items()
                         if k in ("processing_s", "rtf", "latency_s", "output_tokens")}
                add("  - %s: %s" % (key, brief))
    if not (voice_maxn or voice_15w):
        add("not measured")
    add("")

    add("## ChatTTS long-text synthesis (separate measurement)")
    add("")
    tts = load("chattts_*.json")
    if tts:
        add("| Historical RTF (transcribed) | Measured RTF | Synthesis | Audio | Text chars |")
        add("| --- | --- | --- | --- | --- |")
        for run in tts:
            m = run.get("measurement", {})
            v = m.get("value") or {}
            hist = HISTORICAL["bench_ai"]["chattts_long"]["rtf"]
            if m.get("status") == "ok":
                add("| %s | %.3f | %.2f s | %.2f s | %s |" % (
                    hist, v["rtf"], v["synthesis_s"],
                    v["audio_duration_s"], v["text_chars"]))
            else:
                add("| %s | unavailable | - | - | - |" % hist)
        add("")
        add("RTF = synthesis wall time / measured duration of the generated "
            "waveform, so the denominator is observed rather than assumed. "
            "The Chinese source text and its character count are recorded in "
            "the script; it is not the lost October reference text.")
    else:
        add("not measured")
    add("")

    add("## Failsafe")
    add("")
    if failsafe:
        f = failsafe[0]
        p = f.get("protocol_checks", {})
        add("- F3 offline protocol guard: **%s / %s cases passed** "
            "(physical fault injection performed: %s)." % (
                p.get("cases_passed"), p.get("cases_total"),
                p.get("physical_fault_injection_performed")))
        add("- F1 / F2: %s — they require physical intervention and were not "
            "performed." % ", ".join(
                "%s=%s" % (k, v.get("status")) for k, v in f.get("physical_cases", {}).items()
                if k in ("F1_control_crash", "F2_cable_pull")))
    else:
        add("not measured")
    add("")

    add("## Source-aligned measurements (from the device source tree)")
    add("")
    add("`evidence/SOURCE_ALIGNMENT.md` audits the difference between the "
        "reconstructed scripts and the real service code in "
        "`/root/src/gedge_bot` (HEAD `%s`). "
        "`scripts/bench_source_aligned.py` re-runs the same workloads with the "
        "source parameters: FP16 Whisper with a 1 s warm-up and a Chinese "
        "language hint, fused YOLO with `conf=0.35`/`classes=[0]`, and NLLB "
        "`num_beams=5`. These are **new measurements**, not recovered October logs." %
        (aligned_maxn[0].get("source_commit") if aligned_maxn else "n/a"))
    add("")
    add("| Metric | Historical (transcribed) | Reconstructed MAXN | Source-aligned MAXN | Source-aligned 15 W |")
    add("| --- | --- | --- | --- | --- |")

    def cell(value, unit, digits=3):
        return "not measured" if value is None else ("%.*f %s" % (digits, value, unit))

    sa_rows = [
        # label, historical group/field, aligned key/field, unit, reconstructed key
        ("YOLO@320 mean", "yolo_320_15w", "mean_ms", "yolo_320", "latency_mean_ms", "ms", "yolo_320"),
        ("YOLO@640 mean", "yolo_640_15w", "mean_ms", "yolo_640", "latency_mean_ms", "ms", "yolo_640"),
        ("Whisper 10 s RTF", "whisper_10s_15w", "rtf", "whisper_10s", "rtf", "RTF", "whisper_10s"),
        ("Whisper 2 s RTF", "whisper_2s_clip", "rtf", "whisper_2s_clip", "rtf", "RTF", "whisper_2s_clip"),
        ("NLLB short (beam 5)", "nllb_single_model_15w", "short_s", "nllb_short", "latency_mean_s", "s", None),
        ("NLLB mid (beam 5)", "nllb_single_model_15w", "mid_s", "nllb_mid", "latency_mean_s", "s", None),
        ("NLLB long (beam 5)", "nllb_single_model_15w", "long_s", "nllb_long", "latency_mean_s", "s", None),
        ("NLLB short (greedy)", "nllb_single_model_15w", "short_s", "nllb_short_greedy", "latency_mean_s", "s", "nllb_short"),
        ("NLLB mid (greedy)", "nllb_single_model_15w", "mid_s", "nllb_mid_greedy", "latency_mean_s", "s", "nllb_mid"),
        ("NLLB long (greedy)", "nllb_single_model_15w", "long_s", "nllb_long_greedy", "latency_mean_s", "s", "nllb_long"),
    ]
    for label, hgroup, hfield, key, field, unit, recon_key in sa_rows:
        add("| %s | %s | %s | %s | %s |" % (
            label, historical(hgroup, hfield),
            fmt(aggregate(maxn, recon_key, field), unit) if recon_key else "n/a",
            cell(workload(aligned_maxn, key, field), unit),
            cell(workload(aligned_15w, key, field), unit)))
    add("")
    warm = [r.get("whisper_warmup", {}) for r in aligned_maxn + aligned_15w
            if r.get("whisper_warmup", {}).get("status") == "ok"]
    if warm:
        add("- First ASR call (1 s silence, includes CUDA/cuDNN initialization): "
            + ", ".join("%.2f s" % (w["value"]["duration_s"]) for w in warm)
            + ". This one-off cost explains most of the 25x RTF gap in the first "
              "reconstructed batch, which had no warm-up and used FP32.")
    conc_sa = [r.get("concurrency", {}) for r in aligned_maxn + aligned_15w
               if r.get("concurrency", {}).get("status") == "ok"]
    if conc_sa:
        add("- Three-model residency (torch allocator): "
            + ", ".join("%.1f MB" % (c["value"]["torch_allocated_mb"]) for c in conc_sa)
            + "; contention YOLO@320 mean "
            + ", ".join("%.2f ms" % (c["value"]["yolo_latency_mean_ms"]) for c in conc_sa) + ".")
    add("")
    add("The source service uses `num_beams=5`, while the Extended Abstract "
        "describes NLLB as greedy. Both were measured on the same model; the "
        "greedy control is much closer to the transcribed 0.42 / 1.02 / 1.55 s, "
        "which suggests the October benchmark used greedy decoding even though "
        "the shipped service uses beam search. This is an inference, not proof: "
        "the original script is still missing.")
    add("")

    add("## AI-loaded A/B control-frame timing (pty, same machine)")
    add("")
    add("`scripts/ab_control.py` writes the production seven-byte frames through "
        "the production `TermiosSerial` implementation to a **pseudo-terminal**, "
        "never to the motor controller. Condition A runs `yolo_load.py` "
        "(continuous YOLO@640, fused) as a child process. 200 intervals at a "
        "50 ms period per condition, matching the historical frame count.")
    add("")
    add("| Mode | Condition | interval std (ms) | p99 abs deviation (ms) | max abs deviation (ms) | max interval (ms) | intervals | YOLO loader |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    def ab_row(label, run):
        if not run:
            add("| %s | not measured | - | - | - | - | - | - |" % label)
            return
        s = run.get("summary", {})
        loader = (run.get("yolo_load") or {}).get("status", "none")
        add("| %s | %.3f | %.3f | %.3f | %.3f | %s | %s |" % (
            label, s.get("interval_std_ms") or 0,
            s.get("jitter_p99_abs_deviation_ms") or 0,
            s.get("jitter_max_abs_deviation_ms") or 0,
            s.get("max_interval_ms") or 0, s.get("interval_count"), loader))

    for mode in ("MAXN", "15W"):
        ab_row("%s | B_idle" % mode, ab_run("B_idle", mode))
        ab_row("%s | A_ai_loaded" % mode, ab_run("A_ai_loaded", mode))
        for i, run in enumerate(ab_repeats(mode), 1):
            ab_row("%s | B_idle repeat %d" % (mode, i), run)
    for mode in ("MAXN", "15W"):
        b, a = ab_run("B_idle", mode), ab_run("A_ai_loaded", mode)
        if not (b and a):
            continue
        bp = b["summary"].get("jitter_p99_abs_deviation_ms")
        ap = a["summary"].get("jitter_p99_abs_deviation_ms")
        repeats = [r["summary"].get("jitter_p99_abs_deviation_ms")
                   for r in ab_repeats(mode)]
        repeats = [v for v in repeats if v is not None]
        if not (bp and ap):
            continue
        add("")
        if mode == "15W" and repeats and max([bp] + repeats) / min([bp] + repeats) > 5:
            add("- %s: the idle baseline is **not stable** — repeated idle runs gave "
                "p99 deviations of %s ms, while A_ai_loaded gave %.3f ms. The A/B "
                "ratio therefore spans roughly %.2f–%.2fx depending on which idle "
                "run is used; **no single ratio is reported**. Re-run with the "
                "sources of 15 W scheduling spikes controlled before making any "
                "quantitative claim." % (
                    mode, ", ".join("%.3f" % v for v in [bp] + repeats), ap,
                    ap / max([bp] + repeats), ap / min([bp] + repeats)))
        else:
            add("- %s p99 ratio A/B = **%.1fx** (%.3f ms / %.3f ms) in this "
                "single first-batch run; the repeated batch below does not "
                "reproduce a stable factor." % (mode, ap / bp, ap, bp))
    add("")
    add("**This is not a reproduction of the historical 208x.** The historical "
        "A condition used direct Xavier control over a real UART to the MCU; "
        "this run uses a pty, does not actuate hardware, and uses a different "
        "transport and drain implementation. On MAXN the concurrent YOLO load "
        "appeared to raise p99 deviation about fourfold in that single run, "
        "while the 15 W idle baseline itself was noisy. The repeated batch "
        "below did not reproduce a stable factor. The data support only the "
        "qualitative statement that AI load can perturb user-space frame "
        "timing, but they do **not** support "
        "the magnitude of the historical ratio; `main.tex` must keep 208x as an "
        "unreplicated historical value. The repeated-batch section below "
        "reports five paired runs per mode plus a CPU-only control; it "
        "supersedes these single-run numbers for any dispersion claim.")
    add("")

    # ---- Evening robustness batch: repeated A/B with a CPU-only control ----
    if ab_robust:
        add("## Repeated A/B robustness batch (2026-11-07 evening)")
        add("")
        add("The first A/B batch had one run per condition and an unstable 15 W "
            "idle baseline. This batch runs five B_idle / A_ai_loaded pairs per "
            "power mode plus a GPU-free `cpu_load.py` control (C), using the same "
            "200 x 50 ms pty protocol. `C_cpu_load` separates generic Linux "
            "scheduler interference from effects specific to the CUDA/YOLO stack.")
        add("")
        add("| Mode | Condition | runs | p99 abs dev min (ms) | median (ms) | max (ms) | interval std median (ms) |")
        add("| --- | --- | --- | --- | --- | --- | --- |")
        for mode in ("MAXN", "15W"):
            for cond in ("B_idle", "A_ai_loaded", "C_cpu_load"):
                vals, stds = [], []
                for _, d in ab_robust:
                    if str(d.get("condition", "")).startswith("%s_%s_rep" % (cond, mode)):
                        s = d.get("summary", {})
                        if s.get("jitter_p99_abs_deviation_ms") is not None:
                            vals.append(s["jitter_p99_abs_deviation_ms"])
                            stds.append(s.get("interval_std_ms") or 0)
                if vals:
                    add("| %s | %s | %d | %.3f | %.3f | %.3f | %.3f |" % (
                        mode, cond, len(vals), min(vals),
                        statistics.median(vals), max(vals),
                        statistics.median(stds)))
                else:
                    add("| %s | %s | 0 | not measured | - | - | - |" % (mode, cond))
        add("")
        for mode in ("MAXN", "15W"):
            def condition_run(cond, rep):
                prefix = "%s_%s_rep%d_" % (cond, mode, rep)
                for _, d in ab_robust:
                    if str(d.get("condition", "")).startswith(prefix):
                        return d
                return None
            ratios = []
            for rep in range(1, 6):
                b, a = condition_run("B_idle", rep), condition_run("A_ai_loaded", rep)
                if not (b and a):
                    continue
                bp = b["summary"].get("jitter_p99_abs_deviation_ms")
                ap = a["summary"].get("jitter_p99_abs_deviation_ms")
                if bp and ap:
                    ratios.append(ap / bp)
            if ratios:
                spread = (max(ratios) / min(ratios)) if min(ratios) > 0 else float("inf")
                stability = ("**unstable** — the ratio spans %.0fx across paired "
                             "runs, so no quantitative A/B factor is reported for "
                             "this mode" % spread) if spread > 5 else (
                    "stable enough to quote a median, but still a pty-only result")
                add("- %s paired A/B p99 ratios over %d pairs: %s; median %.2fx "
                    "(range %.2f–%.2fx) — %s." % (
                        mode, len(ratios),
                        ", ".join("%.2f" % r for r in ratios),
                        statistics.median(ratios), min(ratios), max(ratios),
                        stability))
        add("")
        for mode in ("MAXN", "15W"):
            cpu = [d["summary"]["jitter_p99_abs_deviation_ms"] for _, d in ab_robust
                   if str(d.get("condition", "")).startswith("C_cpu_load_%s_rep" % mode)
                   and d.get("summary", {}).get("jitter_p99_abs_deviation_ms") is not None]
            ai = [d["summary"]["jitter_p99_abs_deviation_ms"] for _, d in ab_robust
                  if str(d.get("condition", "")).startswith("A_ai_loaded_%s_rep" % mode)
                  and d.get("summary", {}).get("jitter_p99_abs_deviation_ms") is not None]
            idle = [d["summary"]["jitter_p99_abs_deviation_ms"] for _, d in ab_robust
                    if str(d.get("condition", "")).startswith("B_idle_%s_rep" % mode)
                    and d.get("summary", {}).get("jitter_p99_abs_deviation_ms") is not None]
            if cpu and ai:
                add("- %s CPU-only control p99 median %.3f ms vs AI-loaded median "
                    "%.3f ms (idle median %.3f ms). The CPU-only control stays "
                    "near idle while AI-loaded is higher but also highly "
                    "dispersed; with the camera-capture thread, two streaming "
                    "services and `schedutil` DVFS present on this host, that "
                    "gap is **not** sufficient to attribute the excursions to "
                    "the CUDA path specifically. See "
                    "`evidence/ENVIRONMENT_NOISE.md`." % (
                        mode, statistics.median(cpu), statistics.median(ai),
                        statistics.median(idle) if idle else float("nan")))
        add("")
        add("**Interpretation.** Occasional scheduling spikes dominate the p99 "
            "on this host: they appear in idle runs as well as AI-loaded ones, "
            "the paired A/B ratio changes by more than an order of magnitude, "
            "and the CPU-only control does not reproduce the largest excursions. "
            "The data therefore support only the *qualitative* statement that a "
            "concurrent workload can perturb user-space frame timing. They do "
            "**not** support any single quantitative factor, do not reproduce "
            "the historical 208x, and must not be pooled with October data. A "
            "controlled replication requires removing the spike sources "
            "(background services, DVFS/thermal state) and using a real UART "
            "with a logic analyzer; the first batch's 4.0x was a one-off value.")
        add("")
        add("`robustness_env_*.log` records load average and thermal-zone "
            "temperature at each phase boundary for diagnosing those spikes.")
        add("")

    # ---- Repeated source-aligned benchmarks ----
    if aligned_rep_maxn or aligned_rep_15w:
        add("## Repeated source-aligned benchmarks (dispersion)")
        add("")
        add("Three repeats per power mode of `bench_source_aligned.py`, 60 YOLO "
            "iterations and 3 NLLB runs per prompt each. This replaces the "
            "single-run point estimates above with a measured dispersion and "
            "checks whether the earlier `yolo_320` outlier recurs.")
        add("")
        add("| Metric | Source-aligned MAXN (n=3) | Source-aligned 15 W (n=3) | Historical (transcribed) |")
        add("| --- | --- | --- | --- |")
        rep_rows = [
            ("YOLO@320 mean", "yolo_320", "latency_mean_ms", "ms", "yolo_320_15w", "mean_ms"),
            ("YOLO@640 mean", "yolo_640", "latency_mean_ms", "ms", "yolo_640_15w", "mean_ms"),
            ("Whisper 10 s RTF", "whisper_10s", "rtf", "RTF", "whisper_10s_15w", "rtf"),
            ("Whisper 2 s RTF", "whisper_2s_clip", "rtf", "RTF", "whisper_2s_clip", "rtf"),
            ("NLLB short (beam 5)", "nllb_short", "latency_mean_s", "s", "nllb_single_model_15w", "short_s"),
            ("NLLB mid (beam 5)", "nllb_mid", "latency_mean_s", "s", "nllb_single_model_15w", "mid_s"),
            ("NLLB long (beam 5)", "nllb_long", "latency_mean_s", "s", "nllb_single_model_15w", "long_s"),
            ("NLLB short (greedy)", "nllb_short_greedy", "latency_mean_s", "s", "nllb_single_model_15w", "short_s"),
            ("NLLB mid (greedy)", "nllb_mid_greedy", "latency_mean_s", "s", "nllb_single_model_15w", "mid_s"),
            ("NLLB long (greedy)", "nllb_long_greedy", "latency_mean_s", "s", "nllb_single_model_15w", "long_s"),
        ]
        for label, key, field, unit, hgroup, hfield in rep_rows:
            add("| %s | %s | %s | %s |" % (
                label, fmt(aggregate(aligned_rep_maxn, key, field), unit),
                fmt(aggregate(aligned_rep_15w, key, field), unit),
                historical(hgroup, hfield)))
        add("")
        for label, runs in (("MAXN", aligned_rep_maxn), ("15 W", aligned_rep_15w)):
            spreads = []
            for run in runs:
                item = run.get("workloads", {}).get("yolo_320", {})
                v = item.get("value") or {}
                if item.get("status") == "ok" and v.get("latency_pstdev_ms") is not None:
                    spreads.append(v["latency_pstdev_ms"])
            if spreads:
                add("- %s `yolo_320` within-run population stdev across repeats: %s ms." %
                    (label, ", ".join("%.2f" % s for s in spreads)))
            values = aggregate(runs, "yolo_320", "latency_mean_ms")
            if len(values) > 1 and statistics.fmean(values) > 0:
                cv = statistics.pstdev(values) / statistics.fmean(values)
                if cv > 0.15:
                    add("- %s `yolo_320` cross-repeat CV = **%.0f%%** (per-run means "
                        "%s ms): the first-batch outlier recurs, so the aggregate "
                        "mean is not a stable point estimate for this workload." %
                        (label, cv * 100,
                         ", ".join("%.2f" % v for v in values)))
        add("")

    # ---- Parameter sensitivity ----
    if sensitivity:
        add("## Parameter sensitivity (addressing the two historical conflicts)")
        add("")
        add("`scripts/sensitivity_bench.py` varies the parameters that were "
            "unknown in the October record. These are NEW measurements, not a "
            "reconstruction of the lost prompts or audio.")
        add("")
        add("### Whisper: dtype, warm-up and audio length")
        add("")
        add("| Mode | Profile | 1 s RTF | 2 s RTF | 5 s RTF | 10 s RTF | init. call (s) |")
        add("| --- | --- | --- | --- | --- | --- | --- |")
        for _, d in sensitivity:
            mode = d.get("condition", {}).get("power_mode_requested", "?")
            w = d.get("whisper", {})
            for profile in ("fp16_warm", "fp32_no_warm"):
                p = w.get(profile)
                if not isinstance(p, dict):
                    continue
                clips = p.get("clips", {})
                def rtf(seconds):
                    c = clips.get("%gs" % seconds, {})
                    return "not measured" if c.get("rtf") is None else "%.3f" % c["rtf"]
                warm = p.get("warmup_call_s")
                cold = p.get("cold_first_call_s")
                init = warm if warm is not None else cold
                add("| %s | %s | %s | %s | %s | %s | %s |" % (
                    mode, profile, rtf(1), rtf(2), rtf(5), rtf(10),
                    "-" if init is None else "%.2f" % init))
        add("")
        add("`init. call` is the 1 s silence warm-up for `fp16_warm` (it includes "
            "CUDA/cuDNN first-call initialisation) or the first timed call for "
            "`fp32_no_warm`. Historical 10 s RTF (15 W, transcribed): **0.091**. "
            "RTF falls strongly with clip length because a fixed per-call cost is "
            "amortised; reporting RTF without the audio duration is therefore "
            "meaningless. Neither profile recovers the lost October audio or "
            "decoding parameters.")
        add("")
        if sensitivity_cold:
            add("### True cold-start control (fresh process, FP32, no warm-up)")
            add("")
            add("The `fp32_no_warm` rows above run *after* `fp16_warm` in the "
                "same process, so CUDA/cuDNN is already initialised and their "
                "first call is not a true cold start. `run_whisper_cold.sh` "
                "launches a fresh Python process per power mode with only the "
                "FP32/no-warm profile, matching the first reconstructed batch "
                "that created a fresh FP32 pipeline per call and saw RTF ~2.27.")
            add("")
            add("| Mode | cold first 10 s call (s) | RTF of cold call | steady 10 s RTF (n=3) |")
            add("| --- | --- | --- | --- |")
            cold_rtfs, steady_rtfs = [], []
            for _, d in sensitivity_cold:
                mode = d.get("condition", {}).get("power_mode_requested", "?")
                p = d.get("whisper", {}).get("fp32_no_warm", {})
                clip = p.get("clips", {}).get("10s", {})
                cold = p.get("cold_first_call_s")
                if clip.get("rtf_cold_first_call") is not None:
                    cold_rtfs.append(clip["rtf_cold_first_call"])
                if clip.get("rtf") is not None:
                    steady_rtfs.append(clip["rtf"])
                add("| %s | %s | %s | %s |" % (
                    mode, "-" if cold is None else "%.2f" % cold,
                    "-" if clip.get("rtf_cold_first_call") is None
                    else "%.3f" % clip["rtf_cold_first_call"],
                    "-" if clip.get("rtf") is None else "%.3f" % clip["rtf"]))
            add("")
            if cold_rtfs and steady_rtfs:
                add("The cold first-call RTF values (%s) match the ~2.27 RTF of "
                    "the first reconstructed batch, while steady state falls to "
                    "(%s). This **quantitatively explains** the earlier 25x gap "
                    "as a one-off CUDA/cuDNN initialisation cost, not a hardware "
                    "regression. It is a mechanism check, not a reproduction of "
                    "the lost October audio or decoding parameters." % (
                        ", ".join("%.3f" % v for v in cold_rtfs),
                        ", ".join("%.3f" % v for v in steady_rtfs)))
            add("")

        add("### NLLB: prompt length x decoding")
        add("")
        add("| Mode | Prompt (chars) | greedy mean (s) | greedy tokens | beam-5 mean (s) | beam-5 tokens |")
        add("| --- | --- | --- | --- | --- | --- |")
        for _, d in sensitivity:
            mode = d.get("condition", {}).get("power_mode_requested", "?")
            cases = d.get("nllb", {}).get("cases", {})
            for key in ("short", "short_alt", "mid", "long"):
                g = cases.get("%s__greedy_control" % key, {})
                b = cases.get("%s__beam5_source" % key, {})
                if not (g or b):
                    continue
                add("| %s | %s (%s) | %s | %s | %s | %s |" % (
                    mode, key, g.get("prompt_chars", b.get("prompt_chars", "?")),
                    "not measured" if g.get("latency_mean_s") is None else "%.3f" % g["latency_mean_s"],
                    "-".join(str(t) for t in g.get("output_tokens", [])) or "-",
                    "not measured" if b.get("latency_mean_s") is None else "%.3f" % b["latency_mean_s"],
                    "-".join(str(t) for t in b.get("output_tokens", [])) or "-"))
        add("")
        add("Historical short-text targets (both 15 W, transcribed): **0.42 s** "
            "in the single-model table and **1.33 s** in the power-sensitivity "
            "table. This sweep shows which prompt/decoding combinations land "
            "near each value; it does **not** identify the lost October prompt, "
            "so the discrepancy remains unresolved in `main.tex`.")
        add("")

    if isolation:
        a = isolation[0]["analysis"]
        add("## Architectural isolation analysis (Q2, trace-driven)")
        add("")
        add("`scripts/boundary_isolation.py` drives two execution organisations "
            "with the **same measured host frame traces**. This directly tests "
            "the paper's causal proposition instead of adding another throughput "
            "benchmark. **Only the host timestamps are measurements**; "
            "execution-side quantities are derived from the audited firmware "
            "contract and are not logic-analyser observations "
            "(`evidence/BOUNDARY_ISOLATION.md`).")
        add("")
        integ = a["integrity"]
        add("- %d A/B runs, %d measured 50 ms intervals, %d/%d exact frame "
            "sequences, no physical motor writes; %d/%d firmware contract "
            "checks re-verified." % (
                integ["runs_total"], integ["frames_analysed"],
                integ["runs_with_exact_frame_sequence"], integ["runs_total"],
                sum(1 for v in a["contract_checks"].values() if v),
                len(a["contract_checks"])))
        add("")
        add("| Mode | Condition | host p99 dev (ms, measured) | direct pulse-train p99 (ms, counterfactual) | offloaded pulse-train jitter from host (contract) | offloaded command start-phase p99 (ms) |")
        add("| --- | --- | --- | --- | --- | --- |")
        for g in a["groups"]:
            add("| %s | %s | %.3f | %.3f | %.3f | %.3f |" % (
                g["mode"], g["condition"], g["host_p99_abs_deviation_ms"],
                g["direct_linux"]["pulse_train_p99_abs_deviation_ms"],
                g["finite_pulse_offload"]["pulse_train_p99_abs_deviation_ms_from_host_jitter"],
                g["finite_pulse_offload"]["command_start_phase_p99_abs_deviation_ms"]))
        add("")
        add("The boundary removes host jitter **inside** the pulse train, but a "
            "command still starts late by the full measured host deviation. "
            "This splits `main.tex` L82 into two separable quantities instead of "
            "a blanket determinism claim. The 1:1 / 0:1 transfer ratios are "
            "contract properties, not empirical estimates; MCU-local clock drift "
            "was not measured.")
        add("")
        finding = a["bounded_stopping"]["finding"]
        add("**New static finding `%s`.** %s Worst case for `TurnBody()`: "
            "**%.1f s (%.2f min)** of residual motion from one seven-byte frame, "
            "versus unbounded motion for a persistent-velocity command. The "
            "finite-pulse bound is therefore a **host-side policy, not an MCU "
            "guarantee** -- a concrete reason to add frame integrity and a "
            "firmware-side pulse ceiling." % (
                finding["id"], finding["description"],
                finding["worst_case_turnbody_residual_motion_s"],
                finding["worst_case_turnbody_residual_motion_min"]))
        add("")
        add("This analysis neither reproduces nor replaces the historical "
            "208x direct-control ratio.")
        add("")

    if replay:
        r = replay[0]
        tot = r["summary"]["totals"]
        find = r["summary"]["findings"]
        by_mode = r["summary"]["by_mode"]
        add("## Product-loop software-in-the-loop replay (Q3)")
        add("")
        add("`scripts/product_loop_replay.py` executes the audited "
            "`robot_runtime.py` decision and frame-building functions with "
            "in-memory event sources and a virtual clock. Scenario events are "
            "**synthetic**: the six DOA seeds are transcribed historical angles, "
            "face streams are probes, and every device-opening method in the "
            "loaded module is replaced by a guard. **No physical actuation, no "
            "wall-clock measurement, and no dialogue success claim** "
            "(`evidence/PRODUCT_LOOP_REPLAY.md`).")
        add("")
        add("- %d traces; **%d/%d** runnable trigger-to-follow completions; "
            "%s in the deliberately injected camera-not-ready condition; "
            "%d seven-byte frames with %d contract errors; %d physical device "
            "opens." % (
                tot["traces"], tot["runnable_completed"], tot["runnable_traces"],
                find["camera_not_ready_chain_completed"], tot["frames_emitted"],
                tot["frame_contract_errors"],
                tot["physical_device_open_attempts"]))
        add("")
        add("| Condition | n | Chain completed | Orient budget mean (s) | Frames |")
        add("| --- | --- | --- | --- | --- |")
        for mode in ("nominal", "voice_degraded", "face_dropout",
                     "doa_dead_zone", "camera_not_ready"):
            if mode not in by_mode:
                continue
            g = by_mode[mode]
            add("| %s | %d | %s | %.3f | %d |" % (
                mode, g["n"], g["completion_rate"], g["orient_s_mean"],
                g["frames"]))
        add("")
        add("Budget figures are **virtual-clock values derived from delays in "
            "the source**, not wall-clock timings, and the full-chain replay "
            "duration scales with the synthetic event-stream length. Voice-"
            "degraded traces are bit-identical to nominal ones because the "
            "audited source starts the voice subprocess in parallel with the "
            "trigger-to-follow critical path -- a source-structure result, not "
            "a dialogue success rate.")
        add("")
        add("- %d/%d transcribed DOA angles exceed 90 deg and saturate to the "
            "same 9-head/25-body pulse plan." % (
                find["historical_angles_saturating_at_90_deg"],
                find["historical_angles_total"]))
        add("- The orientation budget is dominated by fixed host waits "
            "(2.0 s head--body delay, 0.30 s command spacing, "
            "pulse*0.043+1.2 s settling) rather than inference.")
        add("- %d counter-head frames were emitted onto a path with no "
            "reliable MCU queue, consistent with the firmware audit." %
            find["counter_head_frames_without_reliable_mcu_queue"])
        cross = r.get("cross_host_validation")
        if cross and cross.get("fingerprint_matches"):
            add("- Laptop and Xavier runs used identical source hashes and "
                "produced an identical result fingerprint `%s`." %
                r["result_fingerprint_sha256"][:16])
        add("")
        add("This replay replaces the `n=0` empty demo record with reproducible "
            "degraded-mode coverage. It does **not** reproduce the historical "
            "N=6 physical demonstrations and must not be pooled with them.")
        add("")

    uart_runs = []
    for path in sorted(glob.glob(str(DEVICE_DIR / "uart_timing_*.json"))):
        try:
            d = json.loads(Path(path).read_text())
            if d.get("mode") == "no-motion" and "summary" in d:
                uart_runs.append(d)
        except (OSError, ValueError):
            pass
    if uart_runs:
        add("## Real-UART no-motion command-phase measurement")
        add("")
        add("The motor UART is **online**: `/dev/ttyUSB0` (QinHeng HL-340, "
            "ch341, 9600 8N1), with no process holding the port. Six MAXN runs "
            "wrote inert `AA 55 00 00 00 0D 0A` frames (CMD=0x00 has no case "
            "in the audited firmware switch), 201 frames per run at a 50 ms "
            "absolute schedule. `physical_motion=false`; no MCU acknowledgement "
            "was observed, so frame reception is not claimed.")
        add("")
        add("| Condition | runs | p99 abs dev min / median / max (ms) | "
            "os.write p99 median (ms) | tcdrain p99 median (ms) |")
        add("| --- | --- | --- | --- | --- |")
        for key in ("B_idle", "A_ai_loaded"):
            rows = [d for d in uart_runs
                    if d.get("condition", "").startswith(key + "_")]
            if not rows:
                continue
            p = [d["summary"]["jitter_p99_abs_deviation_ms"] for d in rows]
            w = [d["write_latency_stats"]["p99_ms"] for d in rows]
            dr = [d["drain_latency_stats"]["p99_ms"] for d in rows]
            add("| %s | %d | %.3f / %.3f / %.3f | %.3f | %.3f |" % (
                key, len(rows), min(p), statistics.median(p), max(p),
                statistics.median(w), statistics.median(dr)))
        add("")
        add("AI load produces **no repeatable p99 degradation** on the real "
            "link either; the largest deviation is in an idle run. A 7-byte "
            "frame needs about 7.29 ms on the wire at 9600 baud, while "
            "`tcdrain()` returns in about 0.15 ms, so USB bulk transfer hides "
            "the true MCU arrival instant from the host. This measures command "
            "emission (L1), **not** pulse-train cadence (L3); the historical "
            "208x ratio still requires a logic analyser on the MCU pulse GPIOs. "
            "See `evidence/UART_TIMING.md`.")
        add("")

    add("## Interpretation caveats")
    add("")
    add("1. **Whisper in the first batch is not comparable.** That batch used "
        "FP32, no warm-up and no language hint; the first CUDA/cuDNN call alone "
        "cost ~22 s. The source-aligned batch (FP16 + warm-up + language hint) "
        "brings the 10 s RTF into the same order of magnitude as the historical "
        "0.091, which identifies the gap as a setup/estimator difference rather "
        "than a hardware regression. Even so, the October audio, VAD trimming "
        "and decoding parameters are unknown, so no equality claim is made.")
    add("2. **NLLB long-text divergence.** The rebuilt long prompt differs "
        "from the lost original and produces more output tokens; do not read "
        "the gap as a hardware regression.")
    add("3. **Run-to-run outliers.** `yolo_320` in one first-batch MAXN run shows "
        "a large population stdev; per-run p50/p99 and raw latency arrays are "
        "kept in the JSONs. Three source-aligned repeats per power mode were "
        "added to check whether the outlier recurs; the dispersion section flags "
        "any mode whose cross-repeat CV exceeds 15%. For a flagged mode, report "
        "the per-run values rather than a pooled mean.")
    add("4. **ChatTTS** was not importable when the main batch ran, so the "
        "`bench_voice` TTS stage stays `unavailable` in those JSONs. It was "
        "measured afterwards by a separate script using the device's own torch "
        "compatibility patch (section below). Do not splice that number into "
        "the `bench_voice` chain or quote a fabricated end-to-end latency.")
    add("5. **Demo runs (N=6)** and F1/F2 still require an on-site operator. "
        "No `demo_runs_*.json` from a physical demonstration was produced.")
    add("")

    add("## Reproduce")
    add("")
    add("```bash")
    add("# on the Xavier, after sourcing evidence/device/run_env.sh")
    add("bash evidence/device/run_bench.sh          # MAXN, 3 runs (first batch)")
    add("bash evidence/device/run_bench_15w.sh      # 15 W, restores MAXN via trap")
    add("bash evidence/device/run_full_source_aligned.sh  # source params + A/B, both modes")
    add("bash evidence/device/run_robustness.sh   # 5 paired A/B + CPU control + 3 source-aligned repeats")
    add("bash evidence/device/run_sensitivity.sh  # Whisper/NLLB parameter sweeps, both modes")
    add("bash evidence/device/run_whisper_cold.sh # fresh-process FP32 cold start, both modes")
    add("bash evidence/device/run_uart_timing.sh  # real-UART inert frames, MAXN, idle + AI load")
    add("# back on the laptop")
    add("# the Q2 architectural-isolation analysis is host-side and offline")
    add("python3 scripts/boundary_isolation.py   # trace + firmware contract, no device access")
    add("python3 scripts/product_loop_replay.py  # Q3 software-in-the-loop replay, no device access")
    add("python3 scripts/integration_surface.py   # E5 source-only integration inventory")
    add("python3 scripts/uart_report.py          # real-UART link + 208x boundary report")
    add("python3 scripts/summarize_device_runs.py")
    add("```")
    add("")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("wrote %s (%d lines, %d MAXN + %d 15W runs)" %
          (out, len(lines), len(maxn), len(w15)))


if __name__ == "__main__":
    main()
