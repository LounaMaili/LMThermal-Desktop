#!/usr/bin/env python3
"""Camera-free LMTR timeline viewer; source bytes and measurement coordinates stay intact."""
import argparse
from pathlib import Path
import sys
import threading

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QComboBox, QDoubleSpinBox,
    QFileDialog, QCheckBox, QMessageBox, QPlainTextEdit, QDialog)
from PyQt6.QtCore import Qt, QSignalBlocker

from lmthermal_viewer import ThermalImageWidget, CelsiusLegend
from lmtr_reader import LmtrPlaybackModel
from offline_measurement import Rectangle, save_offline_png
from lmtx_json import dumps as metadata_text
from celsius_palette import PALETTES, CelsiusRange

WARNING = 'Native-equivalent temperatures; absolute physical accuracy not yet independently validated.'


class LmtrWorker(QThread):
    """One coalesced request/result; every file/index/decode operation stays off the UI.

    Stop cancels between reference-reader records. One synchronous hash/decompression
    has no certified cancellation latency. The worker owns and closes its descriptor.
    """
    available = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.condition = threading.Condition()
        self.pending = self.result = None
        self.signalled = False
        self.stopped = threading.Event()
    def request(self, token, action, value=None):
        with self.condition:
            self.pending = token, action, value
            self.condition.notify()
    def take(self):
        with self.condition:
            value, self.result = self.result, None
            self.signalled = False
            return value
    def stop(self):
        self.stopped.set()
        with self.condition: self.condition.notify()
    def run(self):
        model = None
        try:
            while not self.stopped.is_set():
                with self.condition:
                    while self.pending is None and not self.stopped.is_set(): self.condition.wait()
                    if self.stopped.is_set(): break
                    token, action, value = self.pending
                    self.pending = None
                try:
                    if action == 'open':
                        if model: model.close(); model = None
                        model = LmtrPlaybackModel(value[0], recover=value[1], cancelled=self.stopped.is_set)
                    elif model is None: raise ValueError('Open a recording first')
                    elif action == 'verify': model.verify_full()
                    elif action == 'seek': model.seek(value)
                    elif action == 'step': model.step(value)
                    elif action == 'time': model.seek_time_ns(value)
                    elif action == 'toggle': model.pause() if model.playing else model.play()
                    elif action == 'tick': model.advance()
                    elif action == 'speed': model.set_speed(value)
                    frame = model.frame()
                    state = {'index':model.index, 'count':model.entry_count, 'duration_s':model.duration_s,
                             'entry':model.entry, 'playing':model.playing, 'report':model.report,
                             'header':model.reader.head, 'cache_bytes':model.cache_bytes}
                    result = token, frame, state, None
                except Exception as error:
                    if model: model.pause()
                    result = token, None, None, str(error)
                with self.condition:
                    if self.stopped.is_set(): break
                    if self.pending is not None: continue  # Obsolete work cannot publish.
                    self.result = result
                    emit = not self.signalled
                    self.signalled = True
                if emit: self.available.emit()
        finally:
            if model: model.close()


