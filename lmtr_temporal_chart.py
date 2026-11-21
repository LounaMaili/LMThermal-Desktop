"""Qt plot of actual stored samples and explicit non-reading events."""
from dataclasses import dataclass

from lmtr_comparison import difference_value

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QWidget


@dataclass(frozen=True)
class PlotTrack:
    """A lightweight scalar view: extrema and differences need no new file traversal."""
    series: object
    color: str
    statistic: str = 'mean_c'
    right: object = None

    def value(self, row):
        if self.right is not None:
            return difference_value(row, self.right.at_event(row.index))[0]
        return None if row.reading is None else getattr(row.reading, self.statistic)

    def samples(self):
        for row in self.series.rows:
            yield row, self.value(row)


class TemporalChart(QWidget):
    """Cached marks and an independently moving playback cursor."""
    seek_event = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 150)
        self.series = None
        self.comparison = None
        self.tracks = ()
        self._layer = None
        self._cursor_ns = None
        self._cursor_index = None
        self._range = (0.0, 1.0)
        self._delta_range = (0.0, 1.0)

    def set_comparison(self, comparison, difference=None):
        """Invalidate the cached marks only when data or display properties change."""
        self.comparison = comparison
        self.series = comparison.series[0] if comparison else None
        tracks = []
        if comparison:
            for item, series in zip(comparison.selections, comparison.series):
                if not item.visible:
                    continue
                tracks.append(PlotTrack(series, item.color))
                if item.native.kind == 'roi':
                    if item.show_min:
                        tracks.append(PlotTrack(series, item.color, 'min_c'))
                    if item.show_max:
                        tracks.append(PlotTrack(series, item.color, 'max_c'))
            if difference and not comparison.difference_error(difference):
                left, right = (comparison.by_id(key) for key in difference)
                tracks.append(PlotTrack(left, '#ffffff', right=right))
        self.tracks = tuple(tracks)
        self._layer = None
        self.update()

    def set_series(self, series):
        """Compatibility entry point for standalone single-selection plots."""
        self.comparison = None
        self.series = series
        self.tracks = () if series is None else (PlotTrack(series, '#67e3d2'),)
        self._cursor_ns = self._cursor_index = None
        self._layer = None
        self.update()

    def set_cursor(self, time_ns, index):
        self._cursor_ns, self._cursor_index = time_ns, index
        self.update()

    def resizeEvent(self, event):
        self._layer = None
        super().resizeEvent(event)

    def _bounds(self):
        """Common time axis for both temperature and difference panes."""
        return QRectF(74, 22, max(1, self.width() - 100), max(1, self.height() - 65))

    def _plot_bounds(self, delta=False):
        rect = self._bounds()
        if not any(track.right is not None for track in self.tracks):
            return rect
        # Independent Celsius scales keep small differences from compressing the
        # absolute readings. Both panes share original-event horizontal positions.
        height = max(1, (rect.height() - 24) * 0.62)
        if delta:
            return QRectF(rect.left(), rect.top() + height + 24, rect.width(),
                          max(1, rect.height() - height - 24))
        return QRectF(rect.left(), rect.top(), rect.width(), height)

    def time_x(self, time_ns):
        rect = self._bounds()
        span = self.series.last_ns - self.series.first_ns
        if not span:
            return rect.center().x()
        return rect.left() + (time_ns - self.series.first_ns) / span * rect.width()

    def _temperature_y(self, value, delta=False):
        rect = self._plot_bounds(delta)
        lo, hi = self._delta_range if delta else self._range
        return rect.bottom() - (value - lo) / (hi - lo) * rect.height()

    def _make_layer(self):
        ratio = self.devicePixelRatioF()
        self._layer = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        self._layer.setDevicePixelRatio(ratio)
        background = QColor('#192125')
        foreground = QColor('#dae6e9')
        self._layer.fill(background)
        painter = QPainter(self._layer)
        try:
            painter.setPen(foreground)
            if self.series is None:
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'No series selected')
                return
            self._draw_series(painter, foreground)
        finally:
            painter.end()

    def _draw_series(self, painter, foreground):
        has_delta = any(track.right is not None for track in self.tracks)
        for delta in (False, True) if has_delta else (False,):
            values = [value for track in self.tracks if (track.right is not None) == delta
                      for _, value in track.samples() if value is not None]
            lo, hi = min(values, default=0.0), max(values, default=1.0)
            padding = max((hi - lo) * 0.08, 0.5 if lo == hi else 0.0)
            limits = lo - padding, hi + padding
            if delta:
                self._delta_range = limits
            else:
                self._range = limits
            rect = self._plot_bounds(delta)
            # On short panes two labelled levels stay legible; the exact value
            # remains in the current readout and round-trip CSV, not an axis tick.
            portions = (0.0, 0.5, 1.0) if rect.height() >= 85 else (0.0, 1.0)
            for portion in portions:
                y = rect.bottom() - rect.height() * portion
                label = limits[0] + (limits[1] - limits[0]) * portion
                painter.setPen(QColor('#415359'))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                painter.setPen(foreground)
                painter.drawText(QRectF(2, y - 9, rect.left() - 10, 18),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f'{label:.4g}')
            painter.setPen(foreground)
            painter.drawText(QPointF(rect.left(), rect.top() - 7),
                             'Difference Δ (°C)' if delta else 'Temperature (°C)')
            if not values:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                                 'No available differences' if delta else 'No visible temperatures')

        rect = self._bounds()
        painter.setPen(foreground)
        duration = (self.series.last_ns - self.series.first_ns) / 1e9
        for portion in (0.0, 0.5, 1.0):
            x = rect.left() + portion * rect.width()
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x, rect.bottom() + 4))
            painter.drawText(QRectF(x - 35, rect.bottom() + 13, 70, 17),
                             Qt.AlignmentFlag.AlignCenter, f'{duration * portion:.4g}')
        painter.drawText(QRectF(0, self.height() - 16, self.width(), 16),
                         Qt.AlignmentFlag.AlignCenter, 'Time since first event (s)')

        gap_marks, missing_marks, boundary_marks = QPainterPath(), QPainterPath(), QPainterPath()
        for track in self.tracks:
            points, extrema = [], QPainterPath()
            drawn = set()
            for row, value in track.samples():
                x = self.time_x(row.time_ns)
                if value is not None:
                    y = self._temperature_y(value, track.right is not None)
                    # Duplicate screen pixels need only one glyph. This affects
                    # raster presentation only: no scalar event/export is removed.
                    pixel = round(x), round(y)
                    if pixel in drawn:
                        continue
                    drawn.add(pixel)
                    if track.statistic == 'mean_c':
                        points.append(QPointF(x, y))
                    else:
                        direction = 1 if track.statistic == 'min_c' else -1
                        extrema.moveTo(x, y + direction * 3)
                        extrema.lineTo(x - 3, y - direction * 2)
                        extrema.lineTo(x + 3, y - direction * 2)
                        extrema.closeSubpath()
                else:
                    path, length = (gap_marks, 9) if row.event == 'gap' else (
                        (boundary_marks, 4) if row.event.startswith('segment_') else (missing_marks, 7))
                    path.moveTo(x, rect.bottom() + 2)
                    path.lineTo(x, rect.bottom() + 2 + length)
            painter.setPen(QPen(QColor(track.color), 4, cap=Qt.PenCapStyle.RoundCap))
            painter.drawPoints(QPolygonF(points))
            painter.strokePath(extrema, QPen(QColor(track.color), 1.2))
        for path, color in ((gap_marks, '#ffb34b'), (missing_marks, '#f695bd'), (boundary_marks, '#9dadb3')):
            painter.strokePath(path, QPen(QColor(color), 2))

    def paintEvent(self, event):
        if self._layer is None:
            self._make_layer()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._layer)
        if self.series is None or self._cursor_ns is None:
            return
        x = self.time_x(self._cursor_ns)
        rect = self._bounds()
        painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom() + 15))
        for track in self.tracks:
            row = track.series.at_event(self._cursor_index)
            value = track.value(row) if row is not None else None
            if value is not None:
                painter.setPen(QPen(QColor(track.color), 2))
                painter.drawEllipse(QPointF(x, self._temperature_y(value, track.right is not None)), 5, 5)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.series is None:
            return
        pos = event.position()
        rect = self._bounds()
        if not rect.adjusted(-6, -4, 6, 15).contains(pos):
            return
        fraction = min(1, max(0, (pos.x() - rect.left()) / rect.width()))
        start, end = self.series.first_ns, self.series.last_ns
        candidates = [(track, row) for track in self.tracks
                      for row in track.series.nearest_rows(start + round((end - start) * fraction))]
        if not candidates:
            return

        def distance(candidate):
            track, row = candidate
            value = track.value(row)
            y = self._temperature_y(value, track.right is not None) if value is not None else rect.bottom() + 7
            return abs(pos.x() - self.time_x(row.time_ns)), abs(pos.y() - y), row.index != self._cursor_index, row.index

        self.seek_event.emit(min(candidates, key=distance)[1].index)
