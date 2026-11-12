#!/usr/bin/env python3
"""Offline contract tests for the captured GEdgeBot runtime.

These tests never open a serial device, camera, microphone, or motor. They test
pure functions and parsers in the exact source snapshot named below.
"""
import hashlib
import importlib.util
import json
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evidence" / "robot_runtime.current.py"
EXPECTED_SHA256 = "d80196b650217cf0e76578855538ab496c7274be4c74766b5b24dc18dc89b9f5"

spec = importlib.util.spec_from_file_location("robot_runtime_snapshot", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def mic_frame(angle, command=0x82, subcommand=0x02, corrupt=False):
    frame = bytearray([0xFF, 0xA5, 0x01, 0x00, 0x05, command, subcommand,
                       angle & 0xFF, (angle >> 8) & 0xFF, 0x00])
    frame[-1] = sum(frame[:-1]) & 0xFF
    if corrupt:
        frame[-1] ^= 0x01
    return bytes(frame)


class SourceIdentityTest(unittest.TestCase):
    def test_snapshot_hash(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), EXPECTED_SHA256)


class MotorProtocolTest(unittest.TestCase):
    def test_seven_byte_turn_frame(self):
        self.assertEqual(runtime.build_motor_command(0x01, 0x1234),
                         bytes([0xAA, 0x55, 0x01, 0x34, 0x12, 0x0D, 0x0A]))

    def test_vision_direction_uses_bit_fifteen(self):
        left = runtime.build_vision_track_command("left", 5)
        right = runtime.build_vision_track_command("right", 5)
        self.assertEqual(left[3] | (left[4] << 8), 5)
        self.assertEqual(right[3] | (right[4] << 8), 0x8005)

    def test_pulse_is_clamped_to_unsigned_sixteen_bits(self):
        self.assertEqual(runtime.build_motor_command(1, -1)[3:5], b"\x00\x00")
        self.assertEqual(runtime.build_motor_command(1, 70000)[3:5], b"\xff\xff")

    def test_protocol_has_no_sequence_or_crc_field(self):
        frame = runtime.build_motor_command(1, 5)
        self.assertEqual(len(frame), 7)
        self.assertEqual(frame[:2], b"\xaa\x55")
        self.assertEqual(frame[-2:], b"\x0d\x0a")


class MicProtocolTest(unittest.TestCase):
    def setUp(self):
        self.reader = runtime.MicArrayReader("/dev/null", 115200, heartbeat=False)

    def test_valid_doa_frame(self):
        event = self.reader._parse_packet(mic_frame(278))
        self.assertIsNotNone(event)
        self.assertEqual(event.angle, 278)

    def test_bad_checksum_is_discarded(self):
        self.assertIsNone(self.reader._parse_packet(mic_frame(90, corrupt=True)))

    def test_non_doa_frame_is_ignored(self):
        self.assertIsNone(self.reader._parse_packet(mic_frame(90, command=0x81)))

    def test_out_of_range_angle_is_ignored(self):
        self.assertIsNone(self.reader._parse_packet(mic_frame(360)))


class AngleMapperTest(unittest.TestCase):
    def mapper(self, **kwargs):
        defaults = dict(mic_angle_offset=0.0, mic_invert=False,
                        sound_dead_zone_deg=15.0, max_head_pulse=15,
                        max_body_pulse=25, min_sound_body_pulse=15)
        defaults.update(kwargs)
        return runtime.AngleMapper(**defaults)

    def test_dead_zone_produces_no_motion(self):
        for raw in (0, 14, 346):
            plan = self.mapper().plan_sound_orient(raw)
            self.assertIsNone(plan.direction)
            self.assertEqual((plan.head_pulse, plan.body_pulse), (0, 0))

    def test_current_default_clamps_ninety_degree_body_pulse(self):
        plan = self.mapper().plan_sound_orient(90)
        self.assertEqual(plan.direction, "left")
        self.assertEqual(plan.signed_deg, 90)
        self.assertEqual(plan.head_pulse, 9)
        self.assertEqual(plan.body_pulse, 25)

    def test_patent_example_requires_nondefault_body_limit(self):
        plan = self.mapper().plan_sound_orient(120)
        self.assertEqual(plan.signed_deg, 120)
        self.assertEqual(plan.head_pulse, 9)
        self.assertEqual(plan.body_pulse, 25)
        wide = self.mapper(max_body_pulse=60).plan_sound_orient(120)
        self.assertEqual((wide.head_pulse, wide.body_pulse), (9, 27))

    def test_right_turn_and_inversion(self):
        self.assertEqual(self.mapper().plan_sound_orient(307).signed_deg, -53)
        self.assertEqual(self.mapper().plan_sound_orient(307).direction, "right")
        self.assertEqual(self.mapper(mic_invert=True).plan_sound_orient(307).direction, "left")


class VisualFollowerTest(unittest.TestCase):
    def follower(self):
        return runtime.VisualFollower(center_zone_width=64, command_interval=0.35,
                                      body_interval=2.0, body_track_threshold=15,
                                      body_track_pulse=10)

    def test_center_and_single_frame_do_not_command(self):
        follower = self.follower()
        self.assertIsNone(follower.update(320).head_direction)
        action = follower.update(500)
        self.assertIsNone(action.head_direction)
        self.assertIn("confirming", action.reason)

    def test_confirmed_large_offset_commands_ten_head_pulses(self):
        follower = self.follower()
        follower.update(600)
        action = follower.update(600)
        self.assertEqual(action.head_direction, "left")
        self.assertEqual(action.head_pulse, 10)

    def test_body_command_waits_for_accumulated_threshold(self):
        follower = self.follower()
        follower.update(500)
        second = follower.update(500)
        self.assertEqual(second.head_pulse, 5)
        self.assertIsNone(second.body_direction)
        follower.last_command_time = 0.0
        third = follower.update(500)
        self.assertEqual(third.head_pulse, 5)
        self.assertIsNone(third.body_direction)
        follower.last_command_time = 0.0
        fourth = follower.update(500)
        self.assertEqual(fourth.body_direction, "left")
        self.assertEqual(fourth.body_pulse, 10)
        self.assertEqual(follower.accumulated_left, 0)

    def test_rate_limit(self):
        follower = self.follower()
        follower.update(500)
        follower.update(500)
        follower.last_command_time = time.time()
        self.assertIn("rate limited", follower.update(500).reason)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "source": str(SOURCE),
        "source_sha256": EXPECTED_SHA256,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "motor_device_opened": False,
        "timestamp_unix": time.time(),
    }
    output = ROOT / "evidence" / "contract_test_results.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
