"""Celsius-only rendering and legend tests without Qt or camera hardware."""

import unittest

import numpy as np

from celsius_palette import (CelsiusRange, PALETTES, auto_range,
                             effective_range, legend_image, normalized_levels,
                             render_temperature)


class CelsiusPaletteTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.tile(np.linspace(10, 40, 384, dtype=np.float32), (288, 1))

    def test_normalization_is_monotonic_and_clamps_to_exact_celsius_bounds(self):
        levels = normalized_levels(self.matrix, 20, 30)
        self.assertEqual(int(levels[0, 0]), 0)
        self.assertEqual(int(levels[0, -1]), 255)
        self.assertTrue(np.all(np.diff(levels[0].astype(np.int16)) >= 0))
        self.assertEqual(int(normalized_levels(np.full_like(self.matrix, 25), 20, 30)[0, 0]), 128)

    def test_renderer_does_not_mutate_source_and_palette_changes_only_colors(self):
        before = self.matrix.copy()
        white = render_temperature(self.matrix, 20, 30, "White hot")
        inferno = render_temperature(self.matrix, 20, 30, "Inferno")
        self.assertEqual(white.shape, (288, 384, 3))
        self.assertFalse(np.array_equal(white, inferno))
        np.testing.assert_array_equal(self.matrix, before)
        for palette in PALETTES:
            self.assertEqual(render_temperature(self.matrix, 20, 30, palette).shape,
                             (288, 384, 3))

    def test_locked_range_is_exact_and_legend_uses_same_endpoints(self):
        bounds = effective_range(self.matrix, False, -5.5, 35.25)
        self.assertEqual(bounds, CelsiusRange(-5.5, 35.25))
        legend = legend_image(bounds.lower, bounds.upper, "Inferno", 256, 8)
        lower = render_temperature(np.full_like(self.matrix, bounds.lower),
                                   bounds.lower, bounds.upper, "Inferno")
        upper = render_temperature(np.full_like(self.matrix, bounds.upper),
                                   bounds.lower, bounds.upper, "Inferno")
        np.testing.assert_array_equal(legend[-1, 0], lower[0, 0])
        np.testing.assert_array_equal(legend[0, 0], upper[0, 0])
        np.testing.assert_array_equal(render_temperature(np.full_like(self.matrix, -100),
                                                         -5.5, 35.25, "Inferno")[0, 0], lower[0, 0])
        np.testing.assert_array_equal(render_temperature(np.full_like(self.matrix, 100),
                                                         -5.5, 35.25, "Inferno")[0, 0], upper[0, 0])

    def test_auto_percentiles_ignore_isolated_extrema_and_constant_has_span(self):
        values = self.matrix.copy()
        values[0, 0], values[0, 1] = -1000, 1000
        bounds = auto_range(values)
        self.assertGreater(bounds.lower, 10)
        self.assertLess(bounds.upper, 40)
        self.assertEqual(bounds, effective_range(values, True, -1000, 1000))
        constant = auto_range(np.full_like(values, 25))
        self.assertEqual(constant, CelsiusRange(24.5, 25.5))

    def test_invalid_inputs_are_rejected(self):
        for lower, upper in ((20, 20), (21, 20), (float("nan"), 30),
                             (20, float("inf"))):
            with self.assertRaises(ValueError):
                render_temperature(self.matrix, lower, upper, "Inferno")
        with self.assertRaises(ValueError):
            render_temperature(self.matrix[:287], 20, 30, "Inferno")
        with self.assertRaises(ValueError):
            render_temperature(np.zeros((288, 384), dtype=np.uint16), 20, 30, "Inferno")
        with self.assertRaises(ValueError):
            render_temperature(self.matrix, 20, 30, "Unknown")
        invalid = self.matrix.copy()
        invalid[0, 0] = np.nan
        with self.assertRaises(ValueError):
            auto_range(invalid)


if __name__ == "__main__":
    unittest.main()
