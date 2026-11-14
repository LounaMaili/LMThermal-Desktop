#!/usr/bin/env python3
"""Camera-free LMTR timeline viewer; source bytes and measurement coordinates stay intact."""
import argparse
from pathlib import Path
import sys
import threading

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QComboBox, QDoubleSpinBox,
    QFileDialog, QCheckBox, QMessageBox, QPlainTextEdit, QDialog, QToolButton, QSizePolicy)
from PyQt6.QtCore import Qt, QSignalBlocker

from lmthermal_viewer import ThermalImageWidget, CelsiusLegend
from lmtr_reader import LmtrPlaybackModel, format_timing_evidence
from offline_measurement import Rectangle, save_offline_png
from lmtx_json import dumps as metadata_text
from celsius_palette import PALETTES, CelsiusRange

WARNING = 'Native-equivalent temperatures; absolute physical accuracy not yet independently validated.'


def native_space_key(frame):
    """An inspection belongs to a source/session/native grid, never just fitting bounds."""
    entry = frame.manifest['entry']
    stream = next(s for s in frame.manifest['segment']['streams'] if s['stream_id'] == entry['stream_id'])
    return (frame.source_path, frame.manifest['recording_id'], entry['stream_id'],
            stream['session_id'], stream['source']['module_id'], stream['source']['model_id'],
            frame.geometry.shape)


class LmtrWorker(QThread):
    """One owned model and bounded request/result slots, with an open-generation barrier.

    A navigation request can replace navigation for the SAME ready file. It cannot
    replace a pending open or cause an obsolete model to publish for a newer file.
    Stop cancels between records, clears retained slots and closes the descriptor.
    No thread termination or certified single-operation cancellation latency exists.
    """
    available = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.condition = threading.Condition()
        self.pending = self.result = None
        self.signalled = False
        self.stopped = threading.Event()
        self.requested_generation = 0
        self.ready_generation = None
        self.close_error = None

    def request(self, token, action, value=None, *, generation=0):
        """Return refusal without overwriting a newer source-opening transaction."""
        with self.condition:
            if self.stopped.is_set(): return False
            if action == 'open':
                if generation <= self.requested_generation: return False
                self.requested_generation = generation
                self.ready_generation = None
            elif generation != self.requested_generation or self.ready_generation != generation:
                return False
            self.result = None
            self.pending = token, generation, action, value
            self.condition.notify()
            return True

    def take(self):
        with self.condition:
            value, self.result = self.result, None
            self.signalled = False
            return value

    def stop(self):
        """Drop unconsumed frame owners immediately; the active model closes on its worker."""
        self.stopped.set()
        with self.condition:
            self.pending = self.result = None
            self.signalled = False
            self.ready_generation = None
            self.condition.notify()

    def _cancelled(self, generation):
        """Source replacement cancels old work at the same reference-reader boundaries as Stop."""
        with self.condition:
            return self.stopped.is_set() or generation != self.requested_generation

    def _close(self, model):
        """Retain a bounded cleanup diagnostic, never an exception/payload graph."""
        if model is None: return
        try: model.close()
        except Exception as error:
            self.close_error = (type(error).__name__ + ': ' + str(error))[:8192]

    def run(self):
        model = None
        try:
            while not self.stopped.is_set():
                with self.condition:
                    while self.pending is None and not self.stopped.is_set(): self.condition.wait()
                    if self.stopped.is_set(): break
                    token, generation, action, value = self.pending
                    self.pending = None
                # Do not keep the preceding result/frame alive in loop locals while
                # validating another CHUNK or an entire source file.
                result = frame = state = None
                try:
                    if action == 'open':
                        self._close(model)
                        model = None
                        if self.close_error: raise OSError('Previous source close failed: '+self.close_error)
                        model = LmtrPlaybackModel(value[0], recover=value[1], cancelled=lambda source_generation=generation: self._cancelled(source_generation))
                        # Event zero commonly declares the segment and has no image.
                        # Land on the first initial-CHUNK observation, keeping every
                        # preceding event accessible through Previous/seek.
                        initial = model.initial_observation_index()
                        if initial is not None:model.seek(initial)
                    elif model is None: raise ValueError('Open a recording first')
                    elif action == 'verify': model.verify_full()
                    elif action == 'seek': model.seek(value)
                    elif action == 'step': model.step(value)
                    elif action == 'time': model.seek_time_ns(value)
                    elif action == 'toggle': model.pause() if model.playing else model.play()
                    elif action == 'tick': model.advance()
                    elif action == 'speed': model.set_speed(value)
                    frame = model.frame()
                    state = {'index': model.index, 'count': model.entry_count,
                             'duration_s': model.duration_s, 'entry': model.entry,
                             'playing': model.playing, 'report': model.report,
                             'header': model.header, 'cache_bytes': model.cache_bytes,
                             'entry_metadata': model.original_entry(model.index) if model.entry_count else None,
                             'timing': model.timing_evidence(model.index) if model.entry_count else None,
                             'source_path': model.path, 'source_generation': generation}
                    result = token, frame, state, None
                except Exception as error:
                    self._close(model)
                    model = None
                    result = token, None, None, (type(error).__name__ + ': ' + str(error))[:8192]
                with self.condition:
                    if self.stopped.is_set(): break
                    if generation != self.requested_generation or self.pending is not None: continue
                    self.ready_generation = generation if model is not None else None
                    self.result = result
                    emit = not self.signalled
                    self.signalled = True
                if emit: self.available.emit()
                if self.close_error: break  # Never allocate another owner after uncertain cleanup.
        finally:
            self._close(model)
            self.stop()


