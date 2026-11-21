# Statistics over a selected time range

This Desktop increment starts at `3557f9484b15065030a8d8c9d387af1e511a324c` on
`codex/desktop-time-range-statistics`. It preserves PR #4, the reader/LMTR contract,
the existing detailed comparison CSV and the original recordings.

## Interaction

1. Open an LMTR, enable **Temperature over time**, add up to four points/ROIs and
   **Calculate**. Optionally enable a compatible Δ pair in **Selections**.
2. Enable **Select range** and drag across the chart, or use Shift+drag while in
   ordinary navigation mode. The **Range statistics** tab opens automatically.
3. Drag the A/B handles to adjust either bound. Handles snap to exact original
   event times, including gaps. Close instants can share a display pixel; the
   nanosecond fields preserve their distinction. Both bounds are inclusive.
4. Enter integer **A (ns)** / **B (ns)** and press **Apply** for precise limits,
   including periods between events. Editing invalidates the old summary until
   Apply succeeds. Invalid/reversed/out-of-recording bounds are refused.
5. **A ← cursor** / **B ← cursor** use the current original event timestamp. If a
   bound crosses the other one, the other bound moves to the same instant.
6. **Full period** restores the complete verified extent. **Cancel** stops the
   incremental summary; Apply restarts it. New ranges replace old pending work.
7. Uncheck **Select range** to resume ordinary sample-click navigation. Handles
   remain draggable at the top of the plot. The cursor and range remain independent:
   playback can leave the selected period and range edits never seek or pause it.
8. **Summary CSV…** saves the complete selected-range snapshot, including hidden
   selections and the requested Δ compatibility status. The original **CSV…** in
   Selections still exports the full detailed comparison. File exports retain the
   existing playback-worker behavior and may be cancelled; source/target changes
   during the chooser cannot silently relabel the exported snapshot.

The colored band and A/B lines share the original time axis with the moving cursor.
The statistics table uses readable theme text and curve-color swatches. On short
windows its tab scrolls while the chart remains visible; the horizontal divider
can give the image/table more height. Exact numeric values and count details are
available in table tooltips and the CSV.

## Arithmetic and count semantics

Every range is `[start_ns, end_ns]`, using Python integers and comparisons with
original integer `timeline_ns`. No timestamp is converted to float for inclusion
or export. Display coordinates are approximate; mouse selection snaps back to
original integer instants. Equal-time events are counted separately.

For a point, input values are its exact stored temperatures. For a ROI, input
values are the existing per-observation mask-aware **ROI means**. The range's
minimum and maximum are extrema of these scalar values, **not spatial pixel
extrema over all planes**. Optional ROI min/max marks on the chart retain their
existing spatial meaning and do not change the summary's input series.

The arithmetic mean is sequential binary64 addition in original event order,
divided by the number of valid observations. Each valid observation has equal
weight. It is neither time weighted nor weighted by the number of valid ROI
pixels. No missing observation is interpolated or replaced by zero.

| Count | Meaning |
| --- | --- |
| Valid | Stored observations with a valid scalar for this selection/pair |
| Unavailable | Stored observations without a usable scalar, including other native spaces |
| Incompatible observations | Subset of unavailable observations refused by native-space or same-event compatibility |
| Gap events | Explicit gap entries whose own original timestamps lie inside the interval |
| Known missing observations | Sum of declared counts on those gap entries |
| Unknown gap events | Gap entries with unknown counts; shown as `+ ?`, never estimated |
| Control events | Segment/control entries; not counted as stored observations |

A gap representing several losses is included as a whole by its original event
time. Its count is not apportioned over an assumed duration or periodic slots.
Control events, gaps and unknown tails never become invented observation records.
`no_usable_data` means no valid scalar in the range; all temperature cells stay
empty in CSV and show an em dash in the table. Zero remains a real measured value.

Δ uses the existing same-recording/clock/stream/session/module/model/grid gate and
same-event/instant/session check. A refused pair has `incompatible_pair` status
and no numeric statistics. Its counts describe the left operand's original event
set, not a cross-session join. Compatible pairs summarize per-observation Δ,
not a subtraction of separately accumulated means. Other-session observations
remain unavailable in each selection's own statistics.

## Bounded calculation and export

`lmtr_range_statistics.py` operates only on the already admitted immutable scalar
series (up to four × 50,000 events, plus one on-demand Δ view). It keeps at most
five small accumulators and uses bisect to delimit each interval. No retained
temperature history or decoded plane is added. The 8 MiB text/CHUNK/reader limits
remain those of the existing collection path.

