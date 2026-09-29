"""Evidence-gated session and display-only preview regressions using saved frames."""

from pathlib import Path
import struct
import unittest

import numpy as np

from diagnostic_preview import normalize_raw14, preview_gray, render_overlay
from radiometric_session import (
    HT301RadiometricSession, SessionError, SessionState, inspect_frame,
    make_measurement,
)


FIXTURES = Path(__file__).parent / "fixtures"
DISPLAY = (FIXTURES / "display-room-baseline.raw").read_bytes()
FIRST = (FIXTURES / "radiometric-room-first.raw").read_bytes()
RANGE = (FIXTURES / "radiometric-room-range.raw").read_bytes()
HELD = (FIXTURES / "radiometric-room-shutter-held.raw").read_bytes()
SETTLED = (FIXTURES / "radiometric-room-settled.raw").read_bytes()
HAND = (FIXTURES / "warm-hand-settled.raw").read_bytes()


def changed_pixel(raw, offset):
    """Create distinct valid test images without changing trailer extrema."""
    modified = bytearray(raw)
    words = np.frombuffer(raw, dtype="<u2", count=384 * 288)
    struct.pack_into("<H", modified, 2 * (100 * 384 + 100), int(words.min()) + 50 + offset)
    return bytes(modified)


class Control:
    """Record exactly which supported zoom writes the session attempted."""

    def __init__(self):
        self.value = 0
        self.writes = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.writes.append(value)


class Stream:
    """Reproduce display, transition, range, shutter hold and live recovery."""

    def __init__(self, control, *, switch=True, extra_hold=31):
        self.control = control
        self.switch = switch
        self.extra_hold = extra_hold
        self.by_control = {32772: 0, 32800: 0, 32768: 0}
        self.timestamp = 0.0
        self.inject = []

    def next(self):
        self.timestamp += .04
        if self.inject:
            return self.timestamp, self.inject.pop(0)
        value = self.control.value
        if value in (0, 32773) or (value == 32772 and not self.switch):
            return self.timestamp, DISPLAY
        index = self.by_control[value]
        self.by_control[value] += 1
        if value == 32772:
            return self.timestamp, changed_pixel(FIRST, index)
        if value == 32800:
            return self.timestamp, changed_pixel(RANGE, index)
        if index < 75 + self.extra_hold:
            return self.timestamp, HELD
        return self.timestamp, changed_pixel(SETTLED, index - 75 - self.extra_hold)


