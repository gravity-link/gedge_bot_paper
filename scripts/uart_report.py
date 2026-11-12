#!/usr/bin/env python3
"""Generate evidence/UART_TIMING.md from real-UART and pty timing JSONs."""
import argparse
import glob
import json
import os
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICE_DIR = ROOT / "evidence" / "device"
DEFAULT_MD = ROOT / "evidence" / "UART_TIMING.md"


def load(pattern):
    out = []
    for path in sorted(glob.glob(str(DEVICE_DIR / pattern))):
        try:
            out.append((Path(path), json.loads(Path(path).read_text())))
        except (OSError, ValueError):
            continue
    return out


def p99(d):
    return d.get("summary", {}).get("jitter_p99_abs_deviation_ms")


def group_uart(files):
    groups = {"B_idle": [], "A_ai_loaded": []}
    for path, d in files:
        if d.get("mode") != "no-motion" or "summary" not in d:
            continue
        cond = d.get("condition", "")
        for key in groups:
            if cond.startswith(key + "_"):
                groups[key].append((path, d))
                break
    return groups


def group_pty(files):
    groups = {"B_idle": [], "A_ai_loaded": []}
    for path, d in files:
        cond = d.get("condition", "")
        if "_MAXN_rep" not in cond or p99(d) is None:
            continue
        for key in groups:
            if cond.startswith(key + "_"):
                groups[key].append((path, d))
                break
    return groups


