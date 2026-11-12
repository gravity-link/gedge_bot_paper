#!/usr/bin/env python3
"""Static checks for the host/MCU counter-turn coordination gap.

These checks inspect source structure only. They do not claim an oscilloscope or
robot-observed dropped frame; the corresponding dynamic test requires an operator.
"""
import hashlib
import json
import re
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCU = ROOT / "evidence" / "motor_uart_vision.current.c"
HOST = ROOT / "evidence" / "robot_runtime.current.py"
EXPECTED_MCU = "978d551c8655385f585e014a08b910f89332085d4a0ac5b71934c99bd6f7885e"
EXPECTED_HOST = "d80196b650217cf0e76578855538ab496c7274be4c74766b5b24dc18dc89b9f5"
mcu = MCU.read_text()
host = HOST.read_text()


def body(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise ValueError(f"unterminated function: {signature}")


def python_body(source, signature):
    start = source.index(signature)
    lines = source[start:].splitlines()
    base_indent = len(lines[0]) - len(lines[0].lstrip())
    selected = [lines[0]]
    for line in lines[1:]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped and indent <= base_indent and line.lstrip().startswith(("def ", "class ", "@", "async def ")):
            break
        selected.append(line)
    return "\n".join(selected)


turn_body = body(mcu, "void TurnBody(")
irq = body(mcu, "void USART1_IRQHandler(")
process = body(mcu, "void ProcessCommand(")
send = python_body(host, "    def _send(")
orient = python_body(host, "    def orient_to_sound(")


class FirmwareStaticAudit(unittest.TestCase):
    def test_snapshot_hashes(self):
        self.assertEqual(hashlib.sha256(MCU.read_bytes()).hexdigest(), EXPECTED_MCU)
        self.assertEqual(hashlib.sha256(HOST.read_bytes()).hexdigest(), EXPECTED_HOST)

    def test_mcu_counter_turn_is_fixed_not_host_plan(self):
        self.assertIn("#define MPAN_PULSE_COUNT 15", turn_body)
        self.assertIn("if (i < MPAN_PULSE_COUNT)", turn_body)
        self.assertIn("def counter_head_during_body():", orient)
        self.assertIn("counter_head_pulse = plan.head_pulse", orient)

    def test_turn_body_is_blocking(self):
        self.assertIn("for (uint16_t i = 0; i < pulse_count; i++)", turn_body)
        self.assertIn("delay_ms(speed);", turn_body)
        self.assertIn("delay_ms(1000);", turn_body)

    def test_irq_only_buffers_bytes(self):
        self.assertIn("rx_buf[rx_idx++] = d;", irq)
        self.assertNotIn("ProcessCommand", irq)

    def test_process_resets_receive_index_after_motion(self):
        switch_end = process.rindex("g_motor_busy = 0;")
        self.assertIn("TurnBody(1, pulse, SPEED_NORMAL);", process)
        self.assertIn("TurnBody(0, pulse, SPEED_NORMAL);", process)
        self.assertRegex(process[switch_end:], r"rx_idx\s*=\s*0\s*;")

    def test_host_second_command_has_no_mcu_queue_contract(self):
        write = send.index("self.serial.write(packet)")
        callback = send.index("after_send()")
        settle = send.index("settle_time =")
        self.assertLess(write, callback)
        self.assertLess(callback, settle)
        self.assertNotIn("rx_idx = 7", process)
        self.assertNotIn("memmove", process)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "schema": "gedgebot.firmware_static_audit.v1",
        "mcu_sha256": EXPECTED_MCU,
        "host_sha256": EXPECTED_HOST,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "dynamic_drop_observed": False,
        "finding": "Host emits a second counter-head frame after the body write; MCU TurnBody blocks, IRQ buffers bytes, and ProcessCommand resets rx_idx after motion. The MCU's in-routine counter-turn is fixed at 15 pulses rather than the host-computed head pulse. Static structure establishes a queue/execution gap, not an observed physical trajectory.",
        "timestamp_unix": time.time(),
    }
    (ROOT / "evidence" / "firmware_static_results.json").write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
