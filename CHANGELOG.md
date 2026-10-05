# Changelog

All notable changes to LMThermal-Desktop will be documented in this file.

## [Unreleased]

### Added (LMTX v1 import and generic offline analysis — 2026-10-05)
- Added a strict camera-free LMThermal Exchange Format v1 still reader using the accepted canonical contract: bounded ZIP/JSON/image validation, exact inventory/CRC/SHA/length/dtype/shape checks, required-feature/version handling, safe paths and clean error codes. Unknown optional JSON retains semantic numeric precision; opaque payload bytes remain exact.
- Added an immutable owned offline measurement model with actual native geometry, optional Celsius/mask/native planes, provenance/evidence and presentation. Point and half-open ROI measurements use valid saved float32 values, first row-major extrema ties and sequential float64 means; import never recomputes thermometry. Preview-only/all-invalid sources produce no Celsius readings or legend.
- Added asynchronous LMTX opening and camera-free `--lmtx`, content-based still dispatch, source/cancellation guards, saved native analysis overlays, palette fallback disclosure and presentation-only quarter-turn/mirror mapping. Exact source planes remain unchanged by palette/range/ROI/rendering; PNG saving protects original sources without a Unix hard-link requirement. Edited LMTX persistence is deferred.
- Adapted validated legacy stills and recording frames into common offline analysis while retaining strict legacy loaders, exact planes, original metadata, distinct centers, honest transport availability and recording timeline/cache/gap/recovery behavior. Live acquisition/controls/session and thermometry arithmetic remain unchanged; Linux acquisition imports occur only on explicit Connect.
- Added Pillow for bounded PNG/JPEG decoding and an authoritative runtime requirements file. Copied the exact eleven Android conformance archives and inventory from producer commit `1cbdd776a485c43e1e7cfa104e3741d1b7e99658`, with provenance and hash checks; expanded parser/model/UI/legacy tests to 189 passing hardware-independent tests.
- Validated the actual Android SAF export against independent source-bit evidence, exact ROI/presentation/native/transport/provenance and unchanged archive hash, plus operator hover/ROI/resize/palette checks. Local median loads were 6.7 ms for the real HT still and 24.9 ms for a synthetic 1024×1024 grid. Documented local resource budgets and curated measurements; private scene files remain excluded. Real Windows offline validation remains pending, so Desktop #1 stays open. Absolute physical accuracy remains independently unvalidated.

### Added (Offline radiometric sequence playback — 2026-09-30)
- Added a camera-independent timeline/playback model with exact stored uint16/float32 frames, lazy two-chunk LRU access, elapsed-time 0.5×/1×/2×/4× playback and explicit valid/gap/drop/uncommitted entries. No thermometry is recomputed.
- Added asynchronous coalesced validation/loading, all-sample slider/step/beginning/end/play/pause controls, blank invalid/drop views, separately preserved centers, recorded/inspection ROI policy and existing Celsius rendering/cursor/legend reuse. Live, still and sequence modes finalize resources and reject obsolete results.
- Added selected PNG-only rendering export with whole-bundle protection; radiometric still extraction remains deferred because recording v1 has no transport evidence. Recording/still format semantics, camera initialization and measurement arithmetic are unchanged.
- Extended loader checks for monotonic timeline timing and existing drop flags/reasons. Added hardware-independent cache/timing/integrity/recovery/UI tests and measured about 5.3 ms median Qt sample navigation with a bounded 21.23 MB array cache.
- Documented camera-free CLI, recovery/timing/ROI/source protection and explicit validation provenance: the prior live bundles were no longer available after temporary storage removal, so operator/automated validation used fixture-derived 5/10/25 Hz histories. Original-live-bundle playback and independent physical accuracy remain unverified.

