#!/usr/bin/env python3
"""LMThermal Desktop workspace: explicit live connection and owned camera-free documents."""
import argparse
import json
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel,
    QPushButton, QFileDialog, QTabWidget, QMessageBox)
from lmthermal_viewer import MainWindow
from lmtr_viewer import LmtrWindow, WARNING

MAX_DOCUMENTS = 4  # Each reader keeps its existing local limits; this is not a total-RSS guarantee.


class WorkspaceLegacyWindow(MainWindow):
    """Reuse legacy/live engines; keep the tab owner alive through cooperative worker shutdown."""
    closed = pyqtSignal()
    close_refused = pyqtSignal()
    def __init__(self):
        super().__init__(auto_connect=False)
        self.setWindowTitle('LMThermal — capture / legacy recording / live camera')
        self._workspace_closing = False
        self._workspace_waiting = set()
    def _on_frame_available(self):
        """A queued callback during cooperative Close must not republish a live reading."""
        if not self._workspace_closing:
            super()._on_frame_available()
    def use_offline_controls(self):
        """Saved-document tabs omit live-only actions; Home still owns explicit Live.

        This is presentation only: it never opens/stops a camera or changes any
        loader, recorder, measurement or finalization decision.
        """
        for control in (self.connect_button, self.initialize_button, self.capture_button,
                        self.start_log_button.parentWidget(), self.start_record_button.parentWidget()):
            control.hide()
        session = self.device_label.parentWidget(); session.setTitle('Saved source')
        session.layout().labelForField(self.fps_label).hide(); self.fps_label.hide()
    def closeEvent(self, event):
        # MainWindow's standalone close waits synchronously. The workspace requests
        # worker cancellation first and lets finished signals retry Close without a GUI wait.
        if not self._workspace_closing:
            self._offline_token += 1;self._playback_token += 1
            self.observation=None;self.image_widget.set_observation(None)
            self._sync_legend();self._clear_measurements()
        self._workspace_closing = True
        self._playback_timer.stop()
        pending = False
        for worker in (self.worker, self.offline_loader, self.playback_worker):
            if worker is not None and worker.isRunning():
                pending = True
                if worker not in self._workspace_waiting:
                    self._workspace_waiting.add(worker)
                    worker.finished.connect(self.close)
                    worker.request_stop()
        if pending:
            self.setEnabled(False);event.ignore();return
        super().closeEvent(event)
        self._workspace_waiting.clear()
        if event.isAccepted():
            self.closed.emit()
        else:
            # A legacy recorder/logger may refuse finalization. Keep its owner and
            # explicit retry controls usable instead of trapping a disabled tab.
            self._workspace_closing = False
            self.setEnabled(True)
            self.close_refused.emit()


class WorkspaceLmtrWindow(LmtrWindow):
    """LMTR retains its reviewed generation/cancellation/verification behavior unchanged."""
    closed = pyqtSignal()
    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted(): self.closed.emit()


