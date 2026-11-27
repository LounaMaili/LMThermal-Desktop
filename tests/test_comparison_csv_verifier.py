"""The independent diagnostic must reject plausible but altered analysis evidence."""
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lmtr_comparison import collect_comparison, write_comparison_csv
from lmtr_reader import LmtrPlaybackModel
from lmtr_test_support import FILES
from test_lmtr_comparison import selections
from tools.verify_comparison_csv import verify


class CsvVerifierTests(unittest.TestCase):
    def test_independent_decoding_accepts_original_and_rejects_changed_values_times_ids_and_gaps(self):
        source = FILES / 'illustration-06-stored.lmtr'
        with TemporaryDirectory() as folder:
            target = Path(folder) / 'comparison.csv'
            model = LmtrPlaybackModel(source)
            try:
                result = collect_comparison(model, selections(model.frame(1)))
                write_comparison_csv(result, target, difference=('S1', 'S2'))
            finally:
                model.close()
            self.assertEqual('passed', verify(source, target)['status'])
            with target.open(newline='', encoding='utf-8') as data:
                original = list(csv.DictReader(data))
            observation = next(i for i, r in enumerate(original) if r['row_kind'] == 'selection' and r['status'] == 'valid')
            gap_delta = next(i for i, r in enumerate(original) if r['row_kind'] == 'difference' and r['event'] == 'gap')
            for index, column, value in ((observation, 'timeline_ns', str(int(original[observation]['timeline_ns']) + 1)),
                                         (observation, 'temperature_c', '30.0000000001'),
                                         (observation, 'selection_id', 'altered'),
                                         (gap_delta, 'temperature_c', '0')):
                with self.subTest(column=column, index=index):
                    rows = [dict(r) for r in original]
                    rows[index][column] = value
                    with target.open('w', newline='', encoding='utf-8') as data:
                        writer = csv.DictWriter(data, fieldnames=tuple(rows[0]))
                        writer.writeheader()
                        writer.writerows(rows)
                    with self.assertRaises(AssertionError):
                        verify(source, target)