### Added (Radiometric sequence recording — 2026-09-30)
- Added distinct v1 `.lmthermal` directory recordings with exact raw14/float32 matrices, per-frame parameters/lookup inputs, scalar/ROI timeline, hashes and native-coordinate accuracy warning. Still-capture v1 and thermometry remain unchanged.
- Added latest-observation 1/2/5/10/25 Hz sampling (default 5), a four-frame handoff, compressed 16-frame incremental chunks, explicit validity gaps/overload drops and atomic chunk/manifest publication. Interrupted bundles preserve committed chunks; a camera-independent bounded chunk loader validates shapes/dtypes/hashes/mappings/summaries.
- Added live-only start/stop/rate/status controls, mutually exclusive with CSV scalar logging, and safe finalization on disconnect, camera termination, close and offline opening.
- Benchmarked NPZ compression with saved data and validated two 60-second 5 Hz runs, 30-second 10 Hz and 15-second 25 Hz stress on the HT-301. Median view FPS remained 25; 5/10 Hz had no recorder drops. The stress run recorded 46 overload drops and 265 matrices (about 17.25 stored/s), so 25 Hz remains experimental. Independent physical accuracy remains unvalidated.
- Documented format/recovery/rate tradeoffs and persistent aggregate live results; expanded hardware-independent persistence, backpressure, cadence and UI lifecycle tests.

### Added (Live measurement time series — 2026-09-30)
- Added a camera-independent measurement logger with a bounded latest-observation slot, monotonic 0.5/1/2/5/10 Hz sampling (1 Hz default), ready-frame scalar/ROI samples and explicit empty-value gap rows for invalid or not-new observations. It does not recompute thermometry or record image/video data.
- Added incremental UTF-8 CSV and versioned session metadata with distinct literal/trailer centers, native high/low coordinates, per-row ROI geometry/statistics, start settings, accuracy warning, counts and completion status. Exclusive incomplete files and no-overwrite final publication preserve interrupted/error evidence.
- Added live-only Start/Stop/rate/duration/count/path controls; disconnect, close, camera failure and offline opening finalize logs. Presentation changes remain independent of measurement logging.
- Validated 1 Hz real-camera CSV/JSON publication, natural validity gaps/recovery, verified visible-hand/background ROI changes and clearing, and unchanged same-frame numbers across rendering controls. Initial runs retained median 25 view FPS; the screenshot-instrumented interactive retry had median 21. Physical accuracy remains unvalidated.

### Added (Offline radiometric viewer — 2026-09-30)
- Added a camera-independent v1 capture loader with immutable saved matrices, companion hashes/completion checks, bounded NPZ header validation, metadata/coordinate consistency checks, optional transport evidence and verified saved ROI statistics. Reopening uses stored Celsius values without recomputing thermometry.
- Added explicit saved-capture inspection, original metadata, cursor/raw14, distinct centers, extrema, restored/new ROI and existing Celsius palette/range controls. Opening stops live acquisition; reconnect closes saved mode; queued old worker signals and pending startup connection cannot contaminate offline data.
- Added no-overwrite PNG-only re-render saving and camera-free `--capture` launch. Original radiometric sets remain unchanged.
- Completed operator offline hover/resize/ROI/palette/range validation and saved auto/derived-locked/ROI smoke checks, including source integrity and corrupt-set rejection. Absolute physical accuracy remains unvalidated.

### Added (Interactive ROI measurements — 2026-09-30)
- Added one native rectangular ROI selected by left-click/drag, with edge clipping, persistent geometry across resize/palette/range changes, and a clear action. A click selects one pixel; stored bounds are half-open.
- Added direct current-temperature-slice min/max/mean, pixel count and native extrema coordinates. Numerical ROI readings disappear on non-ready frames and resume for the same geometry on recovery. The sidebar scrolls to keep the additional controls accessible.
- Added optional same-frame ROI geometry/statistics in capture format v1 JSON while retaining the complete radiometric NPZ matrices and clean PNG rendering.
- Completed operator validation of a cooler background ROI beside the visible hand, resize alignment, and unchanged same-frame ROI statistics across palette and auto/locked range changes. Absolute physical accuracy remains unvalidated.