class DesktopWorkspace(QMainWindow):
    """A bounded set of reusable document views, with exactly one explicit live-camera tab.

    Opening this shell never opens a camera. File engines run in their existing workers;
    a tab is deleted only after its viewer accepts Close and releases its handles.
    Closing the application waits cooperatively; no forced QThread termination/repair.
    """
    def __init__(self, *, legacy_factory=WorkspaceLegacyWindow, lmtr_factory=WorkspaceLmtrWindow):
        super().__init__()
        self.setWindowTitle('LMThermal Desktop');self.resize(1280, 850)
        self._legacy_factory=legacy_factory;self._lmtr_factory=lmtr_factory
        self._documents={};self._live=None;self._closing=False
        self.tabs=QTabWidget();self.tabs.setDocumentMode(True);self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_document);self.setCentralWidget(self.tabs)
        home=QWidget();layout=QVBoxLayout(home)
        title=QLabel('LMThermal Desktop');title.setStyleSheet('font-size: 26px; font-weight: 600');layout.addWidget(title)
        text=QLabel('Inspect thermal captures and recordings, or explicitly connect a supported camera.\n'
                    'Stored measurements remain authoritative. Palette and ROI edits affect analysis/presentation only.')
        text.setWordWrap(True);layout.addWidget(text)
        actions=[('Live camera — Linux backend',self.open_live),('Open LMTX / legacy still',self.choose_still),
                 ('Open LMTR recording — read-only',self.choose_lmtr),('Open legacy .lmthermal recording',self.choose_legacy)]
        self.home_buttons=[]
        for label,callback in actions:
            button=QPushButton(label);button.setMinimumHeight(42);button.clicked.connect(callback)
            layout.addWidget(button);self.home_buttons.append(button)
        note=QLabel('LMTR recording is not enabled. Existing direct capture/legacy recording uses its established backend.\n'
                    'Windows/macOS offline support does not establish direct camera acquisition.');note.setWordWrap(True);layout.addWidget(note)
        warning=QLabel(WARNING);warning.setWordWrap(True);layout.addWidget(warning);layout.addStretch()
        self.home_buttons[0].setEnabled(sys.platform.startswith('linux'))
        self.home_buttons[0].setToolTip('Direct acquisition currently requires the Linux V4L2 backend')
        self.tabs.addTab(home,'Home');self.tabs.tabBar().setTabButton(0,self.tabs.tabBar().ButtonPosition.RightSide,None)
        menu=self.menuBar().addMenu('&File')
        for label,callback in actions:
            action=QAction(label,self);action.triggered.connect(callback);menu.addAction(action)
        close=QAction('Close document',self);close.setShortcut(QKeySequence.StandardKey.Close)
        close.triggered.connect(lambda:self.close_document(self.tabs.currentIndex()));menu.addAction(close)
        quit_action=QAction('Quit',self);quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close);menu.addAction(quit_action)
        help_menu=self.menuBar().addMenu('&Help')
        diagnostics=QAction('Local diagnostics / About',self);diagnostics.triggered.connect(self.show_diagnostics)
        help_menu.addAction(diagnostics)
        self.statusBar().showMessage('LMTR read-only · source-native coordinates · native-equivalent temperatures')
    def show_diagnostics(self):
        """Explicit local information only; no telemetry, scene bytes or automatic sharing."""
        import logging
        path=getattr(logging.getLogger('lmthermal'),'diagnostic_path',None)
        QMessageBox.information(self,'LMThermal Desktop',
            'Development workspace · Linux live backend · camera-free LMTX/LMTR/legacy analysis\n'
            +WARNING+'\nLocal log: '+str(path or 'stderr (no writable local log)')
            +'\nLogs may include file paths; review before sharing. No automatic upload.')

    def _admit(self):
        if self._closing:return False
        if len(self._documents)>=MAX_DOCUMENTS:
            QMessageBox.information(self,'Document limit',f'Close a document first (maximum {MAX_DOCUMENTS}).');return False
        return True
    def _add(self, viewer, title):
        viewer.setWindowFlags(Qt.WindowType.Widget)
        self._documents[viewer]=title
        viewer.closed.connect(lambda:self._document_closed(viewer))
        if hasattr(viewer, 'close_refused'):
            viewer.close_refused.connect(self._abort_close)
        index=self.tabs.addTab(viewer,title);self.tabs.setCurrentIndex(index)
        return viewer
    def open_live(self):
        """Create the aiming workspace; Connect remains an explicit action inside it."""
        if self._live is not None:
            self.tabs.setCurrentWidget(self._live);return self._live
        if not self._admit():return None
        viewer=self._legacy_factory();self._live=viewer
        return self._add(viewer,'Live camera')
    def open_path(self,path,*,recover=False,legacy_recording=False):
        """Route formats without converting them, changing source bytes or initializing hardware."""
        path=Path(path).absolute()
        if not self._admit():return None
        if path.suffix.lower()=='.lmtr' and not legacy_recording:
            viewer=self._lmtr_factory();viewer.recover.setChecked(recover)
            self._add(viewer,path.name+' · LMTR');viewer.open_path(path)
        else:
            viewer=self._legacy_factory()
            if isinstance(viewer, WorkspaceLegacyWindow): viewer.use_offline_controls()
            self._add(viewer,path.name)
            if legacy_recording:viewer.open_recording(path)
            elif path.suffix.lower()=='.lmtx':viewer.open_lmtx(path)
            else:viewer.open_capture(path)
        return viewer
    def choose_still(self):
        path,_=QFileDialog.getOpenFileName(self,'Open thermal still','','Thermal capture (*.lmtx *.json *.npz);;All files (*)')
        if path:self.open_path(path)
    def choose_lmtr(self):
        path,_=QFileDialog.getOpenFileName(self,'Open verified LMTR source','','LMTR (*.lmtr)')
        if path:self.open_path(path)
    def choose_legacy(self):
        path=QFileDialog.getExistingDirectory(self,'Open legacy .lmthermal directory')
        if path:self.open_path(path,legacy_recording=True)
    def close_document(self,index):
        if index>0:self.tabs.widget(index).close()
    def _document_closed(self,viewer):
        # Defer deletion until the viewer's Close stack has returned.
        QTimer.singleShot(0,lambda:self._release(viewer))
    def _release(self,viewer):
        if viewer not in self._documents:return
        self.tabs.removeTab(self.tabs.indexOf(viewer));del self._documents[viewer]
        if self._live is viewer:self._live=None
        viewer.deleteLater()
        if self._closing and not self._documents:self.close()
    def _abort_close(self):
        """Refusal preserves document ownership and lets the user inspect/retry; never discard it."""
        self._closing = False
    def closeEvent(self,event):
        self._closing=True
        for viewer in list(self._documents):viewer.close()
        if self._documents:event.ignore()
        else:event.accept()