`RangePanel` consumes at most 256 events per Qt timer turn. There is one active
iterator, no growing request queue, and no frame/reader owner. Cancel, new source,
Close or a new range drops that iterator before a partial result can publish.
Navigation updates only the cursor indication; names/colors update labels without
recalculating the statistics. Summary export runs on the existing file worker and
uses exclusive creation and cancellation cleanup. Existing targets are refused.

The UTF-8 CSV has at most five rows: four selection summaries and an optional Δ.
It preserves exact bounds, aggregation/inclusivity, IDs/names, native identity and
ROI bounds, recording/clock/session, all counts, exact first/last valid timestamps,
round-trip binary64 statistics, compatibility reason, END/recovery/unknown-tail
evidence. No valid data is represented by empty temperature cells, never a guessed
value. The summary does not mutate selection coordinates or the source recording.

## Focused validation

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=tests python -m unittest \
  test_lmtr_range_statistics test_range_summary_verifier test_lmtr_analysis_finish \
  test_comparison_csv_verifier test_lmtr_comparison test_lmtr_temporal \
  test_lmtr_hardening_ui test_window_check_cli test_window_check -v

LMThermal-Desktop --open /path/real.lmtr --range-check-output /new/private/report
python tools/verify_range_summary.py /path/real.lmtr /new/private/report/comparison.csv \
  /new/private/report/range-summary.csv --report /new/private/verified-summary.json
```

The independent verifier first redecodes original Float32 payloads through the
existing stdlib diagnostic, then separately aggregates the verified detailed rows.
It does not import the product range calculator. Tests cover known irregular/
duplicate/large nanosecond times, gaps with known/unknown counts, empty/control-only
ranges, ROI-mean semantics, incompatible pairs, cancellation and tampered CSVs.
The opt-in GUI scenario uses real widgets and selectors on an explicit real file,
including handle adjustment during Play, Full period, exact fields, empty ranges,
CSV and screenshots. It is automated application interaction, not human acceptance.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.

## Linux candidate receipt — 2026-11-21

Runtime source is `2cfac6b17b9a2d9f10727664f5bb32971461be88`. Later documentation
does not change the executable. The private archive is **146,153,661 bytes**,
SHA-256 `337600f9c75d2314a498d34e9f03e754cf179073bedddd034e5b58fa92ba1edb`.
See the [sanitized validation receipt](diagnostics/2026-11-21-time-range-statistics.json).

- **48/48 targeted tests** pass on the host and in the existing offline Ubuntu
  build root. The synthetic frozen integration check also passes.
- Fresh extraction verifies all **535 manifest files**, with highest observed
  **GLIBC 2.35** requirement. The extracted binary passes the real-file range
  scenario in a separate userland without Python, source checkout, venv or network.
- Two real recordings pass the exposed **Fedora 45 xcb** scenario using actual
  Qt fallback Open/CSV selectors. Range dragging/handles during Play, exact bounds,
  full reset, queued cancellation, unavailable ranges and 940×740 resizing pass.
- The first selected range `[845971314, 2444489381]` ns contains nine valid
  observations per series; the second `[1080430786, 3242014609]` ns contains 23
  valid observations and 32 declared losses. In that second range S1 min/max/mean
  are `36.25070571899414`, `36.311927795410156`, `36.27823721844217` °C and the
  Δ mean is `15.2589983732804` °C.
- **Four summary CSVs / 20 rows** are independently checked against **630 unique
  detailed rows** and original Float32 payloads: two useful ranges, one genuinely
  empty interval and one explicit gap-only interval. Original hashes are unchanged.

Private evidence is under `build/time-range-statistics-20261121/` in the primary
Desktop checkout: `REPORT.md`, `launch.sh`, `linux-build/bundle/`, verified extracted
`candidate/`, screenshots/CSV/receipts in `final-real/` and `final-gaps/`, and the
isolated runtime receipt in `clean-runtime/`. Private captures are not in Git.

These are automated actions in real application windows, not human manual acceptance.
The two original recordings have interrupted END status and are short. Maximum-size
native GUI stress, prolonged comfort, other scaling/themes and a new Wayland-specific
run are not claimed. Small-window statistics scroll while the plot remains visible.
No new camera capture, Android/Recorder work, wire change, Windows campaign, Actions,
release, PR #4 modification or merge was performed. Early layout iterations are
retained separately; only the final frozen receipts support this candidate.