class SessionTests(unittest.TestCase):
    def new_session(self, *, switch=True, extra_hold=31, stage_limit=150):
        control = Control()
        stream = Stream(control, switch=switch, extra_hold=extra_hold)
        observations = []
        session = HT301RadiometricSession(stream, control, stage_discard=0,
                                         stage_limit=stage_limit, sleep=lambda _: None,
                                         on_observation=observations.append)
        return session, stream, control, observations

    def test_official_sequence_waits_for_live_recovery_after_extra_hold(self):
        session, stream, control, observations = self.new_session()
        measured = session.initialize_normal_range()
        self.assertEqual(control.writes, [32772, 32800, 32768])
        self.assertEqual(session.state, SessionState.RADIOMETRIC_READY)
        self.assertEqual(stream.by_control[32768], 75 + 31 + 5)
        self.assertEqual(measured.temperature_c.shape, (288, 384))
        self.assertTrue(np.isfinite(measured.temperature_c).all())
        self.assertEqual(measured.high_index, measured.image_word_max)
        self.assertEqual(measured.low_index, measured.image_word_min)
        self.assertGreater(session.rejections["held_image"], 0)
        self.assertTrue(any(o.state == SessionState.SHUTTER_TRANSIENT for o in observations))
        self.assertIsNotNone(session.time_to_ready_seconds)

    def test_missing_32772_transition_fails_before_range_or_shutter(self):
        session, _, control, _ = self.new_session(switch=False, stage_limit=5)
        with self.assertRaisesRegex(SessionError, "raw14_transition"):
            session.initialize_normal_range()
        self.assertEqual(control.writes, [32772])
        self.assertEqual(session.state, SessionState.ERROR)

    def test_mixed_startup_frame_is_skipped_before_display_baseline(self):
        session, stream, control, observations = self.new_session()
        mixed = bytearray(DISPLAY)
        struct.pack_into("<H", mixed, 0, 5000)
        stream.inject.append(bytes(mixed))
        session.initialize_normal_range()
        self.assertEqual(observations[0].rejection, "mixed_or_out_of_range_words")
        self.assertEqual(session.rejections["mixed_or_out_of_range_words"], 1)
        self.assertEqual(session.events[0]["stage"], "display_baseline")
        self.assertEqual(control.writes, [32772, 32800, 32768])

    def test_unknown_starting_zoom_is_rejected_without_a_write(self):
        session, _, control, _ = self.new_session()
        control.value = 32773
        with self.assertRaisesRegex(SessionError, "readback 0"):
            session.initialize_normal_range()
        self.assertEqual(control.writes, [])

    def test_existing_raw14_is_read_only_without_known_host_state(self):
        session, _, control, _ = self.new_session()
        control.value = 32772
        observation = session.poll()
        self.assertEqual(observation.inspection.mode, "raw14")
        self.assertEqual(session.state, SessionState.RAW14_UNSETTLED)
        self.assertIsNone(observation.measurement)
        with self.assertRaisesRegex(SessionError, "existing raw14"):
            session.initialize_normal_range()
        self.assertEqual(control.writes, [])

    def test_later_malformed_and_held_frames_demote_then_recover(self):
        session, stream, _, _ = self.new_session(extra_hold=0)
        session.initialize_normal_range()
        stream.inject.append(SETTLED[:-1])
        invalid = session.poll()
        self.assertEqual(invalid.rejection, "transport_size")
        self.assertEqual(session.state, SessionState.RAW14_UNSETTLED)
        recovered = [session.poll() for _ in range(5)]
        self.assertIsNotNone(recovered[-1].measurement)
        stream.inject.append(recovered[-1].raw)
        held = session.poll()
        self.assertEqual(held.rejection, "held_image")
        self.assertEqual(session.state, SessionState.RAW14_UNSETTLED)

    def test_display_and_summary_mismatch_cannot_be_measurements(self):
        self.assertEqual(inspect_frame(DISPLAY).mode, "display")
        with self.assertRaisesRegex(ValueError, "not measurement-ready"):
            make_measurement(DISPLAY, 0)
        changed = bytearray(SETTLED)
        struct.pack_into("<H", changed, 221192, 1)
        self.assertFalse(inspect_frame(changed).summary_valid)
        with self.assertRaisesRegex(ValueError, "summary_mismatch"):
            make_measurement(changed, 0)
        zeroed = bytearray(SETTLED)
        zeroed[223494:223514] = bytes(20)
        zeroed[224094:224114] = bytes(20)
        self.assertEqual(inspect_frame(zeroed).reason, "invalid_settings_or_calibration")

    def test_spatial_hand_fixture_keeps_trailer_and_literal_center_distinct(self):
        frame = make_measurement(HAND, 0)
        self.assertEqual((frame.trailer_center_index, frame.literal_center_index), (5740, 5742))
        self.assertEqual(frame.high_xy, (191, 211))
        self.assertEqual(frame.low_xy, (329, 83))
        self.assertEqual(int(frame.raw14[211, 191]), frame.high_index)
        self.assertEqual(int(frame.raw14[83, 329]), frame.low_index)
        self.assertGreater(float(frame.temperature_c[144:176, 176:208].mean()),
                           float(frame.temperature_c[32:64, 16:48].mean()))
        self.assertFalse(frame.raw14.flags.writeable)
        self.assertFalse(frame.temperature_c.flags.writeable)


class PreviewTests(unittest.TestCase):
    def test_display_uses_y_and_raw14_normalization_does_not_mutate_words(self):
        display = preview_gray(DISPLAY, "display")
        self.assertEqual(display.shape, (288, 384))
        self.assertEqual(display[144, 192], DISPLAY[2 * (144 * 384 + 192)])
        raw = np.frombuffer(SETTLED, dtype="<u2", count=384 * 288).reshape(288, 384)
        before = raw.copy()
        normalized = normalize_raw14(raw)
        self.assertEqual(normalized.dtype, np.uint8)
        self.assertEqual(normalized.shape, raw.shape)
        np.testing.assert_array_equal(raw, before)
        self.assertGreater(int(normalized.max()), int(normalized.min()))
        with self.assertRaises(ValueError):
            normalize_raw14(np.full((288, 384), 0x8000, dtype=np.uint16))

    def test_invalid_frame_overlay_and_measurement_markers_are_render_only(self):
        session, stream, _, _ = SessionTests().new_session(extra_hold=0)
        session.initialize_normal_range()
        observation = session.poll()
        original = observation.raw
        overlay = render_overlay(observation, [(10, 10, 20, 20)])
        self.assertEqual(overlay.shape, (288, 384, 3))
        self.assertEqual(overlay[144, 192].tolist(), [0, 255, 255])
        self.assertEqual(observation.raw, original)
        stream.inject.append(b"bad")
        invalid = session.poll()
        self.assertEqual(render_overlay(invalid).shape, (288, 384, 3))


if __name__ == "__main__":
    unittest.main()