def self_check():
    """Validate bundled runtime/schema without USB, GUI display, input files or network."""
    import numpy as np
    import cv2
    from PIL import Image
    from lmtr_reference.metadata import SHAPES
    from celsius_palette import CelsiusRange,render_temperature
    matrix=np.full((288,384),25,dtype='<f4');matrix[0,0]=20;matrix[0,1]=30;original=matrix.tobytes()
    rgb=render_temperature(matrix,0,40,'Inferno')
    assert rgb.shape==(288,384,3) and matrix.tobytes()==original
    assert SHAPES and Image.new('RGB',(1,1)).size==(1,1)
    return dict(status='passed',numpy=np.__version__,opencv=cv2.__version__,schema_resource='loaded',
                frozen=bool(getattr(sys,'frozen',False)),camera_opened=False)


def main():
    """Product entry point; legacy diagnostic entry points remain supported."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open',type=Path);parser.add_argument('--legacy-recording',action='store_true')
    parser.add_argument('--recover-lmtr',action='store_true');parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--verify-lmtr',type=Path)
    parser.add_argument('--window-check-output',type=Path,
        help='Opt-in camera-free developer check on --open; creates a new private report directory')
    parser.add_argument('--expect-window-error',action='store_true')
    parser.add_argument('--native-dialog-check-output',type=Path,
        help='Opt-in Linux own-process actual Open/PNG provider check; needs --open and NEW private output')
    args=parser.parse_args()
    if args.native_dialog_check_output:
        if not sys.platform.startswith('linux') or args.open is None or not args.open.is_file() or args.legacy_recording or args.recover_lmtr or args.window_check_output or args.self_check or args.verify_lmtr:
            parser.error('--native-dialog-check-output requires Linux and only an explicit offline --open file')
        if args.native_dialog_check_output.exists() or args.native_dialog_check_output.resolve().is_relative_to(Path(__file__).resolve().parent):
            parser.error('Native dialog report directory must be NEW and outside the application checkout')
    if args.expect_window_error and not args.window_check_output:
        parser.error('--expect-window-error requires --window-check-output')
    if args.window_check_output and (args.open is None or args.self_check or args.verify_lmtr):
        parser.error('--window-check-output requires only an explicit offline --open source')
    if args.window_check_output:
        if not args.open.is_file() and not (args.legacy_recording and args.open.is_dir()):
            parser.error('Window check requires an existing offline file or legacy recording directory')
        if args.window_check_output.exists():
            parser.error('Window check report directory must be new')
        if args.open.is_dir() and args.window_check_output.resolve().is_relative_to(args.open.resolve()):
            parser.error('Window check output must be outside the immutable source')

    from desktop_runtime import configure_logging
    configure_logging()
    if args.self_check:print(json.dumps(self_check()));return
    if args.verify_lmtr:
        from lmtr_reader import LmtrPlaybackModel
        model=LmtrPlaybackModel(args.verify_lmtr,recover=args.recover_lmtr)
        try:print(json.dumps(model.verify_full()))
        finally:model.close()
        return
    app=QApplication(sys.argv);app.setApplicationName('LMThermal Desktop');app.setOrganizationName('LounaMaili')
    app.setStyle('Fusion');window=DesktopWorkspace()
    if args.open and not args.native_dialog_check_output:window.open_path(args.open,recover=args.recover_lmtr,legacy_recording=args.legacy_recording)
    window.show()
    if args.native_dialog_check_output:
        from desktop_native_dialog_check import run as check_native_dialogs
        app.setQuitOnLastWindowClosed(False)
        def native_dialog_check():
            exit_code=1
            try:
                result=check_native_dialogs(app,window,args.open,args.native_dialog_check_output)
                print(json.dumps({'status':result['status'],'actions':result['actions']}));exit_code=0
            except Exception as error:
                print(json.dumps({'status':'failed','error':repr(error)}))
            finally:app.exit(exit_code)
        QTimer.singleShot(200,native_dialog_check)
        sys.exit(app.exec())
    if args.window_check_output:
        from desktop_window_check import run
        # Native window mapping/exposure requires a running top-level event loop.
        # This bounded developer path never changes normal startup or opens a camera.
        app.setQuitOnLastWindowClosed(False)
        def check_window():
            exit_code=1
            try:
                report=run(app,window,args.open,args.window_check_output,expect_error=args.expect_window_error)
                print(json.dumps({'status':report['status'],'platform':report['platform']}))
                exit_code=0 if report['status']=='passed' else 1
            finally:
                app.exit(exit_code)
        QTimer.singleShot(200,check_window)
        sys.exit(app.exec())
    sys.exit(app.exec())


if __name__=='__main__':main()
