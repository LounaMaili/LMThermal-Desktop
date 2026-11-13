"""Opt-in Linux provider qualification, using real Open/Save dialogs, never a chooser stub.

Only this application's Qt/GTK controls are inspected. GTK's actual Open/Save buttons
are activated after selection; a Qt fallback is reported distinctly, not certified as
a GTK/portal provider. This finite developer path is absent from normal startup.
"""
import ctypes as C
import ctypes.util
import hashlib
import json
from pathlib import Path
import sys
import time

from PyQt6.QtCore import QMetaObject, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog, QLineEdit


def _gtk_controls():
    """Inspect only in-process GTK windows; do not attach to a desktop/foreign process."""
    gtk = C.CDLL(ctypes.util.find_library('gtk-3'))
    gobj = C.CDLL(ctypes.util.find_library('gobject-2.0'))
    glib = C.CDLL(ctypes.util.find_library('glib-2.0'))
    def function(lib, name, result, args):
        value = getattr(lib, name); value.restype = result; value.argtypes = args
        return value
    class List(C.Structure): pass
    List._fields_ = [('data', C.c_void_p), ('next', C.POINTER(List)), ('prev', C.POINTER(List))]
    return dict(
        windows=function(gtk, 'gtk_window_list_toplevels', C.POINTER(List), []),
        visible=function(gtk, 'gtk_widget_get_visible', C.c_int, [C.c_void_p]),
        chooser_type=function(gtk, 'gtk_file_chooser_get_type', C.c_size_t, []),
        is_a=function(gobj, 'g_type_check_instance_is_a', C.c_int, [C.c_void_p, C.c_size_t]),
        filename=function(gtk, 'gtk_file_chooser_set_filename', C.c_int, [C.c_void_p, C.c_char_p]),
        folder=function(gtk, 'gtk_file_chooser_set_current_folder', C.c_int, [C.c_void_p, C.c_char_p]),
        name=function(gtk, 'gtk_file_chooser_set_current_name', None, [C.c_void_p, C.c_char_p]),
        # GTK_RESPONSE_OK is the response of Qt's real GTK3 Open/Save buttons.
        button=function(gtk, 'gtk_dialog_get_widget_for_response', C.c_void_p, [C.c_void_p, C.c_int]),
        label=function(gtk, 'gtk_button_get_label', C.c_char_p, [C.c_void_p]),
        activate=function(gtk, 'gtk_widget_activate', C.c_int, [C.c_void_p]),
        free=function(glib, 'g_list_free', None, [C.c_void_p]))


