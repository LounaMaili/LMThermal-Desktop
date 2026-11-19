"""Qt plot of actual stored samples and explicit non-reading events."""
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QWidget


class TemporalChart(QWidget):
    """Cached marks and an independently moving playback cursor."""
    seek_event = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 170)
        self.series = None
        self._layer = None
        self._cursor_ns = None
        self._cursor_index = None
        self._range = (0.0, 1.0)

    def set_series(self, series):
        self.series = series
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
        values = [row.temperature for row in self.series.rows if row.temperature is not None]
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
        painter.drawText(QPointF(8, 14), '°C')
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
        points = []
        for row in self.series.rows:
            x = self.time_x(row.time_ns)
            if row.temperature is not None:
                y = self._temperature_y(row.temperature)
                points.append(QPointF(x, y))
            else:
                y = rect.bottom()
                path, length = (gap_marks, 11) if row.event == 'gap' else (
                    (boundary_marks, 5) if row.event.startswith('segment_') else (missing_marks, 8))
                path.moveTo(x, y + 2)
                path.lineTo(x, y + 2 + length)
        painter.setPen(QPen(QColor('#67e3d2'), 4, cap=Qt.PenCapStyle.RoundCap))
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
        row = self.series.at_event(self._cursor_index)
        if row is not None and row.temperature is not None:
            painter.setPen(QPen(Qt.GlobalColor.white, 2))
            painter.drawEllipse(QPointF(x, self._temperature_y(row.temperature)), 5, 5)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.series is None:
            return
        pos = event.position()
        rect = self._bounds()
        if not rect.adjusted(-6, -4, 6, 15).contains(pos):
            return
        fraction = min(1, max(0, (pos.x() - rect.left()) / rect.width()))
        start, end = self.series.first_ns, self.series.last_ns
        rows = self.series.nearest_rows(start + round((end - start) * fraction))
        if not rows:
            return

        def distance(row):
            if row.temperature is not None:
                y = self._temperature_y(row.temperature)
            else:
                y = rect.bottom() + 7
            return abs(pos.y() - y), row.index != self._cursor_index, row.index

        self.seek_event.emit(min(rows, key=distance).index)