class LmtrWindow(QMainWindow):
    """Diagnostic offline UI built from existing native-grid image/ROI/palette components."""
    def __init__(self, path=None, *, recover=False):
        super().__init__()
        self.setWindowTitle('LMThermal — LMTR offline recording')
        self.resize(1050, 760)
        self.frame = self.state = None
        self.token = 0
        self._in_flight = False
        self._generation = 0
        self._space_key = None
        self._closing = False
        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        row = QHBoxLayout(); layout.addLayout(row)
        self.open_button = QPushButton('Open .lmtr'); row.addWidget(self.open_button)
        self.recover = QCheckBox('Recover incomplete file'); self.recover.setChecked(recover); row.addWidget(self.recover)
        self.recover.setToolTip('Explicit verified-prefix recovery only. Unknown tail stays unknown; the source is never repaired.')
        self.save_button = QPushButton('Save PNG…'); row.addWidget(self.save_button)
        self.details_button = QToolButton(); self.details_button.setText('Diagnostics')
        self.details_button.setCheckable(True); row.addWidget(self.details_button)
        self.details_button.setToolTip('Original metadata and explicit full-file integrity verification')
        # Keep native controls reachable on laptop-size windows. Splitting rows
        # changes presentation only, never geometry, source bytes or worker admission.
        self.details_panel = QWidget(); row = QHBoxLayout(self.details_panel)
        row.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.details_panel)
        self.metadata_button = QPushButton('Original metadata'); row.addWidget(self.metadata_button)
        self.verify_button = QPushButton('Verify entire file / prefix'); row.addWidget(self.verify_button)
        self.validation_status = QLabel('No source'); self.validation_status.setWordWrap(True)
        row.addWidget(self.validation_status, 1)
        self.details_panel.hide(); self.details_button.toggled.connect(self.details_panel.setVisible)
        self.status = QLabel('Offline — no camera'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.timing_status=QLabel('Timing unavailable');self.timing_status.setWordWrap(True)
        self.timing_status.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.timing_status.setFixedHeight(self.timing_status.fontMetrics().lineSpacing()*3+4)
        layout.addWidget(self.timing_status)
        images = QHBoxLayout(); layout.addLayout(images, 1)
        self.image = ThermalImageWidget(); images.addWidget(self.image, 1)
        self.legend = CelsiusLegend(); images.addWidget(self.legend)
        self.slider = QSlider(Qt.Orientation.Horizontal); layout.addWidget(self.slider)
        controls = QHBoxLayout(); layout.addLayout(controls)
        self.previous = QPushButton('Previous'); self.play = QPushButton('Play'); self.next = QPushButton('Next')
        for b in (self.previous, self.play, self.next): controls.addWidget(b)
        self.speed = QComboBox()
        for rate in (.5,1,2,4): self.speed.addItem(str(rate)+'×', rate)
        self.speed.setCurrentIndex(1); controls.addWidget(self.speed)
        self.seconds = QDoubleSpinBox(); self.seconds.setDecimals(6); self.seconds.setSuffix(' s intake'); controls.addWidget(self.seconds)
        self.seek_button = QPushButton('Seek time'); controls.addWidget(self.seek_button)
        controls = QHBoxLayout(); layout.addLayout(controls)
        self.palette = QComboBox(); self.palette.addItems(PALETTES); self.palette.setCurrentText('Inferno'); controls.addWidget(self.palette)
        self.auto = QCheckBox('Auto range'); self.auto.setChecked(True); controls.addWidget(self.auto)
        self.auto.setToolTip('Display-only percentile bounds; uncheck to edit the locked Celsius bounds')
        self.minimum = QDoubleSpinBox(); self.maximum = QDoubleSpinBox()
        for spin, value in ((self.minimum,20),(self.maximum,40)):
            spin.setRange(-1e30,1e30); spin.setValue(value); spin.setSuffix(' °C'); controls.addWidget(spin)
        self.clear = QPushButton('Clear ROI'); controls.addWidget(self.clear)
        self.range_status = QLabel(''); layout.addWidget(self.range_status)
        self.readings = QLabel('Readings unavailable'); layout.addWidget(self.readings)
        self.point = QLabel('Point unavailable'); layout.addWidget(self.point)
        self.roi_label = QLabel('Inspection ROI: none'); layout.addWidget(self.roi_label)
        # Source values/ROI updates must not establish a wider minimum window or
        # move the image during a drag. Reserve two lines, including the empty state.
        for label in (self.status, self.readings, self.point, self.roi_label):
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.roi_label.setFixedHeight(self.roi_label.fontMetrics().lineSpacing()*2+4)
        warning = QLabel(WARNING); warning.setWordWrap(True); layout.addWidget(warning)
        self.worker = LmtrWorker(self); self.worker.available.connect(self._receive); self.worker.finished.connect(self._worker_finished); self.worker.start()
        self.timer = QTimer(self); self.timer.setInterval(40); self.timer.timeout.connect(self._tick)
        self.open_button.clicked.connect(self._choose)
        self.slider.valueChanged.connect(lambda v:self._request('seek', v))
        self.previous.clicked.connect(lambda:self._request('step', -1)); self.next.clicked.connect(lambda:self._request('step', 1))
        self.play.clicked.connect(lambda:self._request('toggle', clear=False))
        self.speed.currentIndexChanged.connect(lambda:self._request('speed', self.speed.currentData(), clear=False))
        self.seek_button.clicked.connect(lambda:self._request('time', int(self.seconds.value()*1e9)+int(self.state['report']['summary']['first_timeline_ns'] or 0)) if self.state else None)
        self.palette.currentTextChanged.connect(self._presentation); self.auto.toggled.connect(self._presentation)
        self.minimum.valueChanged.connect(self._presentation); self.maximum.valueChanged.connect(self._presentation)
        self.image.hovered.connect(self._point); self.image.roi_changed.connect(self._roi)
        self.clear.clicked.connect(self.image.clear_roi); self.save_button.clicked.connect(self._save)
        self.metadata_button.clicked.connect(self._metadata)
        self.verify_button.clicked.connect(lambda:self._request('verify'))
        self.save_button.setEnabled(False); self.metadata_button.setEnabled(False)
        self._sync_controls()
        self._presentation()
        if path: self.open_path(path)
    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self,'Open read-only LMTR', '', 'LMTR recording (*.lmtr)')
        if path: self.open_path(Path(path))
    def _sync_controls(self):
        """Disable actions for an unopened/closing source; only same-file scrubs coalesce."""
        ready = self.state is not None and not self._closing
        idle = ready and not self._in_flight
        self.slider.setEnabled(ready)
        for control in (self.previous, self.next, self.play, self.speed, self.seek_button,
                        self.seconds, self.verify_button, self.metadata_button):
            control.setEnabled(idle)
        self.open_button.setEnabled(not self._closing and not self.worker.stopped.is_set())
        self.save_button.setEnabled(idle and self.frame is not None and self.image.image is not None)

    def open_path(self, path):
        if self._closing: return False
        self.timer.stop()
        self.state = None
        self._space_key = None
        self._generation += 1
        self.image.clear_roi()
        self.image.pointer = None
        self.image._mouse_widget = None
        return self._request('open', (Path(path), self.recover.isChecked()))

    def _clear_current(self, message='Readings unavailable — loading'):
        self.frame = None
        self.timing_status.setText('Timing unavailable — loading')
        self.image.set_observation(None)
        self.image.setEnabled(False)
        self.image._roi_anchor = None  # A load/gap cancels the gesture, not retained inspection geometry.
        self.legend.set_presentation(None, self.palette.currentText())
        self.readings.setText(message)
        self.point.setText('Point unavailable')
        self.roi_label.setText('ROI unavailable')
        self.save_button.setEnabled(False)

    def _request(self, action, value=None, *, clear=True):
        if self._closing or (action != 'open' and self.state is None): return False
        if self._in_flight and action not in ('open', 'seek'): return False
        token = self.token + 1
        if not self.worker.request(token, action, value, generation=self._generation): return False
        self.token = token
        if clear: self._clear_current()
        self._in_flight = True
        if action == 'open': self.validation_status.setText('Opening — full-file integrity not yet checked')
        self._sync_controls()
        return True

    def _tick(self):
        # A slow selected read must not be endlessly superseded by timer requests.
        # User scrubs still replace the pending slot; no catch-up frame queue exists.
        if not self._in_flight: self._request('tick', clear=False)
    def _receive(self):
        result = self.worker.take()
        if self._closing or result is None or result[0] != self.token: return
        self._in_flight = False
        _, self.frame, state, error = result
        if error:
            self.timer.stop()
            self.state = None
            self._clear_current('Readings unavailable')
            self.status.setText('Read failed: '+error)
            self.validation_status.setText('No current verified source')
            self._sync_controls()
            return
        self.state = state
        entry = state['entry']; report = state['report']
        block = QSignalBlocker(self.slider); self.slider.setRange(0,max(0,state['count']-1)); self.slider.setValue(state['index']); del block
        self.seconds.setRange(0,state['duration_s'])
        completeness = 'RECOVERED PREFIX — unknown tail' if report['status']=='recovered' else 'END '+str(report['declared_end_status'])
        summary = report['summary']
        position = f"event {state['index']+1 if entry else 0}/{state['count']}"
        # Indexed totals are explicitly qualified, not promoted to full-file verification.
        self.status.setText(f"{completeness} · {position}" + (f' · {entry.elapsed_s:.3f} s' if entry else '')
            + f"\nIndexed: {summary['observations']} observations · {summary['temperature_planes']} temperature planes · "
              f"{int(summary['observations'])-int(summary['temperature_planes'])} absent measurements · {summary['gap_events']} gap events")
        timing=format_timing_evidence(state.get('timing'))
        self.timing_status.setText(timing);self.timing_status.setToolTip(timing)
        self.validation_status.setText(f"{report['integrity_scope']} (file) · "
            + ('selected CHUNK verified' if entry else 'no CHUNK selected'))
        self.image.empty_text = ('Gap — '+entry.reason if entry and entry.state == 'gap'
                                 else entry.reason if entry else 'Empty recording')
        if self.frame is not None:
            key = native_space_key(self.frame)
            if self._space_key is not None and key != self._space_key:
                self.image.clear_roi()
                self.image.pointer = None
                self.image._mouse_widget = None
            self._space_key = key
        self.image.set_observation(self.frame)
        self.play.setText('Pause' if state['playing'] else 'Play')
        if state['playing']: self.timer.start()
        else: self.timer.stop()
        self.metadata_button.setEnabled(True)
        self._presentation()
        self.image.setEnabled(self.image.image is not None)
        self._sync_controls()
        if self.frame and self.frame.has_readings:
            self.readings.setText(f'Native grid {self.frame.geometry.width}×{self.frame.geometry.height} | min {self.frame.low_c:.6g} °C at {self.frame.low_xy} | max {self.frame.high_c:.6g} °C at {self.frame.high_xy}')
        else: self.readings.setText('Readings unavailable — no valid stored temperature plane')
        self._roi(self.image.roi); self._point(self.image.pointer)
    def _presentation(self, *unused):
        self.minimum.setEnabled(not self.auto.isChecked()); self.maximum.setEnabled(not self.auto.isChecked())
        lower, upper = self.minimum.value(), self.maximum.value()
        if upper <= lower and not self.auto.isChecked():
            self.range_status.setText('Invalid locked range: Max must exceed Min. Last valid presentation retained.')
            self.range_status.show()
            self.legend.set_presentation(self.image.effective_bounds, self.image.palette)
            return
        self.range_status.setText('')
        self.range_status.hide()
        # Automatic bounds must not be disabled by unfinished manual edits. The
        # shared widget still validates its unused locked fallback, so keep it valid.
        if upper <= lower: lower, upper = 20.0, 40.0
        self.image.set_presentation(self.palette.currentText(),self.auto.isChecked(),lower,upper)
        self.legend.set_presentation(self.image.effective_bounds,self.palette.currentText())
    def _point(self, position):
        value = self.frame.point(*position) if self.frame and position else None
        self.point.setText('Point unavailable' if value is None else f'Native {position}: {value:.8g} °C; original raw '+str(None if self.frame.raw14 is None else int(self.frame.raw14[position[1],position[0]])))
    def _roi(self, roi):
        if roi is None: self.roi_label.setText('Inspection ROI: none'); return
        result = self.frame.roi_statistics(Rectangle(roi.x1,roi.y1,roi.x2,roi.y2)) if self.frame else None
        self.roi_label.setText('ROI unavailable' if result is None or not result.valid_pixel_count else
            f'Native ROI [{roi.x1},{roi.x2}) × [{roi.y1},{roi.y2}): min {result.min_c:.8g}, max {result.max_c:.8g}, mean {result.mean_c:.8g} °C · {result.valid_pixel_count}/{result.pixel_count} valid pixels')
    def _save(self):
        # A native destination chooser runs a nested event loop. Freeze all immutable
        # export inputs BEFORE opening it: playback/file switches may continue meanwhile.
        capture = self.frame
        if capture is None or self.image.image is None: return
        palette = self.image.palette  # Actual retained colors can differ during invalid manual edits.
        bounds = self.image.effective_bounds
        path, _ = QFileDialog.getSaveFileName(self, 'Separate rendered image',
                    str(capture.source_path.with_name(capture.source_path.stem+'-rendered.png')), 'PNG (*.png)')
        if path:
            try: save_offline_png(capture, Path(path), palette, bounds)
            except (OSError, ValueError) as error: QMessageBox.warning(self, 'PNG refused', str(error))
    def _metadata(self):
        if not self.state: return
        dialog=QDialog(self); dialog.setWindowTitle('Original source metadata'); dialog.resize(700,600)
        box=QVBoxLayout(dialog); text=QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText(metadata_text({'header':self.state['header'],'validation':self.state['report'],'entry': self.state.get('entry_metadata'),
                                         'measurement': None if self.frame is None else self.frame.diagnostic_metadata}))
        box.addWidget(text); dialog.exec()
    def _worker_finished(self):
        if self._closing: self.close()
        else:
            self._in_flight = False
            self.state = None
            self._clear_current('Readings unavailable — worker stopped')
            if self.worker.close_error: self.status.setText('Source cleanup failed: '+self.worker.close_error)
            self._sync_controls()

    def closeEvent(self, event):
        """Keep the Qt owner alive until cooperative worker cleanup, without a GUI wait."""
        self._closing = True
        self.timer.stop()
        self.state = None
        self._clear_current('Reader closing')
        self.image.clear_roi()
        self._sync_controls()
        self.worker.stop()
        if self.worker.isRunning():
            event.ignore()  # finished queues a final Close; no terminate or blocking wait.
        else:
            event.accept()


def main():
    """Separate offline entry point; importing/launching never opens a camera."""
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('path',nargs='?',type=Path); parser.add_argument('--recover',action='store_true')
    args=parser.parse_args(); app=QApplication(sys.argv); window=LmtrWindow(args.path,recover=args.recover); window.show(); sys.exit(app.exec())


if __name__=='__main__': main()
