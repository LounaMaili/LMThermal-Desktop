"""Incremental, cancellable statistics over cached scalars; no playback ownership."""
from dataclasses import replace

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                            QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy)

from lmtr_range_statistics import iter_range_summary, range_extent


class RangePanel(QWidget):
    """One active bounded iterator, replaced by the latest range; no request queue.

    Each Qt timer turn handles at most 256 scalar events. Playback keeps its own
    worker and cursor; selecting or calculating a range never seeks or pauses it.
    Closing/replacing a document drops the iterator and its immutable scalar owner.
    """
    bounds_changed = pyqtSignal(object)
    export_requested = pyqtSignal(object)
    cancel_export = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.comparison = self.result = self.bounds = self.difference = None
        self._signature = self._scan = None
        self._cursor_ns = None
        self._source_idle = self._export_busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self._advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(3)
        caption = QLabel('Arithmetic mean of valid observations · points / ROI means')
        caption.setWordWrap(True)
        layout.addWidget(caption)
        row = QHBoxLayout()
        self.start = QLineEdit(); self.end = QLineEdit()
        for label, edit in (('A (ns)', self.start), ('B (ns)', self.end)):
            edit.setMaxLength(20)
            edit.setMinimumWidth(75)
            edit.setToolTip('Original integer nanoseconds, inclusive bound; press Apply after editing')
            edit.returnPressed.connect(self._apply_fields)
            edit.textEdited.connect(self._fields_edited)
            row.addWidget(QLabel(label)); row.addWidget(edit, 1)
        self.apply = QPushButton('Apply')
        self.apply.clicked.connect(self._apply_fields)
        row.addWidget(self.apply)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.from_cursor = QPushButton('A ← cursor'); self.to_cursor = QPushButton('B ← cursor')
        self.full = QPushButton('Full period')
        self.save = QPushButton('Summary CSV…')
        self.cancel = QPushButton('Cancel')
        for button in (self.from_cursor, self.to_cursor, self.full, self.save, self.cancel):
            row.addWidget(button)
        layout.addLayout(row)
        self.from_cursor.clicked.connect(lambda: self._use_cursor(True))
        self.to_cursor.clicked.connect(lambda: self._use_cursor(False))
        self.full.clicked.connect(self.full_period)
        self.save.clicked.connect(lambda: self.export_requested.emit(self.result) if self.result else None)
        self.cancel.clicked.connect(self._cancel)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(('Selection', 'Min °C', 'Max °C', 'Mean °C', 'Valid', 'Unavail.', 'Missing', 'State'))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setMinimumHeight(72)
        self.table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.note = QLabel('Unavail. = stored unusable observations · Missing = losses (? unknown)')
        self.note.setToolTip('Gap events and their declared missing counts are separate from stored unavailable observations. Segment/control events are excluded from observation counts. Counts are never inferred from time intervals.')
        self.note.setWordWrap(True)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.note)
        self.message = QLabel('Calculate selections first')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.message.setFixedHeight(self.message.fontMetrics().lineSpacing() + 3)
        layout.insertWidget(1, self.message)
        self._sync_controls()

    def _stop_scan(self):
        self.timer.stop()
        if self._scan is not None:
            self._scan.close()
        self._scan = None

    def reset(self):
        self._stop_scan()
        self.comparison = self.result = self.bounds = self.difference = self._signature = None
        self._cursor_ns = None
        self._export_busy = False
        self.start.clear(); self.end.clear(); self.table.setRowCount(0)
        self.message.setText('Calculate selections first')
        self.bounds_changed.emit(None)
        self._sync_controls()

    def set_comparison(self, comparison, difference=None):
        if comparison is None:
            self.reset()
            return
        signature = tuple(id(s) for s in comparison.series), tuple(s.id for s in comparison.selections), difference
        self.comparison, self.difference = comparison, difference
        if signature == self._signature:
            # Names/colors/visibility never change arithmetic or invalidate an
            # in-flight scan. Publish labels from the latest metadata on completion.
            if self.result:
                self.result = replace(self.result, comparison=comparison)
                self._show_result()
            return
        self._signature = signature
        first, last = range_extent(comparison)
        bounds = self.bounds
        self.set_bounds(bounds if bounds and first <= bounds[0] <= bounds[1] <= last else (first, last))

    def set_bounds(self, bounds):
        if self.comparison is None:
            return
        first, last = range_extent(self.comparison)
        a, b = bounds
        if type(a) is not int or type(b) is not int or not first <= a <= b <= last:
            self.message.setText('Invalid range: use integer ns inside the recording, with A ≤ B')
            return
        self.bounds = a, b
        self.start.setText(str(a)); self.end.setText(str(b))
        self.bounds_changed.emit(self.bounds)
        self._stop_scan()
        self.result = None
        self.table.setRowCount(0)
        self.message.setText('Calculating range…')
        self._scan = iter_range_summary(self.comparison, a, b, difference=self.difference)
        self.timer.start()
        self._sync_controls()

    def _advance(self):
        if self._scan is None:
            return
        try:
            done, total = next(self._scan)
            self.message.setText(f'Calculating range… {done:,}/{total:,} events')
        except StopIteration as finished:
            self._stop_scan()
            self.result = replace(finished.value, comparison=self.comparison)
            self._show_result()
        except (ValueError, KeyError, InterruptedError) as error:
            self._stop_scan()
            self.message.setText('Range unavailable: ' + str(error))
        self._sync_controls()

    def _show_result(self):
        summary = self.result
        self.table.setRowCount(len(summary.rows))
        self.table.setMinimumHeight(self.table.horizontalHeader().height() + len(summary.rows) * 22 + 4)
        for index, stats in enumerate(summary.rows):
            item = next((s for s in summary.comparison.selections if s.id == stats.selection_id), None)
            name = f'{item.id} · {item.name}' if item else 'Δ ' + ' − '.join(summary.difference)
            missing = str(stats.known_missing_observations) + (' + ?' if stats.unknown_gap_events else '')
            state = 'Incompatible' if stats.status == 'incompatible_pair' else 'No data' if stats.status == 'no_usable_data' else 'Available'
            if stats.incompatible_observations and state == 'Available':
                state = 'Partial'
            values = (name, *(('—' if v is None else f'{v:.6g}') for v in (stats.minimum_c, stats.maximum_c, stats.mean_c)),
                      str(stats.valid_observations), str(stats.unavailable_observations), missing, state)
            evidence = (f'{stats.gap_events} gap events; {stats.known_missing_observations} declared missing observations; '
                        f'{stats.unknown_gap_events} unknown gap counts; {stats.control_events} control events; '
                        f'{stats.incompatible_observations} incompatible observations. '
                        + (stats.compatibility_reason or stats.compatibility))
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(evidence + (f'\n{item.native.label}' if item else '') +
                                (f'\nExact value: {getattr(stats, ("minimum_c", "maximum_c", "mean_c")[column-1])!r}' if column in (1, 2, 3) else ''))
                if column == 0:
                    # Colored swatches preserve the curve identity while ordinary
                    # palette text remains readable in both light and dark themes.
                    cell.setData(Qt.ItemDataRole.DecorationRole, QColor(item.color if item else '#ffffff'))
                else:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(index, column, cell)
        usable = any(r.valid_observations for r in summary.rows)
        self.message.setText('Inclusive range · all selections, including hidden ones' if usable else
                             'No usable data in this range')
        self.set_cursor(self._cursor_ns)
        self._sync_controls()

    def set_cursor(self, time_ns):
        self._cursor_ns = time_ns
        if self.result:
            # Playback updates only this small label, never the scan or table.
            prefix = self.message.text().split(' · cursor')[0]
            inside = time_ns is not None and self.bounds[0] <= time_ns <= self.bounds[1]
            self.message.setText(prefix + (' · cursor inside' if inside else ' · cursor outside'))
            self.message.setToolTip(self.message.text() + '\nPlayback is unrestricted by the selected range.')
        self._sync_controls()

    def _use_cursor(self, start):
        if self.bounds is None or self._cursor_ns is None:
            return
        a, b = self.bounds
        self.set_bounds((self._cursor_ns, max(self._cursor_ns, b)) if start else (min(a, self._cursor_ns), self._cursor_ns))

    def full_period(self):
        if self.comparison:
            self.set_bounds(range_extent(self.comparison))

    def _fields_edited(self):
        self._stop_scan()
        self.result = None
        self.table.setRowCount(0)
        self.message.setText('Press Apply to calculate the edited bounds')
        self._sync_controls()

    def _apply_fields(self):
        try:
            a, b = int(self.start.text()), int(self.end.text())
        except ValueError:
            self.message.setText('Bounds must be integer nanoseconds')
            return
        self.set_bounds((a, b))

    def _cancel(self):
        if self._export_busy:
            self.cancel_export.emit()
        else:
            self._stop_scan()
            self.message.setText('Range calculation cancelled · Apply or Full period to calculate again')
        self._sync_controls()

    def set_source_state(self, idle, exporting=False):
        self._source_idle, self._export_busy = idle, exporting
        self._sync_controls()

    def _sync_controls(self):
        available = self.comparison is not None
        for widget in (self.start, self.end, self.apply, self.full):
            widget.setEnabled(available and not self._export_busy)
        for widget in (self.from_cursor, self.to_cursor):
            widget.setEnabled(available and self._cursor_ns is not None and not self._export_busy)
        self.save.setEnabled(self.result is not None and self._source_idle and not self._export_busy)
        self.cancel.setEnabled(self._scan is not None or self._export_busy)