class LmtrWindow(QMainWindow):
    """Diagnostic offline UI built from existing native-grid image/ROI/palette components."""
    def __init__(self, path=None, *, recover=False):
        super().__init__()
        self.setWindowTitle('LMThermal — LMTR offline recording')
        self.resize(1050, 760)
        self.frame = self.state = None
        self.token = 0
        self._in_flight = False
        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        row = QHBoxLayout(); layout.addLayout(row)
        self.open_button = QPushButton('Open .lmtr'); row.addWidget(self.open_button)
        self.recover = QCheckBox('Explicit verified-prefix recovery'); self.recover.setChecked(recover); row.addWidget(self.recover)
        self.metadata_button = QPushButton('Original metadata'); row.addWidget(self.metadata_button)
        self.verify_button = QPushButton('Verify entire file / prefix'); row.addWidget(self.verify_button)
        self.save_button = QPushButton('Save separate PNG'); row.addWidget(self.save_button)
        self.status = QLabel('Offline — no camera'); self.status.setWordWrap(True); layout.addWidget(self.status)
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
        self.palette = QComboBox(); self.palette.addItems(PALETTES); self.palette.setCurrentText('Inferno'); controls.addWidget(self.palette)
        self.auto = QCheckBox('Auto'); self.auto.setChecked(True); controls.addWidget(self.auto)
        self.minimum = QDoubleSpinBox(); self.maximum = QDoubleSpinBox()
        for spin, value in ((self.minimum,20),(self.maximum,40)):
            spin.setRange(-1e30,1e30); spin.setValue(value); spin.setSuffix(' °C'); controls.addWidget(spin)
        self.clear = QPushButton('Clear ROI'); controls.addWidget(self.clear)
        self.readings = QLabel('Readings unavailable'); layout.addWidget(self.readings)
        self.point = QLabel('Point unavailable'); layout.addWidget(self.point)
        self.roi_label = QLabel('Inspection ROI: none'); layout.addWidget(self.roi_label)
        warning = QLabel(WARNING); warning.setWordWrap(True); layout.addWidget(warning)
        self.worker = LmtrWorker(self); self.worker.available.connect(self._receive); self.worker.start()
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
        if path: self.open_path(path)
    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self,'Open read-only LMTR', '', 'LMTR recording (*.lmtr)')
        if path: self.open_path(Path(path))
    def open_path(self, path):
        self.timer.stop(); self.state = None; self.image.clear_roi()
        self._request('open', (Path(path), self.recover.isChecked()))
    def _request(self, action, value=None, *, clear=True):
        self.token += 1
        if clear:
            self.frame = None; self.image.set_observation(None); self.legend.set_presentation(None,self.palette.currentText())
            self.readings.setText('Readings unavailable — loading'); self.point.setText('Point unavailable'); self.roi_label.setText('ROI unavailable — loading')
            self.save_button.setEnabled(False)
        self._in_flight = True
        self.worker.request(self.token,action,value)
    def _tick(self):
        # A slow selected read must not be endlessly superseded by timer requests.
        # User scrubs still replace the pending slot; no catch-up frame queue exists.
        if not self._in_flight: self._request('tick', clear=False)
    def _receive(self):
        result = self.worker.take()
        if result is None or result[0] != self.token: return
        self._in_flight = False
        _, self.frame, state, error = result
        if error:
            self.timer.stop(); self.state = None; self.image.set_observation(None)
            self.legend.set_presentation(None,self.palette.currentText())
            self.readings.setText('Readings unavailable'); self.point.setText('Point unavailable'); self.roi_label.setText('ROI unavailable')
            self.save_button.setEnabled(False); self.status.setText('Read failed: '+error); return
        self.state = state
        entry = state['entry']; report = state['report']
        block = QSignalBlocker(self.slider); self.slider.setRange(0,max(0,state['count']-1)); self.slider.setValue(state['index']); del block
        self.seconds.setRange(0,state['duration_s'])
        completeness = 'RECOVERED PREFIX — unknown tail' if report['status']=='recovered' else 'END '+str(report['declared_end_status'])
        self.status.setText(f"{completeness} | {report['integrity_scope']} (file); selected CHUNK verified | event {state['index']+1 if entry else 0}/{state['count']} | "+(f'{entry.elapsed_s:.6f} s | {entry.state}: {entry.reason}' if entry else 'empty recording'))
        self.image.empty_text = entry.reason if entry else 'Empty recording'
        # Inspection geometry is native to one stream geometry, never scaled into
        # another camera/module after a segment replacement.
        if self.frame and self.image.roi:
            r = self.image.roi
            if not (0 <= r.x1 < r.x2 <= self.frame.geometry.width and 0 <= r.y1 < r.y2 <= self.frame.geometry.height):
                self.image.clear_roi()
        self.image.set_observation(self.frame)
        self.play.setText('Pause' if state['playing'] else 'Play')
        if state['playing']: self.timer.start()
        else: self.timer.stop()
        self.metadata_button.setEnabled(True)
        self._presentation()
        self.save_button.setEnabled(self.image.image is not None)
        if self.frame and self.frame.has_readings:
            self.readings.setText(f'Native grid {self.frame.geometry.width}×{self.frame.geometry.height} | min {self.frame.low_c:.6g} at {self.frame.low_xy} | max {self.frame.high_c:.6g} at {self.frame.high_xy}')
        else: self.readings.setText('Readings unavailable — no valid stored temperature plane')
        self._roi(self.image.roi); self._point(self.image.pointer)
    def _presentation(self, *unused):
        if self.maximum.value() <= self.minimum.value(): return
        self.image.set_presentation(self.palette.currentText(),self.auto.isChecked(),self.minimum.value(),self.maximum.value())
        self.legend.set_presentation(self.image.effective_bounds,self.palette.currentText())
    def _point(self, position):
        value = self.frame.point(*position) if self.frame and position else None
        self.point.setText('Point unavailable' if value is None else f'Native {position}: {value:.8g} °C; original raw '+str(None if self.frame.raw14 is None else int(self.frame.raw14[position[1],position[0]])))
    def _roi(self, roi):
        if roi is None: self.roi_label.setText('Inspection ROI: none'); return
        result = self.frame.roi_statistics(Rectangle(roi.x1,roi.y1,roi.x2,roi.y2)) if self.frame else None
        self.roi_label.setText('ROI unavailable' if result is None or not result.valid_pixel_count else f'Native ROI {roi}: min {result.min_c:.8g}, max {result.max_c:.8g}, mean {result.mean_c:.8g} °C ({result.valid_pixel_count}/{result.pixel_count})')
    def _save(self):
        if not self.frame: return
        path, _ = QFileDialog.getSaveFileName(self,'Separate rendered image',str(self.frame.source_path.with_name(self.frame.source_path.stem+'-rendered.png')),'PNG (*.png)')
        if path:
            try: save_offline_png(self.frame,Path(path),self.palette.currentText(),self.image.effective_bounds)
            except (OSError,ValueError) as error: QMessageBox.warning(self,'PNG refused',str(error))
    def _metadata(self):
        if not self.state: return
        dialog=QDialog(self); dialog.setWindowTitle('Original source metadata'); dialog.resize(700,600)
        box=QVBoxLayout(dialog); text=QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText(metadata_text({'header':self.state['header'],'validation':self.state['report'],'entry':None if self.frame is None else self.frame.diagnostic_metadata}))
        box.addWidget(text); dialog.exec()
    def closeEvent(self,event):
        self.timer.stop(); self.worker.stop()
        if not self.worker.wait(10000): event.ignore(); return
        event.accept()


def main():
    """Separate offline entry point; importing/launching never opens a camera."""
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('path',nargs='?',type=Path); parser.add_argument('--recover',action='store_true')
    args=parser.parse_args(); app=QApplication(sys.argv); window=LmtrWindow(args.path,recover=args.recover); window.show(); sys.exit(app.exec())


if __name__=='__main__': main()
