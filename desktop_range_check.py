"""Own-process range interactions on the exposed/frozen real-recording scenario."""
from dataclasses import asdict

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest


def check_range(app, workspace, viewer, output, report, wait, snapshot):
    """Use graph gestures, actual controls and selectors; never claim human acceptance."""
    from desktop_analysis_check import choose_file
    panel = viewer.temporal_panel
    stats, chart = panel.range_panel, panel.chart
    series = panel.comparison.series
    valid = [r for r in series[0].rows if r.temperature is not None]
    a, b = valid[len(valid)//4].time_ns, valid[3*len(valid)//4].time_ns
    wait(lambda: stats.result is not None, 'initial full-period statistics')
    full = stats.bounds
    original_cursor = chart._cursor_index
    panel.range_mode.click()

    def gesture(left, right, *, handle=False):
        y = chart._bounds().top()-3 if handle else chart._bounds().center().y()
        for kind, ns, buttons in ((QEvent.Type.MouseButtonPress, left, Qt.MouseButton.LeftButton),
                                   (QEvent.Type.MouseMove, right, Qt.MouseButton.LeftButton),
                                   (QEvent.Type.MouseButtonRelease, right, Qt.MouseButton.NoButton)):
            app.sendEvent(chart, QMouseEvent(kind, QPointF(chart.time_x(ns), y),
                          Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier))
        wait(lambda: stats.result is not None, 'range statistics after graph gesture')

    gesture(a, b)
    assert stats.bounds == (a, b), stats.bounds
    assert chart._cursor_index == original_cursor and panel.comparison.series is series
    assert viewer.analysis_tabs.currentIndex() == 1
    assert stats.table.rowCount() == 5
    # Adjust the right handle during playback; range selection cannot pause/seek.
    viewer.speed.setCurrentIndex(0)
    wait(lambda: not viewer._in_flight, 'half-speed playback')
    viewer.play.click()
    wait(lambda: viewer.state['playing'] and not viewer._in_flight, 'play while selecting range')
    playback_index = viewer.state['index']
    new_b = valid[min(len(valid)-1, 3*len(valid)//4+1)].time_ns
    gesture(b, new_b, handle=True)
    assert viewer.state['playing'] and stats.bounds == (a, new_b)
    held_rows = stats.result.rows
    wait(lambda: viewer.state['index'] > playback_index and not viewer._in_flight, 'playback across selected range')
    assert stats.result.rows is held_rows and chart._cursor_index == viewer.state['index']
    if viewer.state['playing']:
        viewer.play.click()
        wait(lambda: not viewer._in_flight and not viewer.state['playing'], 'pause after range check')
    stats.full.click()
    wait(lambda: stats.result is not None, 'full-period reset')
    assert stats.bounds == full
    # A queued scan can be cancelled before its first GUI slice; in-progress
    # cancellation and latest-result replacement are separately covered by tests.
    stats.full.click(); stats.cancel.click()
    assert not stats.timer.isActive() and stats.result is None and 'cancelled' in stats.message.text()
    stats.apply.click()
    wait(lambda: stats.result is not None, 'calculate after range cancellation')
    gesture(a, b)
    chosen = stats.bounds
    report['range_statistics'] = dict(bounds_ns=list(chosen), inclusive=True,
        aggregation='arithmetic mean of valid observations, not time weighted',
        rows=[asdict(row) for row in stats.result.rows], playback_continued=True,
        scalar_series_reused=True, queued_calculation_cancelled=True)
    snapshot('06-range-statistics.png')
    choose_file(app, stats.save.click, output / 'range-summary.csv', report['actions'])
    wait(lambda: not viewer._in_flight, 'range summary CSV export')
    assert 'Summary CSV saved' in stats.message.text(), stats.message.text()

    usable_times = {r.time_ns for s in series for r in s.rows if r.temperature is not None}
    blank = next((r for r in series[0].rows if r.event == 'gap' and r.time_ns not in usable_times), None)
    if blank is None:
        # Some recordings give segment declarations the same instant as a valid
        # observation. Select a genuinely empty integer interval instead.
        represented = {r.time_ns for s in series for r in s.rows}
        empty_ns = full[0]
        while empty_ns in represented:
            empty_ns += 1
        assert empty_ns <= full[1]
    else:
        empty_ns = blank.time_ns
    for edit in (stats.start, stats.end):
        edit.setFocus(); edit.selectAll(); QTest.keyClicks(edit, str(empty_ns))
    stats.apply.click()
    wait(lambda: stats.result is not None, 'single-instant range')
    assert all(r.mean_c is None for r in stats.result.rows)
    assert 'No usable data' in stats.message.text()
    report['empty_range'] = dict(bounds_ns=list(stats.bounds), event=blank.event if blank else 'no_event',
                                rows=[asdict(row) for row in stats.result.rows])
    snapshot('07-unavailable-range.png')
    choose_file(app, stats.save.click, output / 'unavailable-summary.csv', report['actions'])
    wait(lambda: not viewer._in_flight, 'unavailable range CSV export')
    for edit, ns in zip((stats.start, stats.end), chosen):
        edit.setFocus(); edit.selectAll(); QTest.keyClicks(edit, str(ns))
    stats.apply.click()
    wait(lambda: stats.result is not None, 'restore selected period')
    workspace.resize(940, 740)
    QTest.qWait(50)
    assert workspace.width() <= 940 and workspace.height() <= 740
    snapshot('08-range-small-window.png')
    workspace.resize(1180, 900)
    panel.range_mode.setChecked(False)
    viewer.analysis_tabs.setCurrentIndex(0)