### Added (Radiometric capture/export — 2026-09-29)
- Added ready-frame-only PyQt capture from one frozen `MeasurementFrame` and effective palette/range; display, held, shutter and unsettled states disable saving.
- Added no-overwrite v1 PNG/NPZ/JSON capture sets: a Celsius rendering, lossless native raw14/float32 matrix plus exact transport bytes, and versioned settings/calibration/presentation metadata with integrity hashes. Temporary-file publication rolls back ordinary failures; absolute physical accuracy remains unvalidated.

### Added (Celsius palette and range — 2026-09-29)
- Added a pure display renderer that maps only valid native-equivalent temperature matrices through an automatic 2nd/98th-percentile Celsius scale or exact locked Celsius bounds into White hot, Black hot, Inferno, Iron-like/Hot, or Turbo colors.
- Added an effective-range Celsius legend and live palette/auto/locked controls. Display and unsettled frames remain aiming previews without current Celsius colors, legend or readings; palette changes do not alter raw14, session validity, or thermometry.

### Added (PyQt radiometric MVP — 2026-09-29)
- Replaced the obsolete Y-based PyQt thermometry path with a camera worker that owns stable HT-301 discovery, latest-frame acquisition, the validated radiometric session, controls, and clean shutdown outside the GUI thread.
- Added a native-orientation 384 × 288 display with explicit initialization, session validity, view FPS, true matrix cursor values, validated high/low markers, and separately labeled literal/trailer center readings. Pure aspect-preserving coordinate helpers reject letterbox positions and retain raw14 indices independently of display normalization.
- Removed misleading legacy range-lock, palette, point persistence, and screenshot controls from the first measurement MVP. Absolute physical accuracy remains unvalidated.

### Fixed (Live diagnostic preview validation — 2026-09-29)
- Enlarged the OpenCV diagnostic window and rendered the center crosshair last at two-pixel thickness after a live display-mode window capture showed that the original one-pixel marker disappeared when HighGUI scaled the image down.
- Allowed the noninteractive session to skip an invalid startup frame while requiring three consecutive valid display frames before any initialization write; the live camera produced a mixed first frame after reconnect.

### Added (Radiometric measurement session and diagnostic preview — 2026-09-29)
- Added a PyQt-independent normal-range HT-301 session with explicit display, transition, shutter, unsettled, ready and error states. It uses only the confirmed `32772 -> 32800 -> 32768` controls, readbacks and observed-frame gates.
- Added a measurement object containing original raw14 pixels, a native-equivalent 288 × 384 temperature matrix, settings/calibration trace, distinct trailer and literal-center readings, extrema and timestamps. Held, malformed, inconsistent and undefined frames cannot be reported as ready measurements.
- Promoted the float32-faithful lookup to `native_equivalent_thermometry.py` while retaining the experimental CLI compatibility path; physical accuracy remains unvalidated.
- Added an OpenCV diagnostic preview for display Y and display-only normalized raw14, with state/transient overlays, center and extrema markers, optional ROI guides and valid-frame click inspection. Added saved-fixture state/recovery and pure preview tests; no PyQt measurement integration.

### Added (Operator-confirmed warm-target discrimination — 2026-09-29)
- Captured a visible hand against cooler background after the documented official raw14 transition; selected 23 distinct post-shutter frames for relative ROI and center/high/low analysis.
- Saved one identifier-redacted, spatially preserved steady hand fixture with provenance, a fixed ROI plan, a compact results report, and a hardware-independent regression test.
- Detected and excluded an additional 31-frame held image interval after the 75-frame shutter discard and one malformed frame; neither contributes to reported repeatability. This does not establish absolute temperature accuracy.

### Changed (Settled radiometric validation — 2026-09-29)
- Exposed the complete 288 × 384 experimental native-equivalent temperature matrix for valid raw14 frames, with strict display-word and undefined-entry rejection.
- Recorded the observed roughly 1.3-second shutter hold and 75-frame experimental settling window; separate transient and settled fixtures, liveness, trailer-coordinate, and region-stability evidence are retained.
- Updated the capture procedure to inspect the current camera mode before replay and to use independent surface references for a later physical-accuracy test.

