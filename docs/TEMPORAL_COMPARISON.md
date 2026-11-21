# Temporal point and ROI comparison

This feature builds on candidate `5c67906448dbbd4675cfa446acc148747c4ad7c8` on the
separate `codex/desktop-temporal-comparison` branch. It does not modify PR #4 or
its source branch. All operations are offline and preserve the original LMTR.

## Using the comparison

1. Open a recording in the Desktop workspace and enable **Temperature over time**.
2. Click a native image pixel and **Add point**, or drag a rectangle and **Add ROI**.
   Add up to four selections, then **Calculate**. This batches them into one read.
3. The legend rows expose stable IDs (`S1`, etc.), editable names, color buttons,
   visibility checkboxes and removal (`×`). Hover a name to inspect its original
   native bounds/session. IDs are never reused during the document's lifetime.
4. ROI means are always available when the selection is visible. **min**/**max**
   add extrema from the same mask-aware statistics used by the image inspector.
   Dots represent points/means, downward triangles minima and upward triangles
   maxima. Display changes never alter stored values or re-read the file.
5. Enable **Δ** and choose left and right operands. The white trace is left minus
   right in °C, in a separate pane with its own temperature scale and the same time
   axis; ROI operands use means. Incompatible pairs show an explicit refusal.
   Hiding an operand does not disable an independently requested difference.
6. Click chart marks to pause/seek; Previous/Next/slider/Play move the shared cursor.
   Equal-time events remain separately accessible through Previous/Next. Gaps and
   unavailable events clear current values, never carrying forward the last reading.
7. **CSV…** writes a new comparison file. Existing targets are refused. Selection
   controls scroll on short screens; the full-width plot, legend and current values
   remain below the image. Drag the horizontal divider to adjust their height.
8. **Cancel** stops the current calculation or CSV export cooperatively. The source
   and last complete result remain available; partial CSV output is removed. The
   indeterminate progress indicator does not imply a measured completion percentage.

Adding a selection invalidates the plotted result until **Calculate** completes.
Removing one reuses the remaining data and disables a difference that referenced
it. A source replacement discards the entire comparison. Selection edits are
in-memory only and are not restored on reopening a document.

## Compatibility and missing observations

Differences require the same recording ID, timeline clock, file time range, stream,
source session, module/model and native grid. Each value also requires identical
event ID, integer timestamp, segment and session, with two valid observations.
An equal timestamp alone cannot establish common acquisition. Cross-stream/file
comparison is deliberately refused even if timestamps appear close or equal.

Mask-invalid points, empty valid ROI sets, unavailable measurements and different
native spaces have no temperature. Explicit gap and segment events are retained.
No interpolation, nearest-neighbor matching, timeline shifting or substitution is
performed. Different compatible ROIs may have different valid pixel counts; their
means follow the existing deterministic valid-only binary64 statistic.

The chart is a scatter trace of actual observations, not a continuous estimate.
Numeric CSV precision is independent of chart formatting and pixel resolution.

## Comparison CSV

UTF-8 long form: selection blocks in legend order, then the optional enabled
difference block. Within each block, rows keep original event order. Hidden
selections are exported as well. A maximum-size admitted file exports at most
250,000 rows (four selections and one difference); output streams on the worker.

| Columns | Meaning |
| --- | --- |
| `row_kind` | `selection` or `difference` |
| `selection_id`, `selection_name`, `color`, `visible`, `show_roi_min`, `show_roi_max` | Stable identity and current display metadata; visibility never filters measurements |
| `left_selection_id`, `right_selection_id` | Ordered operands for a difference; blank on selection rows |
| `left_temperature_c`, `right_temperature_c`, `left_status`, `right_status` | Exact operands and availability evidence |
| `recording_id`, `stream_id`, `selected_session_id`, native bounds/grid | Source and coordinate identity; difference rows have no rectangle of their own |
| `entry_id`, `timeline_ns`, `elapsed_ns`, `first_timeline_ns`, clock domain | Original integer nanoseconds/event identity, without float conversions |
| `temperature_c` | Exact Float32-to-binary64 point value, deterministic binary64 ROI mean or binary64 subtraction, in round-trip decimal text |
| `roi_min_c`, `roi_max_c`, `valid_pixels`, `total_pixels` | Existing mask-aware statistics; blank for difference rows |
| `status`, `measurement_status`, `reason`, gap fields | Selection availability and original event evidence; difference status is `valid`, `operands_unavailable` or `incompatible_observation` |
| receipt, period, slot/deadline fields | Separate original timing evidence, without clock alignment |
| `file_status`, `end_status`, `unknown_tail` | Completion/recovery evidence, never fabricated tail events |

Unavailable values are empty cells, including unavailable differences. Zero remains
a real measured zero. Signed zero is preserved for point values. A difference uses
ordinary binary64 subtraction; there is no extra rounding or physical calibration.
Names are CSV-quoted, including Unicode, commas and quotes. Export cancellation or
failure removes only the file created by that export. Existing source/targets remain
protected by exclusive creation.

## Implementation and scoped validation

`lmtr_temporal.py` shares the existing native sampler and exclusive CSV writer.
`lmtr_comparison.py` owns immutable named scalar results and difference eligibility.
`lmtr_comparison_panel.py` owns display edits; `lmtr_temporal_chart.py` caches marks
separately from the moving playback cursor. The viewer worker performs all file
traversal and CSV writes. The established 50,000-event/8 MiB text/CHUNK limits and
cooperative cancellation are described in [the reader guide](LMTR_READER.md).

Targeted checks (not the historical full campaigns):

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=tests python -m unittest \
  test_lmtr_comparison test_lmtr_temporal test_lmtr_ui \
  test_lmtr_hardening_ui test_desktop_workspace test_window_check -v
```

The existing opt-in `--window-check-output` now exercises four comparison selections,
rename/color/hide, ROI extrema, difference, original-event chart/player synchronization,
CSV and source hashes on LMTR input. It works in the standalone frozen candidate;
only the destination chooser is substituted, not computation/export. This is automated
widget validation, not manual operator or physical-camera acceptance.

An optional developer tool produces a **new synthetic** 12-second example with
56 temperature observations and four explicit gaps:

```sh
python tools/prepare_temporal_demo.py /new/path/comparison-demo.lmtr
LMThermal-Desktop --open /new/path/comparison-demo.lmtr
```

The tool uses the existing fixture writer and requires the source checkout/tests.
It is not a product recorder and never replaces the canonical fixtures. It supplies
no evidence of physical accuracy or live acquisition performance.

## Linux candidate receipt — 2026-11-21

The [scoped machine-readable receipt](diagnostics/2026-11-21-temporal-comparison.json)
pins runtime `d43ef5e9fe480853f2d3e6cc8d2fd0f605c003c8`; later documentation does not
change that executable. Its private x86_64 archive is **146,093,940 bytes**, SHA-256
`8f473e4deb90aa8db15988f380cf464203db667d8bdc7915965fb22f4c4f3101`.

- **51/51 source tests** and the same **51/51 in the existing Ubuntu build root**.
- Frozen comparison checks pass in the build root, a native Fedora xcb window and
  a separate Ubuntu 22.04.5 userland without Python, venv, sources or network.
- All four selections, extrema, difference, gap seek, cursor synchronization,
  CSV, source hashes and normal Close pass. All **310 exported rows** were also
  compared with the original **62 events** and exact stored point/ROI values.
- The bundle audit verifies **535 files**, maximum required **GLIBC 2.35**, and
  zero unmapped native components. The archive and executable require no system
  installation. This is a private candidate, not a public release.

The GUI checks use own-process Qt events and substitute the export destination
chooser. Manual operator acceptance and new native Windows validation remain open.
No historical full campaign, physical camera, Android build, Actions run or release
was performed. PR #4 remains an independent draft at its original candidate.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.

## Linux functional acceptance tools

See the [follow-up candidate and acceptance results](LINUX_ANALYSIS_ACCEPTANCE.md)
for the exact executable, real-input scope and remaining human gate.

The follow-up uses two existing **real** Android HT-301 recordings, opened read-only.
The new `--analysis-check-output` diagnostic executes four selections, two ROI sizes,
extrema, a compatible difference, Cancel/recalculate, hide/reactivate/rename/remove,
chart seek/play/pause, unavailable-event clearing and 940×740 resizing. It opens and
exports through the actual Qt fallback selectors using own-process Qt input events;
it never replaces `QFileDialog.getOpenFileName` or `getSaveFileName`. Unsupported
dialog providers fail explicitly. It records whether the platform exposes a native
window, frozen-runtime status, GUI timer intervals, source hashes and screenshots.

```sh
LMThermal-Desktop --open /path/recording.lmtr \
  --analysis-check-output /new/private/acceptance-directory
python tools/verify_comparison_csv.py /path/recording.lmtr \
  /new/private/acceptance-directory/comparison.csv --report /new/private/csv-check.json
```

Use a recording with at least two distinct valid pixels and enough observations for
the real Cancel/playback exercise. This diagnostic is opt-in, never a startup test.
The CSV verifier uses the pinned reference framing validator, then independently
decodes original Float32 payloads with `struct` and recomputes means/extrema/deltas
using sequential binary64 arithmetic. It checks exact integer times, stable IDs,
event coverage and empty unavailable values, without Desktop measurement/playback
or comparison imports. It is scoped to finalized supported materialized recordings;
it does not certify recovery or physical temperature accuracy. Run it without `-O`.

Focused follow-up suite (no historical campaign):

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=tests python -m unittest \
  test_lmtr_analysis_finish test_comparison_csv_verifier test_lmtr_comparison \
  test_lmtr_temporal test_lmtr_hardening_ui test_window_check_cli test_window_check -v
```

Exposed-window automation is **not human operator acceptance**. A human's judgement
of comfort during prolonged use remains separate from these repeatable actions.
