# LMTR initial observation correction and physical readback

## Reproduced defect

On 2026-11-13 the previously installed private Linux runtime was launched normally
with `--open` on unchanged `illustration-01-stored.lmtr` and
`illustration-02-stored.lmtr`. Both displayed event 1/3, `segment_start`, with no
pixels. Neither fixture was damaged and neither required camera/thermometry work.
The initial event declares the segment; the observation is event 2/3. Earlier UI
qualification forcibly sought index 1 and therefore concealed normal-open behavior.

Runtime/test correction:
`85dd25fa4747a2f79b9486702318ab2b9a76b4cc`.
The worker now selects the first observation in the already validated initial
CHUNK before publishing the initial result. It does not scan later CHUNKs, skip
unavailable observations or alter model default/event ordering. If that CHUNK has
no observation, event zero remains selected. Every earlier declaration/gap remains
reachable and clears image/readings. The campaign no longer forces index 1.
Regressions cover both original examples and the bounded/control-only/unavailable cases.
No fixture, pinned reference algorithm, wire rule, coordinate or cache limit changed.

`illustration-01` is a 3×2 synthetic masked grid, not a photograph.
`illustration-02` is a 384×288 constant 25 °C matrix; a uniform colored plane is
correct. Expecting scene texture from either canonical illustration is incorrect.

## Exact private Linux artifact

The clean runtime commit above was rebuilt in the existing rootless Ubuntu 22.04 /
GLIBC 2.35 environment with pinned CPython 3.13.18 and dependency inputs. All 535
manifest members were verified again after extraction into a **new private install**;
the previous installation and negative screenshots were retained separately.

Archive `LMThermal-Desktop-linux-private.tar.gz`, SHA-256:
`299b2a51453c55e4539d4928e9925f5ffe4d3d868f43a63fd51493a00ec83b3d`.
No public release, global installation, association, system-library or plugin change.

| Check | Result |
|---|---|
| Complete source suite / build-root suite | 260 / 260 passing, no skips |
| Frozen canonical outcomes | 51 files, 102 strict/recovery comparisons, original hashes unchanged |
| Dependency/ABI/member audit | Pass, 535 files, highest observed GLIBC requirement 2.35 |
| Frozen offscreen campaign | 11/11, including legacy/LMTX/LMTR refusals and recovery |
| Normal installed native X11 opens, no test flags or seeking | Both illustrations plus the physical source show their initial observation |
| Native real-source widget campaign | Point mapping, ROI/palette/range/resize, separate PNG, metadata, navigation/full check and cooperative Close pass |

The last two checks exposed real Fedora/XWayland windows; only the newly launched
application windows were inspected. The interaction campaign uses automated Qt
input and substitutes only its own file chooser. It does not certify normal picker
interaction, manual operator acceptance, a fresh clean distro/Wayland session or
native Windows on this corrective build. Earlier platform evidence remains historical.
Source bytes were hashed before/after every check and stayed exact.

## Real Pixel/HT-301 file: verified interrupted observation

The separate R4 field mission produced one acknowledged physical Full observation.
Its [exact Android report](https://github.com/LounaMaili/LMThermal/blob/bd2cfa614d64021078e0a5d16646e00cca6f08f8/docs/lmtr-r4/FIRST_PHYSICAL_ATTEMPT.md)
preserves the failed ten-second smoke: diagnostic callback-FPS serialization stopped
intake after about 139 ms. The file has a valid **interrupted** END, not a completed
ten-second sequence. No second physical attempt was performed.

Private `capture.lmtr`, 201601 bytes, SHA-256:
`9432aba49701659ad1cad331f0c097d0f96791ad65e486f0e89d4957bb65d384`.
No real thermal file, oracle, per-frame data or screenshot is committed here.

Independent Python and the corrected source/frozen Desktop agree on strict full
integrity, one observation, zero gap events, and exact original-owner Float32,
native and transport hashes. Full physically stores 442368 temperature bytes and
224256 transport bytes; the 221184-byte native plane is an exact same-observation
view. The mask is absent, with all 110592 stored values finite. Native raw range is
5195–5604; stored temperature range is 20.803895950317383–30.107088088989258 °C,
low `(104,155)`, high `(206,258)`. No temperature is recomputed from raw bytes.

The displayed image is recognizable wall/object structure. Native points, two
inspection ROIs, all palettes, Auto/Locked bounds, resize and separate PNG preserve
the same stored numbers. Literal center `(192,144)` reads raw 5217 and
21.32840919494629 °C; no trailer-center equivalence is inferred. Previous/Next
shows the real blank segment boundaries and restores the observation. Full-file
verification retains `END interrupted`; all checks leave the source unchanged.

This proves reading/analysis of one real physical observation, **not** multi-frame
motion, cadence, a ten-second recording, stable memory or sustained loss-free
recording. The Android field-only decimal fix is host-tested and awaits separately
authorized physical retry before a longer test can be considered. The existing
R4/R5 gates stay open; there is no common Desktop recorder or production R6 change.

## Second physical sequence follow-up

The unchanged Desktop reference `969420ec49e5570599648a186db4617658f0911e`
(runtime `85dd25fa4747a2f79b9486702318ab2b9a76b4cc`) subsequently read the
[second short R4 attempt](https://github.com/LounaMaili/LMThermal/blob/bacfd3a66cb7a63aa8f17d883c01c7ed047fdf6e/docs/lmtr-r4/SECOND_PHYSICAL_ATTEMPT.md).
The fixed Android diagnostic worked, but the unchanged RSS guard stopped the
requested ten-second Full/DEFLATE run at 4.308975303 seconds. Its finalized
interrupted file contains 45 observations and 60 explicit losses (2 preparation,
58 writer), not a loss-free 25 Hz sequence.

Private `capture.lmtr`: 10051773 bytes, SHA-256
`0ca64fc9a9a9507a241f20c1b358d176c476972603f088bd64cafcea9cb76a3f`.
Independent Python and source/installed Linux Desktop verify full integrity and
original-owner Float32/native/transport parity for all 45 available planes. The
native plane is an exact same-observation transport view; no mask is invented.
All107 events, all 60 gaps and exact time seek are checked; blank events clear
readings. Representative first/middle/last frame and gap navigation, play/pause/step,
native point/ROI, all palettes, range, resize, PNG and cooperative worker Close pass.
All45 raw payloads have distinct hashes, without proving controlled target motion.
Stored raw range 5177–5876; temperature range 20.920644760–36.332649231 °C.

This is new Linux multi-frame readback evidence with unchanged source hashes and
unchanged Desktop runtime, not manual operator, native Windows, ten-second recording,
steady-memory or throughput acceptance. The original one-observation file remains
preserved separately. Real files/oracles/images remain private; only these numerical
conclusions and hashes are published. A longer recording is not ready and was not run.

**Native-equivalent temperatures; absolute physical accuracy not yet independently validated.**