def fmt(values, digits=3):
    if not values:
        return "not measured"
    return "%.3f / %.3f / %.3f" % (min(values), statistics.median(values), max(values))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default=str(DEFAULT_MD))
    args = ap.parse_args()

    uart = group_uart(load("uart_timing_*.json"))
    pty = group_pty(load("ab_control_*.json"))
    inventory = None
    for _, d in load("uart_link_inventory_*.json"):
        inventory = d.get("link_inventory")
        break

    lines = []
    add = lines.append
    add("# 真实 UART 链路测量与 208× 未复现的边界")
    add("")
    add("生成：`python3 scripts/uart_report.py`。数据来源：`evidence/device/uart_timing_*.json`、")
    add("`ab_control_*.json`、`uart_link_inventory_*.json`。")
    add("")
    add("> **一句话回答「UART 能连接，为什么还说未解决」**：UART 链路**一直在线**，")
    add("> 本轮也已在真实 `/dev/ttyUSB0` 上完成实测。但历史 $208\\times$ 比较的是")
    add("> **STM32 电机引脚上脉冲串的时序抖动**，UART 上只有一条 7 字节命令帧，")
    add("> 看不到任何脉冲。缺的不是「能连的串口」，而是**脉冲引脚上的波形捕获**")
    add("> 以及**运动态故障注入的现场安全条件**。此前把两者并列写成")
    add("> 「a real UART and a logic analyser」容易被读成串口也没连上，正文已改。")
    add("")

    add("## 1. 链路实测清单（2026-11-07，Xavier）")
    add("")
    add("| 项 | 实测值 |")
    add("| --- | --- |")
    add("| 电机串口 | `/dev/ttyUSB0`，QinHeng HL-340 (`1a86:7523`)，`ch341` 驱动，9600 8N1 |")
    add("| 另一 STM32 设备 | `/dev/ttyACM0`，CDC-ACM Virtual COM Port (`0483:5741`)，9600；源码未确认其用途 |")
    add("| 调试器 | ST-LINK/V2 (`0483:3748`) |")
    add("| 端口占用 | 无进程持有 `ttyUSB0`；`ttyTCU0`/`ttyTHS0` 仅被 getty 占用 |")
    add("| MCU 遥测 | 只读打开两端口各等 1.5 s，**读回 0 字节**；固件只在启动时输出 5 行横幅，无逐命令 ACK、无逐脉冲时间戳 |")
    if inventory:
        tools = inventory.get("logic_analyser_tools") or []
        add("| 逻辑分析仪工具 | %s |" % (", ".join(tools) if tools else "**无**（sigrok / pulseview / openocd / pyocd / st-util 均未安装）"))
    add("")

    add("## 2. 三层测量对象：UART 只到第 1 层")
    add("")
    add("| 层 | 物理对象 | 观测手段 | 本轮状态 |")
    add("| --- | --- | --- | --- |")
    add("| L1 命令帧发出 | 7 字节 `AA 55 CMD PULSE_L PULSE_H 0D 0A` | 主机 `os.write()` 时间戳 | **已测**：本轮真实 UART + 之前 pty A/B |")
    add("| L2 命令帧到达 MCU | USART1 RXNE 中断 / `rx_idx` | MCU 侧时间戳或 RX 线探针 | **不可测**：固件无 ACK；`os.write`/`tcdrain` 只到 USB 提交，不等于线缆发完 |")
    add("| L3 电机脉冲串 | `GPIOA12` / `GPIOC12` / `GPIOG10` 等 MCU 脉冲 GPIO | 逻辑分析仪 / 示波器 | **未测**：设备无采集工具；$208\\times$ 就在这一层 |")
    add("")
    add("历史 idle p99 为 0.24 ms，要分辨需不低于约 4.2 kS/s 的采样，实用上应取")
    add("$\\geq 10$~kS/s 并覆盖秒级窗口才能同时看到 49.93 ms 的尖峰。设备上的")
    add("ST-LINK/V2 只能通过 SWD 以百 Hz 量级轮询 GPIO 寄存器，既未安装 openocd，")
    add("精度也不足以测亚毫秒抖动，因此**不能替代逻辑分析仪**。")
    add("")

    add("## 3. 本轮新增：真实串口上的零运动命令起始相位")
    add("")
    add("`scripts/uart_timing.py` 在真实 `/dev/ttyUSB0` 上以 50 ms 周期发送")
    add("`AA 55 00 00 00 0D 0A`。固件 `ProcessCommand()` 只派发 `0x01`–`0x09`，")
    add("`CMD=0x00` 无匹配分支，**不会调用任何电机函数**；脚本运行前解析固件快照")
    add("确认这一前提，且需显式 `--confirm-physical-uart` 才允许写端口。")
    add("记录 `os.write()` 返回时刻与 `tcdrain()` 返回时刻，统计口径与")
    add("`ab_control.py` 完全一致（population stdev + nearest-rank p99 of $|t_i-50|$）。")
    add("")
    add("| 传输 | 条件 | 轮次 | p99 绝对偏差 min / median / max (ms) |")
    add("| --- | --- | --- | --- |")
    for label, groups in (("真实 UART `/dev/ttyUSB0`", uart), ("pty 对照（既有数据）", pty)):
        for key in ("B_idle", "A_ai_loaded"):
            vals = [p99(d) for _, d in groups[key] if p99(d) is not None]
            add("| %s | %s | %d | %s |" % (label, key, len(vals), fmt(vals)))
    add("")
    if any(uart[k] for k in uart):
        sample = (uart["B_idle"] or uart["A_ai_loaded"])[0][1]
        add("- 每轮发送 **%d** 帧，帧长 %d 字节；`physical_motion=%s`、" % (
            sample.get("frames_sent", 0), sample.get("frame_length_bytes", 0),
            str(sample.get("physical_motion")).lower()))
        add("  `mcu_ack_observed=%s`、`logic_analyser=%s`。" % (
            str(sample.get("mcu_ack_observed")).lower(),
            str(sample.get("logic_analyser")).lower()))
    add("")
    add("### 一个此前被 pty 掩盖的硬件事实")
    add("")
    write_p99, drain_p99 = [], []
    for key in ("B_idle", "A_ai_loaded"):
        for _, d in uart[key]:
            write_p99.append(d.get("write_latency_stats", {}).get("p99_ms"))
            drain_p99.append(d.get("drain_latency_stats", {}).get("p99_ms"))
    write_p99 = [v for v in write_p99 if v is not None]
    drain_p99 = [v for v in drain_p99 if v is not None]
    if write_p99 and drain_p99:
        add("7 字节帧在 9600 baud 下需 $7\\times10/9600 \\approx 7.29$~ms 才能在线缆上发完，")
        add("但 6 轮实测 `os.write()` p99 中位数只有 %.3f~ms、`tcdrain()` p99" % statistics.median(write_p99))
        add("中位数 %.3f~ms（最大 %.3f~ms）。也就是说 USB 批量传输把" % (
            statistics.median(drain_p99), max(drain_p99)))
        add("「主机提交」与「MCU 实际收到」之间隔了一层**主机不可观测的延迟**。")
    else:
        add("7 字节帧在 9600 baud 下需约 7.29~ms 才能在线缆上发完，而")
        add("`os.write()` / `tcdrain()` 只反映 USB 提交，不等于线缆发送完成。")
    add("因此在真实链路上，L1 的时间戳甚至不能当作命令到达时刻；这进一步")
    add("说明为什么 $208\\times$ 必须在 MCU 侧测量，而不能从主机侧推断。")
    add("")

    add("## 4. 仍未解决的项目与真正缺的条件")
    add("")
    add("| 项目 | 与 UART 链路的关系 | 真正缺的条件 |")
    add("| --- | --- | --- |")
    add("| $208\\times$ direct-control 比值 | **无关**：UART 只传命令，脉冲在 MCU GPIO 上 | 在 8 个脉冲脚接 $\\geq10$~kS/s 逻辑分析仪，在**同一 AI 负载**下同时记录 idle / AI-loaded 波形；或固件增加逐脉冲时间戳回传 |")
    add("| F1（发送停止帧） / F2（运动中关串口、拔线） | **无关**：需要电机**正在转动**才能验证「立即停止」 | 真实运动命令 `CMD 0x01`–`0x09` + 架起底盘/空旷场地 + 现场人员观察；零运动帧无法证伪「持续运动」 |")
    add("| N=6 端到端演示 | 链路已就绪 | 真实声源、摄像头、采集窗口与现场记录 |")
    add("| 云台反向补偿机械角度 | 链路已就绪 | 同步机械角度测量 |")
    add("")
    add("> 本轮**没有**发送任何运动命令，没有改变 `nvpmodel`，没有停止任何常驻服务，")
    add("> 没有写入系统库目录。MCU 未回 ACK，因此**不宣称 MCU 已收到帧**；")
    add("> 「零运动」是固件 switch 的静态性质，不是物理观测。")
    add("")

    add("## 5. 证据状态")
    add("")
    add("- `evidence_status = device_measurement_real_uart_no_motion`，")
    add("  `device_measurement=true`、`physical_uart_writes=true`、`physical_motion=false`。")
    add("- `citation_allowed=false`：只可作为 **2026-11-07 并列新测量**引用，")
    add("  不得当作十月复现、不得当作脉冲串抖动、不得与历史或 pty 数据合并统计。")
    add("- 结论边界：真实串口路径下的**主机侧命令起始相位**已有实测；")
    add("  **架构隔离脉冲串抖动**仍是契约推导（见 `BOUNDARY_ISOLATION.md`）。")
    add("")
    add("## 6. 复现")
    add("")
    add("```bash")
    add("# on the Xavier, read-only inventory")
    add("python3 scripts/uart_timing.py --mode probe --device /dev/ttyUSB0 \\")
    add("    --output evidence/device/uart_link_inventory_20261107.json")
    add("# MAXN paired batch (inert frames only)")
    add("bash evidence/device/run_uart_timing.sh")
    add("# back on the laptop")
    add("python3 scripts/uart_report.py")
    add("```")
    add("")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
