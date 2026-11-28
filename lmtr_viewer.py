#!/usr/bin/env python3
"""Camera-free LMTR timeline viewer; source bytes and measurement coordinates stay intact."""
import argparse
from pathlib import Path
import sys
import threading
import time

from PyQt6.QtCore import QEvent, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QComboBox, QDoubleSpinBox,
    QFileDialog, QCheckBox, QMessageBox, QPlainTextEdit, QDialog, QToolButton,
    QSizePolicy, QSplitter, QScrollArea, QTabWidget, QLineEdit, QTextEdit, QAbstractSpinBox,
    QStyle, QStyleOptionSlider)
from PyQt6.QtCore import Qt, QSignalBlocker

from lmthermal_viewer import ThermalImageWidget, CelsiusLegend
from lmtr_reader import LmtrPlaybackModel, format_timing_evidence
from lmtr_reference import ContractError
from lmtr_temporal import TemporalSelection
from lmtr_comparison import collect_comparison, write_comparison_csv
from lmtr_comparison_panel import ComparisonPanel
from lmtr_range_statistics import write_range_summary_csv
from offline_measurement import Rectangle, save_offline_png
from lmtx_json import dumps as metadata_text
from celsius_palette import PALETTES, CelsiusRange

WARNING = 'Native-equivalent temperatures; absolute physical accuracy not yet independently validated.'
ANALYSIS_ACTIONS = ('series', 'csv', 'summary_csv')
NAVIGATION_ACTIONS = ('seek', 'time', 'tick')
TIMELINE_STEPS = 1_000_000


