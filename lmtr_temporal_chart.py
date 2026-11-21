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
        self.setMinimumSize(260, 170)
        self.series = None
        self.comparison = None
        self.tracks = ()
        self._layer = None
        self._cursor_ns = None
        self._cursor_index = None
        self._range = (0.0, 1.0)

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
        return QRectF(62, 18, max(1, self.width() - 82), max(1, self.height() - 71))

    def time_x(self, time_ns):
        rect = self._bounds()
        span = self.series.last_ns - self.series.first_ns
        if not span:
            return rect.center().x()
        return rect.left() + (time_ns - self.series.first_ns) / span * rect.width()

    def _temperature_y(self, value):
        rect = self._bounds()
        lo, hi = self._range
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
        rect = self._bounds()
        values = [value for track in self.tracks for _, value in track.samples() if value is not None]
        lo, hi = min(values, default=0.0), max(values, default=1.0)
        padding = max((hi - lo) * 0.06, 0.5 if lo == hi else 0.0)
        self._range = lo - padding, hi + padding
        for portion in (0.0, 0.5, 1.0) if values else ():
            y = rect.bottom() - rect.height() * portion
            label = self._range[0] + (self._range[1] - self._range[0]) * portion
            painter.setPen(QColor('#415359'))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(foreground)
            painter.drawText(QRectF(2, y - 10, rect.left() - 8, 20),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f'{label:.5g}')
        painter.setPen(foreground)
        painter.drawText(QPointF(8, 14), '°C / Δ°C' if any(t.right for t in self.tracks) else '°C')
        painter.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom()))
        duration = (self.series.last_ns - self.series.first_ns) / 1e9
        axis = QRectF(rect.left(), rect.bottom() + 17, rect.width(), 17)
        painter.drawText(axis, Qt.AlignmentFlag.AlignLeft, '0 s')
        painter.drawText(axis, Qt.AlignmentFlag.AlignRight, f'{duration:.6g} s' if duration else '')
        painter.drawText(QRectF(0, rect.bottom() + 34, self.width(), 17),
                         Qt.AlignmentFlag.AlignCenter, 'Time since first event')
        if not values:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, 'No valid temperatures')

        gap_marks, missing_marks, boundary_marks = QPainterPath(), QPainterPath(), QPainterPath()
        for track in self.tracks:
            points = []
            for row, value in track.samples():
                x = self.time_x(row.time_ns)
                if value is not None:
                    points.append(QPointF(x, self._temperature_y(value)))
                else:
                    path, length = (gap_marks, 11) if row.event == 'gap' else (
                        (boundary_marks, 5) if row.event.startswith('segment_') else (missing_marks, 8))
                    path.moveTo(x, rect.bottom() + 2)
                    path.lineTo(x, rect.bottom() + 2 + length)
            # Mark observations only. Even a missing instant between two good
            # samples must never be crossed by an interpolated line.
            color = QColor(track.color)
            color.setAlpha(150 if track.statistic == 'min_c' else 255)
            size = 2 if track.statistic != 'mean_c' else 4
            cap = Qt.PenCapStyle.SquareCap if track.statistic == 'max_c' else Qt.PenCapStyle.RoundCap
            painter.setPen(QPen(color, size, cap=cap))
            painter.drawPoints(QPolygonF(points))
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
                painter.drawEllipse(QPointF(x, self._temperature_y(value)), 5, 5)

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
            y = self._temperature_y(value) if value is not None else rect.bottom() + 7
            return abs(pos.x() - self.time_x(row.time_ns)), abs(pos.y() - y), row.index != self._cursor_index, row.index

        self.seek_event.emit(min(candidates, key=distance)[1].index)