def run(app, workspace, source, output):
    """Open through Home's ordinary chooser, save a distinct PNG, compare pixels and Close.

    Immutable source input and a fresh private destination are explicit CLI arguments.
    No USB discovery, connection, controls, clipboard or other app window is touched.
    """
    if not sys.platform.startswith('linux'): raise ValueError('Linux provider probe only; Windows needs its native checks')
    source = Path(source).resolve(); output = Path(output).resolve()
    if not source.is_file() or output.exists(): raise ValueError('Existing source and NEW report directory required')
    output.mkdir(mode=0o700, parents=True)
    report = dict(status='running', platform=app.platformName(), selector_replaced=False,
                  operator_acceptance=False, camera_opened=False, actions=[])
    original = hashlib.sha256(source.read_bytes()).hexdigest()

    def wait(condition, label):
        """Bound own-process GUI observation; exposure is required, never inferred offscreen."""
        deadline = time.monotonic()+15
        while not condition() and time.monotonic()<deadline: QTest.qWait(20)
        if not condition(): raise TimeoutError(label)

    def choose(action, path, save=False):
        """Drive the real in-process selector without replacing its return value/provider."""
        row = dict(operation='save' if save else 'open'); report['actions'].append(row)
        started = time.monotonic(); selected = None; failures = []
        timer = QTimer(); timer.setInterval(100)
        def reject():
            # Native helpers have a Qt owner even when that proxy itself is hidden.
            for dialog in app.topLevelWidgets():
                if isinstance(dialog, QFileDialog): QMetaObject.invokeMethod(dialog, 'reject', Qt.ConnectionType.QueuedConnection)
        def operate():
            nonlocal selected
            try:
                head = gtk['windows'](); at = head
                try:
                    while at:
                        pointer = at.contents.data
                        if gtk['visible'](pointer) and gtk['is_a'](pointer, gtk['chooser_type']()):
                            if selected is None:
                                row['provider'] = 'GTK3 native file chooser'
                                if save:
                                    assert gtk['folder'](pointer, str(path.parent).encode())
                                    gtk['name'](pointer, path.name.encode())
                                else: assert gtk['filename'](pointer, str(path).encode())
                                selected = time.monotonic()
                            elif time.monotonic()-selected>.8:
                                button = gtk['button'](pointer, -5); assert button
                                row['activated_button'] = gtk['label'](button).decode()
                                timer.stop(); assert gtk['activate'](button)
                            return
                        at = at.contents.next
                finally:
                    if head: gtk['free'](head)
                for dialog in app.topLevelWidgets():
                    if isinstance(dialog, QFileDialog) and dialog.isVisible():
                        row['provider'] = 'Qt fallback file chooser (not GTK/portal)'
                        assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
                        edit = dialog.findChild(QLineEdit, 'fileNameEdit'); assert edit
                        edit.setText(str(path)); edit.setFocus()
                        timer.stop(); QTest.keyClick(edit, Qt.Key.Key_Return)
                        return
                if time.monotonic()-started>12: raise TimeoutError('No supported in-process selector; portal/operator check still pending')
            except BaseException as error:
                failures.append(repr(error)); timer.stop(); reject()
        timer.timeout.connect(operate); timer.start()
        try: action()
        finally: timer.stop()
        row['elapsed_s'] = time.monotonic()-started
        if failures: raise RuntimeError(failures[0])
        if 'provider' not in row: raise RuntimeError('No actual dialog observed')

    try:
        gtk = _gtk_controls()
        if not QTest.qWaitForWindowExposed(workspace, 5000): raise RuntimeError('Window not exposed')
        workspace.resize(1000, 740)
        choose(workspace.choose_lmtr if source.suffix.lower()=='.lmtr' else workspace.choose_still, source)
        viewer = workspace.tabs.currentWidget()
        lmtr = source.suffix.lower()=='.lmtr'
        wait(lambda: (viewer.frame is not None and not viewer._in_flight) if lmtr else viewer.offline_capture is not None,
             'Chooser did not produce a usable observation')
        image = viewer.image if lmtr else viewer.image_widget
        frame = viewer.frame if lmtr else viewer.offline_capture
        expected = frame.render(image.palette, image.effective_bounds)
        target = output/'normal dialog export é.png'
        choose((viewer.save_button if lmtr else viewer.render_button).click, target, save=True)
        from PIL import Image
        import numpy as np
        with Image.open(target) as png: np.testing.assert_array_equal(expected, np.asarray(png.convert('RGB')))
        assert hashlib.sha256(source.read_bytes()).hexdigest()==original
        workspace.grab().save(str(output/'own-window.png'))
        workspace.close(); wait(lambda:not workspace._documents, 'Cooperative Close')
        report.update(status='passed', source_sha256=original, source_unchanged=True,
                      PNG_pixels_match=True, closed_normally=True, window_exposed=True)
        return report
    except BaseException as error:
        report.update(status='failed', error=repr(error)); workspace.close()
        try: wait(lambda:not workspace._documents, 'Failed probe cooperative cleanup')
        except Exception as cleanup: report['cleanup_error'] = repr(cleanup)
        raise
    finally:
        (output/'native-dialog-check.json').write_text(json.dumps(report, indent=2)+'\n')