### Added (Full radiometric sequence — 2026-09-27)
- Added staged official and ThermViewer replays with exact decoded parameter commands, readbacks, command timing, settling observations and high-bit statistics. No raw vendor requests or GUI changes are involved.
- Observed genuine raw14 output after official `zoom_absolute=32772`; the separately reset ThermViewer type-0 startup changed emissivity but retained display words.
- Added an experimental 16384-entry official range-120/lens-68 lookup and an optional hash-pinned native reference harness. Every entry of the initial fixture agrees with executed x86_64 APK arithmetic, including undefined values; physical accuracy is not yet validated.
- Preserved the interrupted work in timestamped research archives, and retained the surviving sanitized raw fixture/native table. Labeled the earlier temporary-report results as transcript-derived evidence.
- Added persistent stage capture, no-overwrite checks, spatial/trailer comparisons and an offline controlled-target validation tool with independent-reference metadata and temporal drift reporting.

### Corrected (Lookup inputs — 2026-09-27)
- Separated the FPA word at frame byte 221186 from the calibration-temperature word at 223490 (`word/10-273.15`).
- Reconstructed the native lens-68 distance multiplier and final correction using explicit float32/double arithmetic. Masked image-word statistics remain diagnostics only; invalid full pixel indices are rejected.
- Updated the measurement audit, fixture provenance, agent measurement status, README and controlled-validation procedure while preserving repository completion workflow requirements.

### Added (Radiometric-mode diagnostic — 2026-09-26)
- Added a PyQt-independent, baseline-first HT-301 mode diagnostic reporting 14-bit word compatibility, trailer center/high/low indices, calibration inputs, and exact before/after frame metrics.
- Added an explicit, single-control ThermViewer HT-301 output-type-zero test (`zoom_absolute=32773`) through standard V4L2, with settling frames and hardware-independent tests.
- Observed that the documented control alone retained `0x80YY` display words in three live post-control frames; no Celsius lookup or GUI measurement was enabled.

### Corrected (Native thermometry audit — 2026-09-26)
- Renamed diagnostic parameter fields to match the APK's correction, reflected temperature, ambient temperature, humidity, emissivity, distance, and copied calibration coefficients.
- Added 14-bit lookup compatibility and trailer center/high/low index reporting for saved or live frames; the current saved image words exceed that range.
- Moved the historical rejected Y-based calculation behind explicit legacy labeling and evaluated the documented `CalcFixRaw` first stage with ambient temperature.
- Updated measurement and fixture documentation to identify field 356 as a duplicated calibration coefficient while preserving the end-of-task workflow requirements.

### Added
- A PyQt-independent HT-301 measurement diagnostic with stable camera discovery, exact raw-frame capture, known parameter decoding, and intermediate arithmetic traces.
- Two sanitized captures and hardware-independent tests for frame size, parameter offsets, image/trailer separation, metadata exclusion, and reproducibility.
- A measurement audit recording the evidence, rejected legacy calculation, and remaining thermometry questions.

### Changed
- Added persistent agent workflow instructions requiring documentation and changelog maintenance for meaningful changes.
- Completed task branches must be committed and pushed to GitHub for remote review; agents must not merge them into `main` automatically.

### Corrected
- Measurement diagnostic image statistics use only the 384 × 288 thermal image area; the four trailing transport rows are non-image data.
- Documentation no longer presents the existing per-pixel Celsius calculation or parameter field 356 as a validated live center temperature.

## [0.1.0] - 2026-04-19

### Added
- Initial PyQt6 desktop thermal viewer for the Infiray HT-301 / T3-317-13.
- Real-time YUYV capture through V4L2.
- Per-pixel click and hover measurement prototype.
- Min/max markers.
- Multiple color palettes.
- Zoom and frame-information controls.
- Image capture with overlays.
