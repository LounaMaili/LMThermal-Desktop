"""Opt-in offline window qualification, reusable in the frozen application; never opens hardware.

This drives only this process's Qt widgets on an explicitly supplied, non-sensitive source.
Timings include event-loop waits and are not acquisition FPS or operator-visible latency.
"""
import hashlib
import json
import time
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPointF, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QDialog, QPlainTextEdit


def hashes(path):
    """Include all regular source members, including legacy sidecars, before and after inspection."""
    paths = sorted(path.rglob('*')) if path.is_dir() else [path]
    if path.suffix == '.json':
        paths += [p for p in (path.with_suffix('.npz'), path.with_suffix('.png')) if p.exists()]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def pointer_probe(app, image, position, expected):
    """Check our synchronous event before pumping unrelated native cursor/enter events.

    A real desktop may legitimately move the cursor during the later event-loop wait.
    That subsequent input is not evidence that this probe's inverse mapping was wrong.
    """
    app.sendEvent(image,QMouseEvent(QEvent.Type.MouseMove,QPointF(*position),
        Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier))
    actual=image.pointer
    assert actual==expected,('pointer mapping',position,actual)
    return actual


def run(app, window, path, output, *, expect_error=False):
    """Run a bounded developer check through actual viewers; normal launches never enter here."""
    from mvp_presentation import native_to_widget
    from roi_measurement import NativeROI
    from lmtr_viewer import LmtrWindow
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    before = hashes(path)
    if not before:
        raise ValueError('Offline window check requires an existing regular source')
    result = dict(status='running', platform=app.platformName(), physical_camera_opened=False,
                  operator_validation=False, timing_scope='Inclusive GUI/event-loop wall time; not visible latency',
                  source_before=before, checks=[])
    viewer = window.tabs.currentWidget()
    lmtr = isinstance(viewer, LmtrWindow)
    def wait(condition, label):
        start = time.monotonic()
        while not condition() and time.monotonic() - start < 15:
            app.processEvents(); QTest.qWait(10)
        if not condition():
            raise TimeoutError(label)
        return (time.monotonic()-start)*1000
    def settle():
        app.processEvents(); QTest.qWait(40); app.processEvents()
    try:
        settle()
        if app.platformName() not in ('offscreen','minimal'):
            window.raise_(); window.activateWindow()
            wait(lambda:window.windowHandle().isExposed(),'native window exposure')
        result['window_exposed'] = window.windowHandle().isExposed()
        result['initial_window_size'] = [window.width(),window.height()]
        if expect_error:
            result['ready_wait_after_test_settle_ms'] = wait(lambda: ('failed' in viewer.status.text().lower()) if lmtr
                else ('failed' in viewer.state_label.text().lower()), 'expected visible read refusal')
            assert (viewer.frame if lmtr else viewer.observation) is None
            image = viewer.image if lmtr else viewer.image_widget
            assert image.image is None
            result['checks'].append('visible_error_without_stale_image')
        else:
            if lmtr:
                result['ready_wait_after_test_settle_ms'] = wait(lambda: viewer.state is not None, 'LMTR open')
                result['initial_event_index']=viewer.state['index']
                result['initial_event_kind']=viewer.state['entry'].state if viewer.state['entry'] else None
                # Exercise normal opening. A forced seek used to hide an empty
                # segment-start landing in the installed application's acceptance checks.
                assert viewer.frame is not None and not viewer._in_flight,'Initial LMTR observation not published'
            else:
                result['ready_wait_after_test_settle_ms'] = wait(lambda: viewer.observation is not None, 'legacy/LMTX open')
            frame = viewer.frame if lmtr else viewer.observation
            image = viewer.image if lmtr else viewer.image_widget
            assert image.image is not None
            result['checks'].append('document_loaded_without_camera')
            assert lmtr or viewer.worker is None
            # Stored values remain authoritative across every presentation interaction.
            matrix = frame.temperature_c
            if matrix is not None:
                evidence = matrix.tobytes()
                mapping = image._mapping();geometry = mapping['geometry'];transform = mapping['transform']
                x, y = 0, 0
                position = native_to_widget(x,y,image.width(),image.height(),geometry=geometry,transform=transform)
                result['pointer_at_synchronous_probe']=pointer_probe(app,image,position,(x,y))
                point = viewer.point.text() if lmtr else viewer.cursor_label.text()
                result['point_text'] = point
                settle()
                result['pointer_after_event_loop_wait']=image.pointer
                roi = NativeROI(0,0,min(2,geometry.width),1)
                image.set_roi(roi);settle()
                roi_label = viewer.roi_label if lmtr else viewer.roi_values_label
                roi_text = roi_label.text()
                palette = viewer.palette if lmtr else viewer.palette_combo
                automatic = viewer.auto if lmtr else viewer.auto_range_check
                minimum = viewer.minimum if lmtr else viewer.min_spin
                maximum = viewer.maximum if lmtr else viewer.max_spin
                started = time.monotonic()
                palette.setCurrentText('Turbo');automatic.setChecked(False)
                minimum.setValue(10);maximum.setValue(45);settle()
                assert roi_label.text() == roi_text
                window.resize(1020,740);settle()
                result['resized_window_size'] = [window.width(),window.height()]
                assert image.roi == roi and matrix.tobytes() == evidence
                result['presentation_and_resize_with_test_waits_ms'] = (time.monotonic()-started)*1000
                result['roi_text'] = roi_text
                result['checks'].extend(['native_point_mapping','roi_palette_range_resize_source_unchanged'])
                # Use the real save action; replace only the file chooser to avoid external-provider interaction.
                target = output/'rendered é image.png'
                module = 'lmtr_viewer' if lmtr else 'lmthermal_viewer'
                with patch(module+'.QFileDialog.getSaveFileName',return_value=(str(target),'PNG')):
                    (viewer.save_button if lmtr else viewer.render_button).click()
                assert target.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                image.clear_roi();settle();assert image.roi is None
                result['checks'].append('separate_png_and_clear_roi')
            # Exercise the actual metadata dialog, without capturing any other application.
            dialogs=[]
            def inspect_dialog():
                for dialog in app.topLevelWidgets():
                    if isinstance(dialog,QDialog) and dialog.parent() is viewer:
                        text=dialog.findChild(QPlainTextEdit)
                        dialogs.append(bool(text and text.toPlainText()))
                        dialog.accept()
            QTimer.singleShot(60,inspect_dialog)
            if lmtr: viewer.details_button.setChecked(True)
            (viewer.metadata_button).click()
            assert dialogs == [True]
            result['checks'].append('original_metadata_dialog')
            if lmtr:
                viewer.slider.setValue(0);wait(lambda:not viewer._in_flight,'segment/gap navigation')
                assert viewer.frame is None and image.image is None and not viewer.save_button.isEnabled()
                viewer.slider.setValue(1);wait(lambda:viewer.frame is not None,'observation recovery')
                viewer.verify_button.click();wait(lambda:not viewer._in_flight,'full verification')
                result['integrity_status'] = viewer.validation_status.text()
                result['checks'].append('timeline_blank_event_recovery_full_verification')
            elif viewer.playback is not None:
                viewer.timeline_slider.setValue(2)
                wait(lambda: viewer.playback.index==2 and 'sequence 3 |' in viewer.playback_status_label.text(),'legacy gap')
                assert viewer.observation is None and image.image is None
                viewer.timeline_slider.setValue(4)
                wait(lambda: viewer.playback.index==4 and 'sequence 5 |' in viewer.playback_status_label.text(),'legacy drop')
                assert viewer.observation is None
                viewer.timeline_slider.setValue(5)
                wait(lambda:viewer.observation is not None,'legacy recovery')
                result['checks'].append('legacy_gap_drop_recovery')
        settle();window.grab().save(str(output/'window.png'))
        result['source_after']=hashes(path)
        assert before == result['source_after']
        result['status']='passed'
    except Exception as error:
        import traceback
        result.update(status='failed',error=type(error).__name__+': '+str(error),error_traceback=traceback.format_exc())
        window.grab().save(str(output/'failure.png'))
    finally:
        started=time.monotonic();window.close()
        try:
            wait(lambda:not window._documents,'owned document Close')
            result['close_ms']=(time.monotonic()-started)*1000
            result['closed_normally']=not window.isVisible()
        except Exception as error:
            result.update(status='failed',close_error=str(error))
        result['source_after'] = hashes(path)
        if before != result['source_after']:
            result.update(status='failed',integrity_error='Source changed during qualification')
        (output/'window-check.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result