class IntakeSlider(QSlider):
    """A click or drag targets the timeline directly, independent of event density."""

    def _move_head(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                      QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                      QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        x = round(event.position().x()) - groove.left() - handle.width() // 2
        self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span, option.upsideDown))
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self._move_head(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._move_head(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.isSliderDown() and event.button() == Qt.MouseButton.LeftButton:
            self._move_head(event)
            self.setSliderDown(False)
        else:
            super().mouseReleaseEvent(event)


class LmtrImage(ThermalImageWidget):
    """Saved-document point tool, without changing the shared live-image gesture."""
    point_pinned = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.tool = 'inspect'
        self.pinned = None

    def pin(self, point):
        self.pinned = point
        self.point_pinned.emit(point)
        self.update()

    def mousePressEvent(self, event):
        if self.tool == 'point' and event.button() == Qt.MouseButton.LeftButton:
            self._move(event.position())
            if self.image is not None and self.pointer is not None:
                self.pin(self.pointer)
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image is not None and self.pinned is not None:
            painter = QPainter(self)
            self._marker(painter, self.pinned, QColor('#66f5ef'), 8)


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
        self._analysis_request = None
        self._analysis_cancel = threading.Event()
        self._navigation_request = None
        self._latest_token = 0

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
            self._latest_token = token
            if action in ANALYSIS_ACTIONS:
                self._analysis_cancel.clear()
            self.pending = token, generation, action, value
            self.condition.notify()
            return True

    def discard_tick(self, token, generation):
        """Pause drops an outstanding playback projection without queuing a read."""
        with self.condition:
            if self.stopped.is_set() or generation != self.ready_generation:
                return False
            self._latest_token = token
            if self.pending is not None and self.pending[2] == 'tick':
                self.pending = None
            self.result = None
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
            return (self.stopped.is_set() or generation != self.requested_generation or
                    (self._navigation_request is not None and self._navigation_request != self._latest_token) or
                    (self._analysis_request is not None and self._analysis_cancel.is_set()))

    def cancel_analysis(self, token, generation):
        """Cancel one active or queued operation, never a later scan or another source."""
        with self.condition:
            pending = self.pending
            queued = pending is not None and pending[:2] == (token, generation) and pending[2] in ANALYSIS_ACTIONS
            if generation != self.requested_generation or not (queued or self._analysis_request == (token, generation)):
                return False
            self._analysis_cancel.set()
            return True

    def _analyse(self, model, generation, action, value):
        """Scope cooperative cancellation to analysis, then restore normal playback.

        The reader already unwinds a cancelled scan and restores its descriptor.
        Clear the analysis flag before re-reading the selected playback frame, so
        a user cancellation cannot accidentally close the otherwise valid source.
        """
        try:
            model.pause()
            if model.cancelled():
                raise InterruptedError('Analysis cancelled before processing')
            if action == 'series':
                series = collect_comparison(model, value)
                # Complete the scan and close its cancellation window atomically.
                # An accepted cancellation must prevent publication of the series.
                with self.condition:
                    if model.cancelled():
                        raise InterruptedError('Analysis cancelled')
                    self._analysis_request = None
                return series, None
            model._assert_source_unchanged()
            if action == 'summary_csv':
                count = write_range_summary_csv(value[0], value[1], cancelled=model.cancelled)
                return None, f'Summary CSV saved · {count} rows'
            count = write_comparison_csv(value[0], value[1], difference=value[2], cancelled=model.cancelled)
            return None, f'CSV saved · {count:,} rows'
        except InterruptedError:
            if self.stopped.is_set() or generation != self.requested_generation:
                raise
            return None, 'Analysis cancelled' if action == 'series' else 'CSV cancelled'
        except (ContractError, OSError, ValueError) as error:
            return None, ('Analysis refused: ' if action == 'series' else 'CSV refused: ') + str(error)[:2048]
        finally:
            with self.condition:
                self._analysis_request = None
                self._analysis_cancel.clear()

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
                    navigation = action in NAVIGATION_ACTIONS
                    if navigation:
                        self._navigation_request = token
                    if action in ANALYSIS_ACTIONS:
                        # Cancel must always find the token, even while this
                        # thread is between dequeue and entering _analyse.
                        self._analysis_request = token, generation
                # Do not keep the preceding result/frame alive in loop locals while
                # validating another CHUNK or an entire source file.
                result = frame = state = None
                series = temporal_message = None
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
                    elif action == 'verify':
                        model.index = value
                        model.verify_full()
                    elif action == 'seek': model.seek(value)
                    elif action == 'time': model.seek_time_ns(value)
                    elif action == 'tick':
                        target_ns, displayed_index = value
                        model.project_time_ns(target_ns)
                    elif action in ANALYSIS_ACTIONS:
                        model.index, value = value
                        series, temporal_message = self._analyse(model, generation, action, value)
                        value = None  # The viewer owns persistent scalar results.
                    if model.cancelled():
                        raise InterruptedError('LMTR load cancelled')
                    # The GUI supplies the index it already displays. There is no
                    # need to rebuild or repaint that same observation on a tick.
                    if action == 'tick' and model.index == displayed_index:
                        result = token, None, None, None
                    else:
                        frame = model.frame()
                        state = {'index': model.index, 'count': model.entry_count,
                             'duration_s': model.duration_s, 'entry': model.entry,
                             'first_ns': model.first_time_ns, 'last_ns': model.last_time_ns,
                             'playing': model.playing, 'report': model.report,
                             'header': model.header, 'cache_bytes': model.cache_bytes,
                             'entry_metadata': model.original_entry(model.index) if model.entry_count else None,
                             'timing': model.timing_evidence(model.index) if model.entry_count else None,
                             'source_path': model.path, 'source_generation': generation,
                             'temporal_series': series, 'temporal_message': temporal_message}
                        result = token, frame, state, None
                except Exception as error:
                    with self.condition:
                        if navigation and token != self._latest_token and generation == self.requested_generation and not self.stopped.is_set():
                            self._navigation_request = None
                            continue
                        if self._analysis_request == (token, generation):
                            self._analysis_request = None
                            self._analysis_cancel.clear()
                        # A failing navigation may be superseded until publication.
                        # Keep its source open so that the newer position can run.
                    if not navigation:
                        self._close(model)
                        model = None
                    result = token, None, None, (type(error).__name__ + ': ' + str(error))[:8192]
                with self.condition:
                    self._navigation_request = None
                    if self.stopped.is_set(): break
                    if generation != self.requested_generation or token != self._latest_token: continue
                    self.ready_generation = generation if result[3] is None else None
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
        self._pending_action = None
        self._generation = 0
        self._space_key = None
        self._closing = False
        self._playing = False
        self._anchor_ns = self._anchor_clock = 0
        self._rate = 1.0
        self._skipped = 0
        self._seek_index = self._seek_ns = None
        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        layout.setSpacing(3)
        row = QHBoxLayout(); layout.addLayout(row)
        self.open_button = QPushButton('Open .lmtr'); row.addWidget(self.open_button)
        self.recover = QCheckBox('Recover incomplete file'); self.recover.setChecked(recover); row.addWidget(self.recover)
        self.recover.setToolTip('Explicit verified-prefix recovery only. Unknown tail stays unknown; the source is never repaired.')
        self.save_button = QPushButton('Save PNG…'); row.addWidget(self.save_button)
        self.details_button = QToolButton(); self.details_button.setText('Diagnostics')
        self.details_button.setCheckable(True); row.addWidget(self.details_button)
        self.details_button.setToolTip('Original metadata and explicit full-file integrity verification')
        self.temporal_button = QToolButton(); self.temporal_button.setText('Temperature over time')
        self.temporal_button.setCheckable(True); row.addWidget(self.temporal_button)
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
        views = QSplitter(Qt.Orientation.Vertical)
        inspection = QSplitter(Qt.Orientation.Horizontal)
        inspection.setChildrenCollapsible(False)
        views.addWidget(inspection)
        views.setChildrenCollapsible(False)
        layout.addWidget(views, 1)
        image_panel = QWidget()
        images = QHBoxLayout(image_panel); images.setContentsMargins(0, 0, 0, 0)
        inspection.addWidget(image_panel)
        self.image = LmtrImage(); images.addWidget(self.image, 1)
        # Native matrices keep their dimensions; only the offline viewport may
        # shrink so the image and temporal axes fit simultaneously on a laptop.
        self.image.setMinimumSize(260, 100)
        self.legend = CelsiusLegend(); images.addWidget(self.legend)
        self.temporal_panel = self._build_temporal_panel()
        # Four editable selections must not force a taller window than a laptop
        # screen. The chart/legend remain reachable in the bounded scroll viewport.
        self.temporal_scroll = QScrollArea()
        self.temporal_scroll.setWidgetResizable(True)
        self.temporal_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.temporal_scroll.setMinimumWidth(360)
        self.temporal_scroll.setWidget(self.temporal_panel)
        self.analysis_tabs = QTabWidget()
        self.analysis_tabs.setMinimumWidth(360)
        self.analysis_tabs.addTab(self.temporal_scroll, 'Selections')
        self.statistics_scroll = QScrollArea()
        self.statistics_scroll.setWidgetResizable(True)
        self.statistics_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.statistics_scroll.setWidget(self.temporal_panel.range_panel)
        self.analysis_tabs.addTab(self.statistics_scroll, 'Range statistics')
        inspection.addWidget(self.analysis_tabs)
        self.chart.range_changed.connect(lambda unused: self.analysis_tabs.setCurrentIndex(1))
        self.temporal_panel.range_mode.toggled.connect(lambda active: self.analysis_tabs.setCurrentIndex(1) if active else None)
        inspection.setStretchFactor(0, 1); inspection.setStretchFactor(1, 1)
        views.addWidget(self.temporal_panel.plot_panel)
        views.setStretchFactor(0, 1); views.setStretchFactor(1, 1)
        self.temporal_panel.plot_panel.hide()
        self.analysis_tabs.hide()
        self.temporal_button.toggled.connect(self.analysis_tabs.setVisible)
        self.temporal_button.toggled.connect(self.temporal_panel.plot_panel.setVisible)
        self.temporal_button.toggled.connect(lambda visible: views.setSizes([300, 220]) if visible else None)
        self.time_label = QLabel('No recording timeline')
        self.time_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.time_label)
        self.slider = IntakeSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, TIMELINE_STEPS)
        self.slider.setToolTip('Timeline · click or drag to pause and seek. Left/Right step one event; Space plays or pauses.')
        layout.addWidget(self.slider)
        controls = QHBoxLayout(); layout.addLayout(controls)
        self.previous = QPushButton('Previous'); self.play = QPushButton('Play'); self.next = QPushButton('Next')
        for b in (self.previous, self.play, self.next): controls.addWidget(b)
        self.speed = QComboBox()
        for rate in (.5,1,2,4): self.speed.addItem(str(rate)+'×', rate)
        self.speed.setCurrentIndex(1); controls.addWidget(self.speed)
        self.seconds = QDoubleSpinBox(); self.seconds.setDecimals(9); self.seconds.setSuffix(' s'); controls.addWidget(self.seconds)
        self.seek_button = QPushButton('Seek time'); controls.addWidget(self.seek_button)
        self.seek_button.setToolTip('Pause and select the first stored event at or after the entered elapsed time.')
        controls = QHBoxLayout(); layout.addLayout(controls)
        self.palette = QComboBox(); self.palette.addItems(PALETTES); self.palette.setCurrentText('Inferno'); controls.addWidget(self.palette)
        self.auto = QCheckBox('Auto range'); self.auto.setChecked(True); controls.addWidget(self.auto)
        self.auto.setToolTip('Display-only percentile bounds; uncheck to edit the locked Celsius bounds')
        self.minimum = QDoubleSpinBox(); self.maximum = QDoubleSpinBox()
        for spin, value in ((self.minimum,20),(self.maximum,40)):
            spin.setRange(-1e30,1e30); spin.setValue(value); spin.setSuffix(' °C'); controls.addWidget(spin)
        self.clear = QPushButton('Clear ROI'); controls.addWidget(self.clear)
        controls.addWidget(QLabel('Image tool'))
        self.image_tool = QComboBox()
        for text, tool in (('Inspect', 'inspect'), ('Point', 'point'), ('ROI', 'roi')):
            self.image_tool.addItem(text, tool)
        controls.addWidget(self.image_tool)
        self.clear_point = QPushButton('Clear point'); controls.addWidget(self.clear_point)
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
        self.timer = QTimer(self); self.timer.setInterval(16); self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.open_button.clicked.connect(self._choose)
        self.slider.sliderPressed.connect(self._pause_playback)
        self.slider.valueChanged.connect(self._seek_slider)
        self.slider.sliderReleased.connect(self._update_playhead)
        self.previous.clicked.connect(lambda:self._request('step', -1)); self.next.clicked.connect(lambda:self._request('step', 1))
        self.play.clicked.connect(self._toggle_playback)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self.seek_button.clicked.connect(self._seek_typed)
        self.seconds.lineEdit().returnPressed.connect(self._seek_typed)
        self.palette.currentTextChanged.connect(self._presentation); self.auto.toggled.connect(self._presentation)
        self.minimum.valueChanged.connect(self._presentation); self.maximum.valueChanged.connect(self._presentation)
        self.image.hovered.connect(self._point); self.image.roi_changed.connect(self._roi)
        self.image.point_pinned.connect(self._point_changed)
        self.image_tool.currentIndexChanged.connect(self._set_image_tool)
        self.clear_point.clicked.connect(lambda: self.image.pin(None))
        self.clear.clicked.connect(self.image.clear_roi); self.save_button.clicked.connect(self._save)
        self.metadata_button.clicked.connect(self._metadata)
        self.verify_button.clicked.connect(lambda:self._request('verify'))
        self.save_button.setEnabled(False); self.metadata_button.setEnabled(False)
        app = QApplication.instance()
        app.installEventFilter(self)
        self._set_image_tool()
        self._sync_controls()
        self._presentation()
        if path: self.open_path(path)
    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self,'Open read-only LMTR', '', 'LMTR recording (*.lmtr)')
        if path: self.open_path(Path(path))
    def _sync_controls(self):
        """Only exclusive work blocks navigation; a pending frame is ordinary playback."""
        ready = self.state is not None and not self._closing
        exclusive = self._pending_action in (*ANALYSIS_ACTIONS, 'verify', 'open')
        navigation = ready and not exclusive and bool(self.state['count'])
        self.slider.setEnabled(navigation and self.state['last_ns'] > self.state['first_ns'])
        for control in (self.previous, self.next, self.play, self.speed, self.seek_button, self.seconds):
            control.setEnabled(navigation)
        self.play.setToolTip('Space · Play or pause on the displayed event.' if not exclusive else
                             'Finish or cancel the current file operation to navigate.')
        self.verify_button.setEnabled(ready and not exclusive)
        self.metadata_button.setEnabled(ready and self._pending_action not in ('seek', 'time'))
        self.open_button.setEnabled(not self._closing and not self.worker.stopped.is_set())
        self.save_button.setEnabled(ready and self.frame is not None and self.image.image is not None)
        self.image_tool.setEnabled(ready)
        self.clear.setEnabled(ready and self.image.roi is not None)
        self.clear_point.setEnabled(ready and self.image.pinned is not None)
        self._sync_temporal_controls()

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.KeyPress and self.isVisible()
                and isinstance(watched, QWidget) and watched.window() is self.window()
                and not isinstance(watched, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox))
                and event.modifiers() == Qt.KeyboardModifier.NoModifier and self.play.isEnabled()):
            if event.key() == Qt.Key.Key_Space:
                if not event.isAutoRepeat():
                    self._toggle_playback()
                return True
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                self._request('step', -1 if event.key() == Qt.Key.Key_Left else 1)
                return True
        return super().eventFilter(watched, event)

    def _shown_ns(self):
        timing = self.state['timing']
        return int(timing['timeline_ns']) if timing else self.state['first_ns']

    def _head_ns(self):
        if self._playing:
            elapsed = max(0, time.monotonic_ns() - self._anchor_clock)
            return min(self.state['last_ns'], self._anchor_ns + int(elapsed * self._rate))
        return self._seek_ns if self._seek_ns is not None else self._shown_ns()

    @staticmethod
    def _time_text(ns):
        milliseconds = ns // 1_000_000
        minutes, rest = divmod(milliseconds, 60_000)
        seconds, fraction = divmod(rest, 1000)
        return f'{minutes:02}:{seconds:02}.{fraction:03}'

    def _update_playhead(self):
        if self.state is None:
            self.time_label.setText('No recording timeline')
            return
        first, last = self.state['first_ns'], self.state['last_ns']
        head = self._head_ns()
        mode = ('Playback' if self._playing else 'Seeking' if self._pending_action in ('seek', 'time')
                else 'Paused')
        if mode == 'Seeking' and self._seek_index is not None:
            mode = f'Seeking event {self._seek_index + 1}'
        text = f'{mode}  {self._time_text(head - first)} / {self._time_text(last - first)}'
        if self._playing:
            text += f'   ·   Image {self._time_text(self._shown_ns() - first)}'
        text += f"   ·   Display skipped {self._skipped} events   ·   Recorded gaps {self.state['report']['summary']['gap_events']}"
        if self.time_label.text() != text:
            self.time_label.setText(text)
        if not self.slider.isSliderDown():
            with QSignalBlocker(self.slider):
                self.slider.setValue((head - first) * TIMELINE_STEPS // max(1, last - first))

    def _pause_playback(self, *unused):
        """Freeze the visible event immediately, even while a projection is decoding."""
        self._playing = False
        self.timer.stop()
        self.play.setText('Play')
        if self.state is not None:
            self.state['playing'] = False
        if self._pending_action == 'tick' and self.worker.discard_tick(self.token + 1, self._generation):
            self.token += 1
            self._in_flight = False
            self._pending_action = None
        self._update_playhead()

    def _toggle_playback(self):
        if not self.play.isEnabled() or self.state is None:
            return
        if self._playing:
            self._pause_playback()
            return
        self._playing = self.state['playing'] = True
        self._anchor_clock = time.monotonic_ns()
        self._anchor_ns = (self.state['first_ns'] if self.state['index'] == self.state['count'] - 1
                           else self._head_for_start())
        self.play.setText('Pause')
        self.timer.start()
        self._update_playhead()

    def _head_for_start(self):
        """A pending time seek is a valid play origin; index seeks resolve on arrival."""
        return self._seek_ns if self._seek_ns is not None else self._shown_ns()

    def _speed_changed(self):
        if self._playing:
            self._anchor_ns = self._head_ns()
            self._anchor_clock = time.monotonic_ns()
        self._rate = self.speed.currentData()

    def _seek_slider(self, position):
        if self.state is None:
            return
        first, last = self.state['first_ns'], self.state['last_ns']
        self._request('time', first + ((last - first) * position + TIMELINE_STEPS // 2) // TIMELINE_STEPS)

    def _seek_typed(self):
        if self.state is not None:
            self._request('time', self.state['first_ns'] + round(self.seconds.value() * 1e9))

    def open_path(self, path):
        if self._closing: return False
        self._pause_playback()
        self.state = None
        self._seek_ns = self._seek_index = None
        self._skipped = 0
        self._space_key = None
        self._generation += 1
        self._reset_temporal()
        self.image.clear_roi()
        self.image.pin(None)
        self.image.pointer = None
        self.image._mouse_widget = None
        return self._request('open', (Path(path), self.recover.isChecked()))

    def _clear_current(self, message='Readings unavailable — loading'):
        self.frame = None
        self.temporal_panel.set_cursor(None, None)
        self.temporal_reading.setText('')
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
        if action == 'step':
            index = self._seek_index if self._seek_index is not None else self.state['index']
            value = max(0, min(self.state['count'] - 1, index + value))
            action = 'seek'
        manual = action in ('seek', 'time')
        if self._in_flight and action != 'open' and self._pending_action not in NAVIGATION_ACTIONS:
            return False
        if self._in_flight and action == 'tick':
            return False
        if manual or action in (*ANALYSIS_ACTIONS, 'verify'):
            self._pause_playback()
        if action in ANALYSIS_ACTIONS:
            value = self.state['index'], value
        if action == 'verify':
            value = self.state['index']
        token = self.token + 1
        if not self.worker.request(token, action, value, generation=self._generation): return False
        self.token = token
        if clear and (self.frame is not None or action == 'open' or not self._in_flight):
            self.image.empty_text = 'Loading selected event…'
            self._clear_current()
        self._in_flight = True
        self._pending_action = action
        self._seek_index = value if action == 'seek' else None
        self._seek_ns = value if action == 'time' else None
        if action == 'open': self.validation_status.setText('Opening — full-file integrity not yet checked')
        if action != 'tick':
            self._update_playhead()
            self._sync_controls()
        return True

    def _tick(self):
        # A slow selected read must not be endlessly superseded by timer requests.
        # User scrubs still replace the pending slot; no catch-up frame queue exists.
        if self._playing and self.state is not None:
            self._update_playhead()
            if not self._in_flight:
                self._request('tick', (self._head_ns(), self.state['index']), clear=False)
    def _receive(self):
        result = self.worker.take()
        if self._closing or result is None or result[0] != self.token: return
        self._in_flight = False
        completed_action = self._pending_action
        self._pending_action = None
        _, frame, state, error = result
        if error:
            self._pause_playback()
            self.state = None
            self._seek_index = self._seek_ns = None
            self._reset_temporal()
            self._clear_current('Readings unavailable')
            self.status.setText('Read failed: '+error)
            self.validation_status.setText('No current verified source')
            self._update_playhead()
            self._sync_controls()
            return
        if state is None:  # The playback head is still on the displayed event.
            return
        if completed_action == 'tick' and self.state is not None:
            self._skipped += max(0, state['index'] - self.state['index'] - 1)
        self._seek_index = self._seek_ns = None
        self.frame = frame
        self.state = state
        state['playing'] = self._playing
        if self._playing and completed_action in ('seek', 'time'):
            self._anchor_ns = self._shown_ns()
            self._anchor_clock = time.monotonic_ns()
        series = state.pop('temporal_series')
        message = state.pop('temporal_message')
        if series is not None:
            self.temporal_panel.set_result(series)
        if message is not None:
            self.temporal_message.setText(message)
            self.temporal_message.setToolTip(message)
            if completed_action == 'summary_csv':
                self.temporal_panel.range_panel.message.setText(message)
        elif series is not None:
            self.temporal_message.clear()
        self._update_temporal_cursor()
        entry = state['entry']; report = state['report']
        self.seconds.setRange(0,state['duration_s'])
        if not self._playing and not self.seconds.lineEdit().hasFocus() and entry is not None:
            self.seconds.setValue(entry.elapsed_s)
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
                self.image.pin(None)
                self.image.pointer = None
                self.image._mouse_widget = None
            self._space_key = key
        self.image.set_observation(self.frame)
        self.legend.set_presentation(self.image.effective_bounds, self.image.palette)
        self.image.setEnabled(self.image.image is not None)
        if self._playing and state['index'] == state['count'] - 1:
            self._pause_playback()
        self._update_playhead()
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
    def _set_image_tool(self):
        self.image.tool = self.image_tool.currentData()
        self.image._roi_anchor = None
        hints = {
            'inspect': 'Move over the current image for an instant reading. Drag for a quick ROI.',
            'point': 'Click a native pixel to pin a point across compatible images.',
            'roi': 'Drag a rectangle for instant min, max and mean on the current image.',
        }
        hint = hints[self.image.tool]
        self.image_tool.setToolTip(hint)
        self.image.setToolTip(hint)

    def _point_changed(self, point):
        self.clear_point.setEnabled(self.state is not None and point is not None)
        self._point(self.image.pointer)
        self._sync_temporal_controls()

    def _point(self, position):
        pinned = self.image.pinned
        position = pinned if pinned is not None else position
        value = self.frame.point(*position) if self.frame and position else None
        kind = 'Pinned point' if pinned is not None else 'Native'
        self.point.setText('Point unavailable' if value is None else f'{kind} {position}: {value:.8g} °C; original raw '+str(None if self.frame.raw14 is None else int(self.frame.raw14[position[1],position[0]])))
    def _roi(self, roi):
        self.clear.setEnabled(self.state is not None and roi is not None)
        self._sync_temporal_controls()
        if roi is None: self.roi_label.setText('Inspection ROI: none'); return
        result = self.frame.roi_statistics(Rectangle(roi.x1,roi.y1,roi.x2,roi.y2)) if self.frame else None
        self.roi_label.setText('ROI unavailable' if result is None or not result.valid_pixel_count else
            f'Native ROI [{roi.x1},{roi.x2}) × [{roi.y1},{roi.y2}): min {result.min_c:.8g}, max {result.max_c:.8g}, mean {result.mean_c:.8g} °C · {result.valid_pixel_count}/{result.pixel_count} valid pixels')

    def _build_temporal_panel(self):
        panel = ComparisonPanel()
        self.chart = panel.chart
        self.plot_point, self.plot_roi = panel.plot_point, panel.plot_roi
        self.save_csv = panel.save_csv
        self.temporal_summary, self.temporal_reading, self.temporal_message = panel.summary, panel.reading, panel.message
        self.chart.seek_event.connect(lambda index: self._request('seek', index))
        panel.add_point.connect(lambda: self._plot('point'))
        panel.add_roi.connect(lambda: self._plot('roi'))
        panel.calculate.connect(self._calculate_temporal)
        panel.export.connect(self._export_temporal)
        panel.cancel.connect(self._cancel_temporal)
        panel.range_panel.export_requested.connect(self._export_range_summary)
        panel.range_panel.cancel_export.connect(self._cancel_temporal)
        return panel

    def _sync_temporal_controls(self):
        ready = self.state is not None and not self._closing
        exclusive = self._pending_action in (*ANALYSIS_ACTIONS, 'verify', 'open')
        roi = self.image.roi
        usable = ready and not exclusive and self.frame is not None and self.image.isEnabled()
        has_point = self.image.pinned is not None or (roi is not None and roi.pixel_count == 1)
        self.temporal_panel.set_ready(ready and not exclusive, usable and roi is not None, usable and has_point)
        self.temporal_panel.set_busy(self._in_flight and self._pending_action in ANALYSIS_ACTIONS)
        self.temporal_panel.range_panel.set_source_state(ready and not exclusive, self._in_flight and self._pending_action == 'summary_csv')

    def _reset_temporal(self):
        self.temporal_panel.reset()
        self.analysis_tabs.setCurrentIndex(0)

    def _plot(self, kind):
        if self.frame is None:
            return
        roi = self.image.roi
        if kind == 'point' and self.image.pinned is not None:
            x, y = self.image.pinned
            region = Rectangle(x, y, x + 1, y + 1)
        elif roi is not None and (kind == 'roi' or roi.pixel_count == 1):
            region = Rectangle(roi.x1, roi.y1, roi.x2, roi.y2)
        else:
            return
        selected = TemporalSelection.from_frame(self.frame, kind, region)
        self.temporal_panel.add_selection(selected)

    def _calculate_temporal(self, selections):
        if self._request('series', selections, clear=False):
            self.temporal_message.setText('Calculating… · playback paused')

    def _cancel_temporal(self):
        """Cancel only this active analysis/export; retain the open playback owner."""
        if self.worker.cancel_analysis(self.token, self._generation):
            self.temporal_message.setText('Cancelling…')
            self.temporal_panel.cancel_button.setEnabled(False)

    def _update_temporal_cursor(self):
        timing = self.state['timing']
        self.temporal_panel.set_cursor(int(timing['timeline_ns']) if timing else None,
                                       self.state['index'] if timing else None)

    def _export_temporal(self):
        comparison, generation = self.temporal_panel.comparison, self._generation
        if comparison is None or self.state is None:
            return
        pair = self.temporal_panel.difference_pair()
        source = self.state['source_path']
        path, _ = QFileDialog.getSaveFileName(self, 'Export temporal comparison',
                                             str(source.with_name(source.stem + '-comparison.csv')), 'CSV (*.csv)')
        if path and generation == self._generation and self.temporal_panel.comparison is comparison:
            if self._request('csv', (comparison, Path(path), pair), clear=False):
                self.temporal_message.setText('Writing CSV…')
                return

    def _export_range_summary(self, summary):
        """Freeze bounds, labels and results before the destination's nested event loop."""
        if summary is None or self.state is None:
            return
        generation = self._generation
        source = self.state['source_path']
        path, _ = QFileDialog.getSaveFileName(self, 'Export range summary',
                    str(source.with_name(source.stem + '-range-summary.csv')), 'CSV (*.csv)')
        panel = self.temporal_panel.range_panel
        if path and generation == self._generation and panel.result is summary:
            if self._request('summary_csv', (summary, Path(path)), clear=False):
                self.temporal_message.setText('Writing summary CSV…')
                panel.message.setText('Writing summary CSV…')
                return

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
            self._pause_playback()
            self.state = None
            self._reset_temporal()
            self._clear_current('Readings unavailable — worker stopped')
            if self.worker.close_error: self.status.setText('Source cleanup failed: '+self.worker.close_error)
            self._update_playhead()
            self._sync_controls()

    def closeEvent(self, event):
        """Keep the Qt owner alive until cooperative worker cleanup, without a GUI wait."""
        self._closing = True
        self._pause_playback()
        self.state = None
        self._reset_temporal()
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
