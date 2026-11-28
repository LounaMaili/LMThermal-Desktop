"""Selection editing and legend for the read-only LMTR comparison workspace."""
from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal, QSignalBlocker
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QCheckBox, QComboBox, QLineEdit, QColorDialog, QSizePolicy, QProgressBar)

from lmtr_comparison import Comparison, NamedSelection, MAX_SELECTIONS, CURVE_COLORS, difference_value
from lmtr_temporal_chart import TemporalChart
from lmtr_range_panel import RangePanel


class ComparisonPanel(QWidget):
    """Own display edits on the GUI thread; request scalar collection on the file worker."""
    calculate = pyqtSignal(object)
    export = pyqtSignal()
    add_point = pyqtSignal()
    add_roi = pyqtSignal()
    cancel = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selections = ()
        self.comparison = None
        self._next_id = 1
        self._idle = self._usable = self._single_pixel = False
        self._cursor = (None, None)
        self.item_widgets = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(4)
        instruction = QLabel('Select a pixel or rectangle, add up to four, then Calculate.')
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        row = QHBoxLayout()
        self.plot_point = QPushButton('Add point')
        self.plot_roi = QPushButton('Add ROI')
        self.compute = QPushButton('Calculate')
        self.save_csv = QPushButton('CSV…')
        self.save_csv.setToolTip('Export comparison CSV, including hidden selections and unavailable events')
        for button in (self.plot_point, self.plot_roi, self.compute, self.save_csv):
            row.addWidget(button)
        layout.addLayout(row)
        self.editor = QWidget()
        self.items = QVBoxLayout(self.editor)
        self.items.setContentsMargins(0, 0, 0, 0)
        self.items.setSpacing(2)
        layout.addWidget(self.editor)
        self.difference_controls = QWidget()
        row = QHBoxLayout(self.difference_controls)
        row.setContentsMargins(0, 0, 0, 0)
        self.difference_check = QCheckBox('Δ')
        self.left = QComboBox()
        self.right = QComboBox()
        for combo in (self.left, self.right):
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        row.addWidget(self.difference_check)
        row.addWidget(self.left, 1)
        row.addWidget(QLabel('−'))
        row.addWidget(self.right, 1)
        layout.addWidget(self.difference_controls)
        self.difference_note = self._label(1)
        layout.addWidget(self.difference_note)
        self.summary = self._label(1)
        layout.addWidget(self.summary)
        # The viewer places this plot below the image/editor split. Only editors
        # scroll on short screens; axes, legend and current readings stay together.
        self.plot_panel = QWidget()
        plot = QVBoxLayout(self.plot_panel)
        plot.setContentsMargins(0, 0, 0, 0)
        plot.setSpacing(2)
        self.chart = TemporalChart()
        plot.addWidget(self.chart, 1)
        self.range_panel = RangePanel()
        self.range_panel.bounds_changed.connect(self.chart.set_analysis_range)
        self.chart.range_changed.connect(self.range_panel.set_bounds)
        range_controls = QHBoxLayout()
        self.range_mode = QCheckBox('Select range')
        self.range_mode.setToolTip('Drag a range on the plot; drag A/B to adjust. Uncheck to navigate samples.')
        self.range_mode.toggled.connect(self.chart.set_range_mode)
        range_controls.addWidget(self.range_mode)
        self.chart.setToolTip('Shift+drag selects a range. Drag A/B to adjust exact event bounds. Uncheck Select range to navigate samples.')
        legend = QLabel('● point / ROI mean · ▽ min · △ max · white Δ     │ orange gap · pink unavailable · gray segment')
        legend.setWordWrap(True)
        range_controls.addWidget(legend, 1)
        plot.addLayout(range_controls)
        self.reading = self._label(2)
        plot.addWidget(self.reading)
        self.busy_row = QWidget()
        busy = QHBoxLayout(self.busy_row)
        busy.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.cancel)
        busy.addWidget(self.progress, 1)
        busy.addWidget(self.cancel_button)
        layout.insertWidget(2, self.busy_row)
        self.busy_row.hide()
        self.message = self._label(2)
        layout.addWidget(self.message)
        layout.addStretch()
        self.plot_point.clicked.connect(self.add_point)
        self.plot_roi.clicked.connect(self.add_roi)
        self.compute.clicked.connect(lambda: self.calculate.emit(self.selections))
        self.save_csv.clicked.connect(self.export)
        self.difference_check.toggled.connect(self._refresh_display)
        self.left.currentIndexChanged.connect(self._refresh_display)
        self.right.currentIndexChanged.connect(self._refresh_display)
        self._refresh_display()

    def _label(self, lines):
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        label.setFixedHeight(label.fontMetrics().lineSpacing() * lines + 3)
        return label

    def reset(self):
        """A new source generation cannot inherit series, IDs or pending export inputs."""
        self.selections = ()
        self.comparison = None
        self._next_id = 1
        self._cursor = (None, None)
        self.range_mode.setChecked(False)
        self.range_panel.reset()
        self.difference_check.setChecked(False)
        self.message.clear()
        self._rebuild_items()
        self._refresh_display()

    def add_selection(self, native):
        if len(self.selections) >= MAX_SELECTIONS:
            return
        number = self._next_id
        self._next_id += 1
        used = {item.color for item in self.selections}
        color = next((c for c in CURVE_COLORS if c not in used), CURVE_COLORS[0])
        name = 'ROI' if native.kind == 'roi' else 'Point'
        self.selections += (NamedSelection(f'S{number}', f'{name} {number}', color, native),)
        self.comparison = None
        self.message.setText('Ready to calculate')
        self._rebuild_items()
        self._refresh_display()

    def set_result(self, comparison):
        self.comparison = comparison
        self.message.clear()
        self._refresh_display()

    def set_ready(self, idle, usable, point_ready):
        self._idle, self._usable, self._single_pixel = idle, usable, point_ready
        self._sync_controls()

    def set_busy(self, busy):
        """A cancellable scan/export does not prevent GUI repaint or window interaction."""
        self.busy_row.setVisible(busy)
        self.cancel_button.setEnabled(busy)

    def _sync_controls(self):
        room = len(self.selections) < MAX_SELECTIONS
        self.plot_point.setEnabled(bool(self._single_pixel and room))
        self.plot_roi.setEnabled(bool(self._usable and room))
        self.compute.setEnabled(bool(self._idle and self.selections))
        self.editor.setEnabled(self._idle)
        self.difference_controls.setEnabled(self._idle and len(self.selections) >= 2)
        self.save_csv.setEnabled(self._idle and self.comparison is not None)
        self.plot_point.setToolTip('Add the pinned pixel as a temperature curve.' if self.plot_point.isEnabled()
                                   else 'Pin a pixel with the Point tool and leave space among the four curves.')
        self.plot_roi.setToolTip('Add the mean temperature of the selected rectangle as a curve.' if self.plot_roi.isEnabled()
                                 else 'Drag a rectangle on a valid image and leave space among the four curves.')

    def _rebuild_items(self):
        """Rebuild only after adding/removing; editing names must preserve keyboard focus."""
        while self.items.count():
            self.items.takeAt(0).widget().deleteLater()
        self.item_widgets.clear()
        for item in self.selections:
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(3)
            visible = QCheckBox(item.id)
            visible.setChecked(item.visible)
            visible.setToolTip('Show/hide ' + item.native.label)
            color = QPushButton('●')
            color.setFixedWidth(26)
            color.setToolTip('Curve color')
            color.setStyleSheet(f'color: {item.color}; background: #192125')
            name = QLineEdit(item.name)
            name.setMaxLength(48)
            name.setMinimumWidth(60)
            name.setToolTip(item.native.label + '\n' + item.native.identity[0])
            minimum, maximum = QCheckBox('min'), QCheckBox('max')
            minimum.setChecked(item.show_min)
            maximum.setChecked(item.show_max)
            for check in (minimum, maximum):
                check.setEnabled(item.native.kind == 'roi')
            remove = QPushButton('×')
            remove.setFixedWidth(24)
            remove.setToolTip('Remove selection')
            for control in (visible, color, name, minimum, maximum, remove):
                row.addWidget(control, 1 if control is name else 0)
            self.items.addWidget(widget)
            self.item_widgets[item.id] = dict(visible=visible, color=color, name=name,
                                               minimum=minimum, maximum=maximum, remove=remove)
            visible.toggled.connect(lambda value, key=item.id: self._edit(key, visible=value))
            name.textEdited.connect(lambda value, key=item.id: self._apply_name(key, value or key))
            name.editingFinished.connect(lambda key=item.id, field=name: self._rename(key, field))
            color.clicked.connect(lambda checked=False, key=item.id: self._choose_color(key))
            minimum.toggled.connect(lambda value, key=item.id: self._edit(key, show_min=value))
            maximum.toggled.connect(lambda value, key=item.id: self._edit(key, show_max=value))
            remove.clicked.connect(lambda checked=False, key=item.id: self._remove(key))
        self._update_pair_choices()

    def _update_pair_choices(self):
        for index, combo in enumerate((self.left, self.right)):
            selected = combo.currentData() if combo.count() > index else None
            blocker = QSignalBlocker(combo)
            combo.clear()
            for item in self.selections:
                combo.addItem(f'{item.id} · {item.name}', item.id)
            match = combo.findData(selected)
            combo.setCurrentIndex(match if match >= 0 else min(index, len(self.selections) - 1))
            del blocker

    def _rename(self, key, field):
        name = field.text().strip() or key
        field.setText(name)
        self._apply_name(key, name)

    def _apply_name(self, key, name):
        """Commit visible typing immediately without rebuilding the expensive plot.

        A later Add, export chooser or disabled editor can change focus before
        editingFinished. The name shown to the user must already be authoritative.
        Only labels change: scalar series and cached plot marks remain untouched.
        """
        self.selections = tuple(replace(s, name=name) if s.id == key else s for s in self.selections)
        if self.comparison:
            self.comparison = replace(self.comparison, selections=self.selections)
            self.chart.comparison = self.comparison
        self._update_pair_choices()
        self.set_cursor(*self._cursor)
        self._update_range_comparison()

    def _choose_color(self, key):
        item = next(s for s in self.selections if s.id == key)
        color = QColorDialog.getColor(QColor(item.color), self, 'Curve color')
        # The dialog runs a nested event loop; a replaced source has different owners.
        if color.isValid() and any(s is item for s in self.selections):
            self.item_widgets[key]['color'].setStyleSheet(f'color: {color.name()}; background: #192125')
            self._edit(key, color=color.name())

    def _edit(self, key, **changes):
        self.selections = tuple(replace(s, **changes) if s.id == key else s for s in self.selections)
        if self.comparison:
            self.comparison = replace(self.comparison, selections=self.selections)
        self._refresh_display()

    def _remove(self, key):
        remaining = tuple(i for i, s in enumerate(self.selections) if s.id != key)
        self.selections = tuple(self.selections[i] for i in remaining)
        if self.comparison:
            self.comparison = Comparison(self.selections, tuple(self.comparison.series[i] for i in remaining)) if remaining else None
        if key in (self.left.currentData(), self.right.currentData()):
            self.difference_check.setChecked(False)
        self._rebuild_items()
        self._refresh_display()

    def difference_pair(self):
        pair = self.left.currentData(), self.right.currentData()
        if self.comparison and self.difference_check.isChecked() and not self.comparison.difference_error(pair):
            return pair
        return None

    def _refresh_display(self, *unused):
        pair = self.left.currentData(), self.right.currentData()
        error = self.comparison.difference_error(pair) if self.comparison else 'Calculate selections first'
        self.difference_note.setText(('Δ unavailable: ' + error) if self.difference_check.isChecked() and error
                                    else 'Δ = left − right, same observation only' if self.difference_check.isChecked() else '')
        self.difference_note.setToolTip(self.difference_note.text())
        self.chart.set_comparison(self.comparison, self.difference_pair())
        self._update_range_comparison()
        summary = f'{len(self.selections)}/{MAX_SELECTIONS} selections'
        if self.comparison:
            first = self.comparison.series[0]
            summary += ' · verified prefix, unknown tail' if first.unknown_tail else f' · END {first.end_status}'
        elif self.selections:
            summary += ' · Calculate to update chart'
        self.summary.setText(summary)
        self.set_cursor(*self._cursor)
        self._sync_controls()

    def _update_range_comparison(self):
        # Keep a refused requested pair visible in the summary as incompatible;
        # the plot and detailed CSV retain their existing compatibility gate.
        pair = (self.left.currentData(), self.right.currentData()) if self.difference_check.isChecked() else None
        self.range_panel.set_comparison(self.comparison, pair)
        self.range_mode.setEnabled(self.comparison is not None)

    def set_cursor(self, time_ns, index):
        """Show per-selection current values; a blank event never inherits the last reading."""
        self._cursor = time_ns, index
        self.chart.set_cursor(time_ns, index)
        self.range_panel.set_cursor(time_ns)
        if not self.comparison or index is None:
            self.reading.clear()
            return
        texts = []
        for item, series in zip(self.selections, self.comparison.series):
            if not item.visible:
                continue
            row = series.at_event(index)
            if row is None:
                text = 'no stream event'
            elif row.temperature is not None:
                text = f'{row.temperature:.6g} °C'
                if item.native.kind == 'roi':
                    text += f' [min {row.reading.min_c:.6g}, max {row.reading.max_c:.6g}]'
            else:
                text = row.status.replace('_', ' ') + (f' · {row.gap_category or row.reason}' if row.gap_category or row.reason else '')
            texts.append(f'{item.id} {item.name}: {text}')
        pair = self.difference_pair()
        if pair:
            a, b = (self.comparison.by_id(key).at_event(index) for key in pair)
            value = difference_value(a, b)[0] if a else None
            texts.append('Δ: ' + (f'{value:.9g} °C' if value is not None else 'unavailable'))
        text = f'Event {index + 1} · ' + ' | '.join(texts)
        self.reading.setText(text)
        self.reading.setToolTip(text)
