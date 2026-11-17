"""Clock precision and boundary behavior shared by all capture displays."""
import math
import unittest

from odx_timing import format_elapsed, format_recording_clock, remaining_seconds


class TimingDisplayTests(unittest.TestCase):
    def test_elapsed_floors_completed_tenths_at_minute_boundaries(self):
        for value, expected in (
            (0, "00:00.0"), (0.099, "00:00.0"), (0.1, "00:00.1"),
            (0.999, "00:00.9"), (59.999, "00:59.9"), (60, "01:00.0"),
            (60.099, "01:00.0"), (239.9, "03:59.9"),
            (239.999, "03:59.9"), (240.0, "04:00.0"),
            (241.3, "04:01.3"), (245.4666666667, "04:05.4"),
            (3600.5, "60:00.5"),
        ):
            with self.subTest(value=value):
                self.assertEqual(format_elapsed(value), expected)

    def test_binary_noise_does_not_lose_a_tenth(self):
        for value, expected in (
            (0.3 - 0.1, "00:00.2"),
            (math.nextafter(239.9, 0), "03:59.9"),
            (math.nextafter(60.0, 0), "01:00.0"),
        ):
            with self.subTest(value=value):
                self.assertEqual(format_elapsed(value), expected)

    def test_visible_fraction_is_not_rounded_up_to_an_extra_second(self):
        self.assertEqual(format_elapsed(239.95), "03:59.9")
        self.assertEqual(format_elapsed(240.99), "04:00.9")
        self.assertEqual(format_recording_clock(239.95, 240), "03:59.9 / 04:00.0")

    def test_invalid_negative_and_nonfinite_elapsed_render_as_zero(self):
        for value in (-3, -0.01, None, "unavailable", float("nan"),
                      float("inf"), float("-inf"), complex(1, 2)):
            with self.subTest(value=value):
                self.assertEqual(format_elapsed(value), "00:00.0")

    def test_large_finite_value_does_not_overflow_formatting(self):
        rendered = format_elapsed(1e308)
        self.assertTrue(rendered.endswith(":00.0") or ":" in rendered)
        self.assertNotIn("inf", rendered)

    def test_target_uses_the_same_precision_as_elapsed(self):
        self.assertEqual(format_recording_clock(241.3, 240), "04:01.3 / 04:00.0")
        self.assertEqual(format_recording_clock(12.35, 30.75), "00:12.3 / 00:30.7")
        self.assertEqual(format_recording_clock(-1, None), "00:00.0 / 00:00.0")

    def test_remaining_ceil_keeps_positive_fraction_visible_until_target(self):
        for elapsed, target, expected in (
            (0, 240, 240), (230, 240, 10), (230.1, 240, 10),
            (239, 240, 1), (239.9, 240, 1), (239.999, 240, 1),
            (240, 240, 0), (240.01, 240, 0), (241.3, 240, 0),
            (0.01, 0.1, 1),
        ):
            with self.subTest(elapsed=elapsed, target=target):
                self.assertEqual(remaining_seconds(elapsed, target), expected)

    def test_remaining_handles_invalid_observations_without_nan_or_overflow(self):
        self.assertEqual(remaining_seconds(-1, 240), 240)
        self.assertEqual(remaining_seconds(None, 240), 240)
        self.assertEqual(remaining_seconds(float("nan"), 240), 240)
        self.assertEqual(remaining_seconds(1, float("inf")), 0)
        self.assertEqual(remaining_seconds(1, "unavailable"), 0)


if __name__ == "__main__":
    unittest.main()
