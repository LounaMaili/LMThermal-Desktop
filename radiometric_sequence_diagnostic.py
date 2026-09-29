#!/usr/bin/env python3
"""Replay two APK-backed HT-301 startup sequences with a complete stage history.

Raw masks are statistics only: both ARM thermometrySearch implementations reject
full uint16 words >= 0x4000. No GUI, empirical temperature mapping, or USB sweep.
"""

import argparse
from array import array
from datetime import datetime, timezone
import fcntl
import heapq
import json
import os
from pathlib import Path
import queue
import struct
import threading
import time

from ht301_camera import discover_device, open_camera, read_raw_frame
from radiometric_mode_diagnostic import frame_metrics, ZOOM_ABSOLUTE_CONTROL

# Linux videodev2.h: _IOWR('V', 27/28, struct v4l2_control), two 32-bit fields.
VIDIOC_G_CTRL = 0xC008561B
VIDIOC_S_CTRL = 0xC008561C
PARAMETER_OFFSETS = {
    "correction": 0, "reflection": 4, "ambient": 8,
    "humidity": 12, "emissivity": 16, "distance": 20,
}


def parameter_commands(name: str, value: float, *, official_distance=False) -> list[int]:
    """Encode observed parameter writes; this helper does not send commands.

    ThermViewer uses float32 even for distance. Official distance alone uses
    uint16. NaN is rejected because no experiment needs nonfinite settings.
    """
    import math
    if not math.isfinite(value):
        raise ValueError("Parameter must be finite")
    if official_distance:
        if name != "distance" or value != int(value):
            raise ValueError("Only official integer distance uses uint16")
        payload = struct.pack("<H", int(value))
    else:
        payload = struct.pack("<f", value)
    offset = PARAMETER_OFFSETS[name]
    return [((offset + i) << 8) | byte for i, byte in enumerate(payload)]


class ZoomControl:
    """Use standard V4L2 zoom control; no raw USB requests or driver detach."""

    def __init__(self, device):
        self.fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)

    def get(self):
        data = array("i", [ZOOM_ABSOLUTE_CONTROL, 0])
        fcntl.ioctl(self.fd, VIDIOC_G_CTRL, data, True)
        return data[1]

    def set(self, value):
        data = array("i", [ZOOM_ABSOLUTE_CONTROL, value])
        fcntl.ioctl(self.fd, VIDIOC_S_CTRL, data, True)

    def close(self):
        os.close(self.fd)


def usb_identity(device):
    """Record reconnect evidence without the serial embedded in the by-id path."""
    node = Path(device).resolve().name
    path = Path('/sys/class/video4linux') / node / 'device'
    resolved = path.resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / 'idVendor').exists():
            return {key: (parent / key).read_text().strip()
                    for key in ('idVendor', 'idProduct', 'busnum', 'devnum')}
    raise RuntimeError("Cannot verify the selected camera's USB identity")


class FrameStream:
    """One acquisition thread keeps short command intervals free of blocking reads."""

    def __init__(self, capture):
        self.capture = capture
        self.frames = queue.Queue(maxsize=512)
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while not self.stop.is_set():
                raw = read_raw_frame(self.capture)
                # Receipt time is not the sensor exposure time; V4L2 can buffer frames.
                self.frames.put_nowait((time.monotonic(), raw))
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__

    def check(self):
        if self.error:
            raise RuntimeError(f"Acquisition stopped: {self.error}")

    def next(self):
        self.check()
        try:
            return self.frames.get(timeout=4)
        except queue.Empty as exc:
            raise RuntimeError("No complete transport frame received within four seconds") from exc

    def drain(self):
        result = []
        while True:
            try:
                result.append(self.frames.get_nowait())
            except queue.Empty:
                return result

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
        # Avoid releasing an OpenCV object while a blocked read still owns it.
        if not self.thread.is_alive():
            self.capture.release()


