"""Opt-in functional LMTR scenario through actual Qt widgets and file selectors.

This developer path is also bundled for exact-candidate acceptance. It never
opens hardware, rewrites the input, substitutes a chooser result, or claims a
human operator has tested the application. Only this process is inspected.
"""
import csv
import hashlib
import json
from pathlib import Path
import time

from PyQt6.QtCore import QEvent, QPointF, QTimer, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog, QLineEdit

from mvp_presentation import native_to_widget
from offline_measurement import Rectangle


def choose_file(app, action, path, actions):
    """Use the real Qt fallback selector; report unsupported native providers honestly."""
    row = {'path_name': path.name, 'selector_replaced': False}
    actions.append(row)
    failures = []
    deadline = time.monotonic() + 12
    timer = QTimer()
    timer.setInterval(50)

    def operate():
        dialogs = [d for d in app.topLevelWidgets() if isinstance(d, QFileDialog)]
        if time.monotonic() > deadline:
            failures.append('File selector did not complete; this check supports visible Qt selectors only')
            timer.stop()
            for dialog in dialogs:
                dialog.reject()
            return
        for dialog in dialogs:
            edit = dialog.findChild(QLineEdit, 'fileNameEdit')
            if dialog.isVisible() and edit and not row.get('submitted'):
                row.update(provider='actual Qt fallback selector', submitted=True)
                edit.setText(str(path))
                edit.setFocus()
                QTest.keyClick(edit, Qt.Key.Key_Return)

    timer.timeout.connect(operate)
    timer.start()
    try:
        action()
    finally:
        timer.stop()
    if failures or not row.get('submitted'):
        raise RuntimeError(failures[0] if failures else 'No supported real file selector observed')


