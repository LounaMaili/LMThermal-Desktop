"""Independent source-byte check of range summaries and deliberate tampering."""
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lmtr_comparison import collect_comparison, write_comparison_csv
from lmtr_range_statistics import summarize_range, write_range_summary_csv
from lmtr_reader import LmtrPlaybackModel
from lmtr_test_support import FILES
from test_lmtr_comparison import selections
from tools.verify_range_summary import verify_summary


class SummaryVerifierTests(unittest.TestCase):
    def test_original_float32_fixture_gaps_and_tampered_statistics_counts_and_bounds(self):
        source = FILES / 'illustration-06-stored.lmtr'
        model = LmtrPlaybackModel(source)
        try:
            comparison = collect_comparison(model, selections(model.frame(1)))
        finally:
            model.close()
        first = comparison.series[0]
        summary = summarize_range(comparison, first.first_ns, first.last_ns, difference=('S1', 'S2'))
        with TemporaryDirectory() as folder:
            detailed, target = Path(folder)/'detail.csv', Path(folder)/'summary.csv'
            write_comparison_csv(comparison, detailed, difference=('S1', 'S2'))
            write_range_summary_csv(summary, target)
            self.assertEqual('passed', verify_summary(source, detailed, target)['status'])
            with target.open(newline='') as data:
                original = list(csv.DictReader(data))
            for column, value in (('mean_c', '999'), ('valid_observations', '999'),
                                  ('known_missing_observations', '999'), ('range_end_ns', str(first.last_ns+1)),
                                  ('compatibility', 'unchecked')):
                with self.subTest(column=column):
                    rows = [dict(r) for r in original]
                    rows[0][column] = value
                    with target.open('w', newline='') as data:
                        writer = csv.DictWriter(data, fieldnames=tuple(rows[0]))
                        writer.writeheader(); writer.writerows(rows)
                    with self.assertRaises(AssertionError):
                        verify_summary(source, detailed, target)