class Experiment:
    """Persist successful stages even when a later control or frame fails."""

    def __init__(self, stream, control, report, output, count=3, settle=15,
                 capture_dir=None, preserve_spatial=False, shutter_settle=None):
        self.stream, self.control = stream, control
        self.report, self.output = report, output
        self.count, self.settle = count, settle
        self.shutter_settle = settle if shutter_settle is None else shutter_settle
        self.origin = time.monotonic()
        self.raw_candidates = []
        self.capture_dir = capture_dir
        self.preserve_spatial = preserve_spatial

    def elapsed(self):
        return round((time.monotonic() - self.origin) * 1000, 3)

    def save(self):
        temporary = self.output.with_suffix(self.output.suffix + '.tmp')
        temporary.write_text(json.dumps(self.report, indent=2, allow_nan=False) + '\n')
        temporary.replace(self.output)

    def metrics(self, item):
        timestamp, raw = item
        metrics = frame_metrics(raw)
        # A nonfinite trailer is treated as unstable transport, not hidden in JSON.
        json.dumps(metrics, allow_nan=False)
        metrics['received_ms'] = round((timestamp - self.origin) * 1000, 3)
        if metrics['image_words_at_most_0x3fff_percent'] == 100.0:
            self.raw_candidates.append(raw)
        return metrics

    def record_frame(self, item, stage_name, phase, index):
        """Save redacted bytes alongside metrics without overwriting earlier runs."""
        metrics = self.metrics(item)
        if self.capture_dir:
            from measurement_diagnostic import sanitize_fixture
            directory = self.capture_dir / stage_name
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f'{phase}-{index:03d}.raw'
            with path.open('xb') as file:
                file.write(sanitize_fixture(item[1], seed=index + 1,
                                            scramble_image=not self.preserve_spatial))
            metrics['saved_frame'] = str(path.relative_to(self.output.parent))
        return metrics

    def snapshot(self, name, discard, count=None):
        """Retain settling observations as well as the selected stable samples."""
        self.stream.drain()
        stage = {'name': name, 'started_ms': self.elapsed(), 'discarded_frames': 0,
                 'settling_frames': [], 'frames': [], 'control_readback': self.control.get()}
        self.report['stages'].append(stage)
        self.save()
        for i in range(discard):
            stage['settling_frames'].append(self.record_frame(self.stream.next(), name, 'settling', i))
            stage['discarded_frames'] += 1
            self.save()
        for i in range(self.count if count is None else count):
            metrics = self.record_frame(self.stream.next(), name, 'frame', i)
            stage['frames'].append(metrics)
            self.save()
            if name == 'E_post_shutter_stability' and metrics['image_words_at_most_0x3fff_percent'] != 100.0:
                raise RuntimeError('Post-shutter transport lost raw14 compatibility')
            if metrics['calibration_coefficients'][0] <= 0:
                raise RuntimeError('Invalid calibration in selected frame; stopping sequence')
        stage['finished_ms'] = self.elapsed()
        self.save()

    def send(self, name, value, planned_ms):
        self.stream.check()
        event = {'name': name, 'value': value, 'planned_ms': round(planned_ms, 3),
                 'started_ms': self.elapsed(), 'readback': None, 'succeeded': False}
        self.report['commands'].append(event)
        self.save()
        self.control.set(value)
        event['succeeded'] = True
        event['completed_ms'] = self.elapsed()
        event['readback'] = self.control.get()
        self.save()

    def official(self):
        """Replay only the three observed official device writes."""
        # Capture latency can extend APK times; never shorten the observed gaps.
        start = time.monotonic()
        for name, value, delay in [('B_output1', 32772, .5),
                                   ('C_range120', 32800, .6),
                                   ('D_refresh', 32768, .5)]:
            deadline = start + delay
            time.sleep(max(0, deadline - time.monotonic()))
            self.send(name, value, (deadline - self.origin) * 1000)
            start = time.monotonic()
            self.snapshot(name, self.shutter_settle if name == 'D_refresh' else self.settle)
            if any(f['image_words_at_most_0x3fff_percent'] != 100.0
                   for f in self.report['stages'][-1]['frames']):
                raise RuntimeError('Official raw stage did not remain 14-bit compatible; stopped')

    def thermviewer(self):
        """Mirror nested Handler posting, including writes that overlap refresh.

        Same-deadline tasks retain insertion order. Nested byte deadlines start
        at their setter invocation, as Android postDelayed does. Runtime jitter
        and Linux control latency are recorded, not called Android timestamps.
        """
        pending = []
        serial = 0
        start = time.monotonic()
        observations = []

        def post(deadline, name, value=None, parameter=None):
            nonlocal serial
            heapq.heappush(pending, (deadline, serial, name, value, parameter))
            serial += 1

        post(start + .020, 'output0', 32773)
        post(start + .030, 'emissivity_setter', parameter=('emissivity', 1.0))
        post(start + .040, 'correction_setter', parameter=('correction', 0.0))
        post(start + .040, 'refresh', 32768)
        self.stream.drain()
        first_command = len(self.report['commands'])
        try:
            while pending:
                deadline, _, name, value, parameter = heapq.heappop(pending)
                time.sleep(max(0, deadline - time.monotonic()))
                self.stream.check()
                if parameter:
                    now = time.monotonic()
                    for i, (command, delay) in enumerate(zip(
                            parameter_commands(*parameter), (0, .010, .020, .020))):
                        post(now + delay, f'{parameter[0]}_byte{i}', command)
                else:
                    self.send(name, value, (deadline - self.origin) * 1000)
                observations.extend(self.stream.drain())
        finally:
            observations.extend(self.stream.drain())
            commands = self.report['commands'][first_command:]
            stages = [{'name': c['name'], 'value': c['value'],
                       'started_ms': c['started_ms'], 'control_readback': c['readback'],
                       'frames': []} for c in commands]
            for item in observations:
                metrics = self.metrics(item)
                eligible = [s for s in stages if s['started_ms'] <= metrics['received_ms']]
                if eligible:
                    eligible[-1]['frames'].append(metrics)
            for stage in stages:
                stage['timing_note'] = ('Receipt interval only, not exposure attribution. '
                    'An empty list means no complete frame arrived in this short interval.')
            self.report['stages'].extend(stages)
            self.save()
        self.snapshot('startup_settled', self.settle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sequence', choices=('baseline', 'official', 'thermviewer'), default='baseline')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--previous-run', type=Path, help='Require a different USB devnum from this report')
    parser.add_argument('--count', type=int, default=3)
    parser.add_argument('--settle', type=int, default=15)
    parser.add_argument('--shutter-settle', type=int, default=75, help='Frames discarded after official shutter; 15 was experimentally insufficient')
    parser.add_argument('--fixture-dir', type=Path)
    parser.add_argument('--capture-dir', type=Path, help='New directory under the report directory; save every stage')
    parser.add_argument('--preserve-spatial', action='store_true', help='Keep image layout in redacted captures; review before sharing')
    parser.add_argument('--stability-count', type=int, default=0, help='Additional consecutive frames after the official shutter stage')
    args = parser.parse_args()
    if args.count < 1 or args.settle < 1 or args.shutter_settle < 1:
        parser.error('count and both settling counts must be positive')
    if args.stability_count < 0 or (args.stability_count and args.sequence != 'official'):
        parser.error('stability-count requires the official sequence and must be nonnegative')
    if args.report.exists():
        parser.error('Report already exists; choose a new path to preserve prior evidence')
    if args.capture_dir and (args.capture_dir.exists() or not args.capture_dir.is_relative_to(args.report.parent)):
        parser.error('capture-dir must be a new directory under the report directory')
    if args.preserve_spatial and not args.capture_dir:
        parser.error('preserve-spatial requires capture-dir')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    device = discover_device()
    identity = usb_identity(device)
    if (identity['idVendor'], identity['idProduct']) != ('1514', '0001'):
        parser.error('Selected device is not HT-301')
    if args.previous_run:
        previous = json.loads(args.previous_run.read_text())['usb_identity']
        if (identity['busnum'], identity['devnum']) == (previous['busnum'], previous['devnum']):
            parser.error('Physically reconnect the camera before this separate experiment')
    control = ZoomControl(device)
    stream = None
    report = {'captured_at_utc': datetime.now(timezone.utc).isoformat(),
              'sequence': args.sequence, 'usb_identity': identity,
              'control_id': hex(ZOOM_ABSOLUTE_CONTROL), 'commands': [], 'stages': [],
              'spatial_layout_preserved': args.preserve_spatial,
              'status': 'running', 'mask_interpretation': 'Diagnostic only; native search does not mask.'}
    experiment = None
    try:
        report['initial_readback'] = control.get()
        if args.sequence != 'baseline' and report['initial_readback'] != 0:
            raise RuntimeError('Expected zoom readback 0 after a physical reconnect')
        stream = FrameStream(open_camera(device))
        experiment = Experiment(stream, control, report, args.report, args.count, args.settle,
                                args.capture_dir, args.preserve_spatial, args.shutter_settle)
        experiment.snapshot('A_baseline', args.settle)
        if args.sequence != 'baseline':
            getattr(experiment, args.sequence)()
        if args.stability_count:
            experiment.snapshot('E_post_shutter_stability', 0, args.stability_count)
        report['status'] = 'complete'
        final = report['stages'][-1]['frames']
        report['all_final_image_words_fit_native_lookup'] = bool(final) and all(
            m['image_words_at_most_0x3fff_percent'] == 100.0 for m in final)
        if args.fixture_dir and report['all_final_image_words_fit_native_lookup']:
            from measurement_diagnostic import sanitize_fixture
            args.fixture_dir.mkdir(parents=True, exist_ok=True)
            report['sanitized_fixtures'] = []
            for i, raw in enumerate(experiment.raw_candidates[-args.count:]):
                path = args.fixture_dir / f'{args.sequence}-{i:03d}.raw'
                with path.open('xb') as file:
                    file.write(sanitize_fixture(raw, seed=i + 1))
                report['sanitized_fixtures'].append(str(path))
    except Exception as exc:
        report.update(status='stopped', error=str(exc))
    finally:
        if stream:
            stream.close()
        control.close()
        if experiment:
            experiment.save()
        else:
            args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'report': str(args.report), 'status': report['status'],
                      'error': report.get('error'), 'stages': len(report['stages'])}))
    if report['status'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