def run(app, workspace, source, output, *, include_range=False):
    """Collect four selections, inspect controls/navigation/cancel and export a real CSV."""
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = dict(status='running', source_sha256=before, platform=app.platformName(),
                  frozen=bool(getattr(__import__('sys'), 'frozen', False)), operator_acceptance=False,
                  camera_opened=False, actions=[], checks=[])

    def wait(condition, label):
        deadline = time.monotonic() + 30
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        if not condition():
            raise TimeoutError(label)

    def snapshot(name):
        QTest.qWait(40)
        if not workspace.grab().save(str(output / name)):
            raise OSError('Could not save own-window screenshot')

    try:
        workspace.resize(1180, 900)
        if app.platformName() not in ('offscreen', 'minimal'):
            assert QTest.qWaitForWindowExposed(workspace, 5000), 'native window not exposed'
        report['native_window_exposed'] = app.platformName() not in ('offscreen', 'minimal') and workspace.windowHandle().isExposed()
        choose_file(app, workspace.choose_lmtr, source, report['actions'])
        viewer = workspace.tabs.currentWidget()
        wait(lambda: viewer.state and viewer.frame is not None and not viewer._in_flight, 'normal LMTR open')
        frame = viewer.frame
        report['initial_event'] = viewer.state['index']
        report['end_status'] = viewer.state['report'].get('declared_end_status')
        snapshot('01-main-window.png')
        viewer.temporal_button.click()
        panel = viewer.temporal_panel
        QTest.qWait(50)
        width, height = frame.geometry.width, frame.geometry.height
        warm, cool = frame.high_xy, frame.low_xy
        small_x, small_y = min(warm[0], max(0, width - 16)), min(warm[1], max(0, height - 16))
        specs = (
            ('point', 'Warm point', Rectangle(warm[0], warm[1], warm[0] + 1, warm[1] + 1)),
            ('point', 'Cool point', Rectangle(cool[0], cool[1], cool[0] + 1, cool[1] + 1)),
            ('roi', 'Small ROI', Rectangle(small_x, small_y, min(width, small_x + 16), min(height, small_y + 16))),
            ('roi', 'Large ROI', Rectangle(width // 4, height // 4, max(width // 4 + 1, 3 * width // 4), max(height // 4 + 1, 3 * height // 4))),
        )
        expected = []
        for kind, name, region in specs:
            def position(x, y):
                return QPointF(*native_to_widget(x, y, viewer.image.width(), viewer.image.height(), geometry=frame.geometry))
            app.sendEvent(viewer.image, QMouseEvent(QEvent.Type.MouseButtonPress, position(region.x1, region.y1),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            app.sendEvent(viewer.image, QMouseEvent(QEvent.Type.MouseButtonRelease, position(region.x2 - 1, region.y2 - 1),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
            (panel.plot_point if kind == 'point' else panel.plot_roi).click()
            selected = panel.selections[-1]
            assert selected.native.region == region
            field = panel.item_widgets[selected.id]['name']
            field.setFocus()
            field.selectAll()
            QTest.keyClicks(field, name)
            QTest.keyClick(field, Qt.Key.Key_Tab)
            assert panel.selections[-1].name == name
            expected.append(frame.point(region.x1, region.y1) if kind == 'point' else frame.roi_statistics(region).mean_c)
            QTest.qWait(20)
        assert len(panel.selections) == 4 and not panel.plot_roi.isEnabled()
        panel.plot_roi.click()
        assert len(panel.selections) == 4
        assert expected[0] > expected[1], 'source does not provide two different initial temperatures'
        for key in ('S3', 'S4'):
            panel.item_widgets[key]['minimum'].click()
            panel.item_widgets[key]['maximum'].click()
        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
        heartbeat.start()
        started = time.monotonic()
        panel.compute.click()
        wait(lambda: panel.comparison and not viewer._in_flight, 'four-selection calculation')
        heartbeat.stop()
        report['calculation'] = dict(elapsed_s=time.monotonic() - started, gui_timer_ticks=len(ticks),
                                    largest_timer_interval_s=max((b - a for a, b in zip(ticks, ticks[1:])), default=None))
        index = viewer.state['index']
        assert [s.at_event(index).temperature for s in panel.comparison.series] == expected
        report['initial_values_c'] = expected
        report['selections'] = [dict(id=s.id, name=s.name, kind=s.native.kind,
                                     bounds=[s.native.region.x1, s.native.region.y1, s.native.region.x2, s.native.region.y2])
                                 for s in panel.selections]
        snapshot('02-four-selections.png')
        old = panel.comparison
        panel.compute.click()
        QTimer.singleShot(20, panel.cancel_button.click)
        wait(lambda: not viewer._in_flight, 'Cancel calculation')
        assert 'cancelled' in panel.message.text().lower()
        assert panel.comparison is old and viewer.frame is not None
        report['checks'].append('real calculation cancellation preserves open file and previous complete result')
        snapshot('calculation-cancelled.png')
        panel.compute.click()
        wait(lambda: not viewer._in_flight and panel.comparison is not old, 'calculate again after cancellation')
        panel.item_widgets['S2']['visible'].click()
        assert len(panel.chart.tracks) == 7
        panel.item_widgets['S2']['visible'].click()
        assert len(panel.chart.tracks) == 8
        field = panel.item_widgets['S1']['name']
        field.setFocus(); field.selectAll(); QTest.keyClicks(field, 'Point A'); QTest.keyClick(field, Qt.Key.Key_Tab)
        assert panel.selections[0].name == 'Point A' and 'Point A' in panel.left.currentText()
        panel.difference_check.click()
        assert panel.difference_pair() == ('S1', 'S2')
        original_series = panel.comparison.series
        valid = [r for r in original_series[0].rows if r.temperature is not None]
        target = valid[len(valid) // 2]
        pos = QPointF(panel.chart.time_x(target.time_ns), panel.chart._temperature_y(target.temperature))
        app.sendEvent(panel.chart, QMouseEvent(QEvent.Type.MouseButtonPress, pos,
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        wait(lambda: not viewer._in_flight, 'chart seek')
        assert viewer.state['index'] == target.index
        assert panel.chart._cursor_index == target.index
        first = panel.selections[0].native.region
        assert viewer.frame.point(first.x1, first.y1) == target.temperature
        report['checks'].append('chart cursor selects original event and matching image')
        snapshot('03-difference.png')
        viewer.speed.setCurrentIndex(3)
        wait(lambda: not viewer._in_flight, 'playback speed')
        viewer.play.click()
        wait(lambda: viewer.state['index'] > target.index and not viewer._in_flight, 'playback advances')
        if viewer.state['playing']:
            viewer.play.click()
            wait(lambda: not viewer._in_flight and not viewer.state['playing'], 'pause')
        assert panel.chart._cursor_index == viewer.state['index']
        blank = next((r for r in original_series[0].rows if r.event == 'gap'), original_series[0].rows[0])
        viewer._request('seek', blank.index)
        wait(lambda: not viewer._in_flight, 'unavailable event navigation')
        assert viewer.frame is None and 'unavailable' in panel.reading.text()
        report['unavailable_event'] = dict(index=blank.index, event=blank.event, status=blank.status,
                                           category=blank.gap_category, reason=blank.reason)
        snapshot('04-unavailable.png')
        viewer._request('seek', target.index)
        wait(lambda: not viewer._in_flight and viewer.frame is not None, 'image recovery')
        workspace.resize(940, 740)
        QTest.qWait(60)
        assert workspace.width() <= 940 and workspace.height() <= 740, (workspace.width(), workspace.height())
        assert panel.chart.isVisible() and panel.reading.isVisible()
        assert panel.comparison.series is original_series
        report['small_window'] = [workspace.width(), workspace.height()]
        snapshot('05-small-window.png')
        workspace.resize(1180, 900)
        if include_range:
            from desktop_range_check import check_range
            check_range(app, workspace, viewer, output, report, wait, snapshot)
        choose_file(app, panel.save_csv.click, output / 'comparison.csv', report['actions'])
        wait(lambda: not viewer._in_flight, 'CSV export')
        assert 'CSV saved' in panel.message.text(), panel.message.text()
        with (output / 'comparison.csv').open(newline='', encoding='utf-8') as data:
            report['csv_rows'] = sum(1 for _ in csv.DictReader(data))
        panel.item_widgets['S2']['remove'].click()
        assert tuple(s.id for s in panel.selections) == ('S1', 'S3', 'S4')
        assert panel.difference_pair() is None
        report['checks'].extend(['four-selection limit', 'ROI mean/min/max', 'hide and reactivate',
                                 'rename preserves ID', 'remove difference operand', 'play/pause cursor synchronization',
                                 'resize preserves scalar series', 'CSV through actual selector'])
        report['status'] = 'passed'
    except Exception as error:
        import traceback
        report.update(status='failed', error=repr(error), traceback=traceback.format_exc())
        snapshot('failure.png')
    finally:
        workspace.close()
        try:
            wait(lambda: not workspace._documents, 'cooperative Close')
            report['closed_normally'] = not workspace.isVisible()
        except Exception as error:
            report.update(status='failed', close_error=str(error))
        report['source_unchanged'] = hashlib.sha256(source.read_bytes()).hexdigest() == before
        if not report['source_unchanged']:
            report['status'] = 'failed'
        (output / 'analysis-check.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
