# Linux thermal-analysis acceptance — 2026-11-21

The private standalone candidate is executable on the tested Fedora 45 x86_64
desktop. Runtime source: `5be6ce99b1b005b006bdbb2bd4833cc2848745c2`, based on
`69faa8207dbc95fd92f4f3e5c274fe670986f14f`. Work is isolated on
`codex/desktop-linux-analysis-finish`; PR #4 and `main` are untouched.

Archive: **146,108,706 bytes**, SHA-256
`b53d8738c84e7d8896c1817023dfd6bb19bfc92f1b3213b0a097798fcedb2a0a`.
The build used a clean committed snapshot in the existing offline Ubuntu 22.04
build root. All **535 manifest files** verified after fresh extraction; the native
audit passed with highest observed **GLIBC 2.35** requirement. The extracted binary
also passed the complete real-file scenario in a separate Ubuntu userland without
Python, source checkout, venv or network. This is a private candidate, not a release
or certification of every Linux configuration.

## Visible corrections

- Full-width plot below the image and selection controls; legend and current
  readings remain visible when the editor must scroll on a short window.
- Independent Celsius scale for the difference pane, sharing the original time
  axis; dots for means/points and distinct triangles for ROI minima/maxima.
- Explicit calculation/export Cancel and busy indicator. Cancel retains the open
  document and previous complete result; cancelled exports remove their partial file.
- Names update operand labels, current values and export metadata during typing,
  without rebuilding scalar results or cached plot marks.

## What was executed

| Evidence | Result and scope |
| --- | --- |
| Original archive | Fresh `/tmp` extraction, native Fedora startup/Open/PNG and comparison check passed; original archive retained |
| Focused tests | 38/38 on host and 38/38 in the build root; cancellation/stale tokens, partial export cleanup, names, layout, incompatible sessions, same-event differences, gaps/masks, event limits, source ownership and altered-CSV rejection |
| Frozen integration | Existing comparison widget check passed on the synthetic example; no full historical campaign |
| Final native GUI | Two real recordings; exposed xcb windows, actual Qt fallback Open/CSV selectors, no chooser substitution |
| Each GUI scenario | Two distinct points, 16×16 and 192×144 ROIs, means/min/max, S1−S2, four-selection limit, Cancel/recalculate, hide/reactivate/rename/remove, chart/image seek, Play/Pause, unavailable clearing and 940×740 resize, normal Close |
| Independent CSV | All 95 + 535 rows recomputed from original payload bytes by `tools/verify_comparison_csv.py`; exact nanoseconds, IDs, means/extrema, operands/deltas and empty unavailable readings |

The first real recording has **17 observations / 19 events / no gaps**, source
SHA-256 `5b78d7ef7d97e34913f23787f2ae083da6d1faf8decf70ecbec67defe4d6b4e3`.
The second has **45 observations / 107 events / 60 gaps**, source SHA-256
`0ca64fc9a9a9507a241f20c1b358d176c476972603f088bd64cafcea9cb76a3f`.
Both have a declared interrupted END from their original short capture. They were
opened normally, never repaired or rewritten; before/after hashes match.

For the first original observation (`entry_id=1`, `timeline_ns=130553548`), S1 is
`24.378164291381836` °C, S2 is `12.805026054382324` °C and S1−S2 is
`11.573138236999512` °C. The two means are `19.36863572150469` and
`16.990467556441825` °C. The verifier decodes Float32 with `struct` and independently
performs sequential binary64 accumulation/subtraction, without the product sampler.
Tests also ensure it rejects changed timestamps, values, selection IDs and a zero
fabricated in an unavailable difference cell.

Native calculations took about **1.74 s / 4.56 s**. A 10 ms GUI timer kept firing
(132 / 359 ticks; largest observed interval about 21 / 23 ms). These are scoped
responsiveness observations on short real recordings, not large-file benchmarks.

## Evidence location and remaining gates

Private delivery directory, relative to the primary Desktop checkout:
`build/linux-acceptance-20261121/`. It contains `REPORT.md`, `launch.sh`, the archive
under `linux-build/bundle/`, a verified extraction under `candidate/`, native
screenshots/CSV/receipts under `final-real/` and `final-gaps/`, and the independent
no-Python receipts under `clean-runtime/`. Private captures and original recordings
are not added to Git. The machine-readable public summary is
[the sanitized receipt](diagnostics/2026-11-21-linux-analysis-acceptance.json).

The GUI actions were **automated within the actual application process**, not
performed by a human operator. Screenshots were inspected. Human comfort over long
sessions, other display scaling/themes, other native dialog providers and a fresh
Wayland-specific run remain unverified in this lot. Small windows intentionally
scroll the selection editor; the plot stays visible. Incompatible-session and CSV
cancellation paths were exercised by targeted widget tests, not on these two
single-session real recordings. No maximum-size native GUI stress run was added.

No new hardware capture, Android/Recorder change, Windows test, Actions run, release
or merge was performed. Earlier failed diagnostic iterations remain in the private
delivery: rounded-coordinate test input, focus-sensitive names, verifier setup and
one CLI timeout during an interrupted host run were corrected or rerun; the final
receipts above all passed.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.
