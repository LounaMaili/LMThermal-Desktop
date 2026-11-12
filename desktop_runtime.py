"""Local bounded diagnostics for the product shell; never capture scene data or upload logs."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def configure_logging():
    """Use per-user state, no administrator/system configuration and no CWD capture output.

    Logs may contain exception paths. They stay local; users review them before sharing.
    A read-only home must not prevent offline inspection: stderr is the fallback.
    """
    if sys.platform=='win32':root=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'LMThermal Desktop'
    elif sys.platform=='darwin':root=Path.home()/'Library/Logs/LMThermal Desktop'
    else:root=Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state'))/'lmthermal-desktop'
    if not root.is_absolute():root=Path.home()/'.local/state/lmthermal-desktop'
    logger=logging.getLogger('lmthermal');logger.setLevel(logging.INFO)
    if logger.handlers:return getattr(logger,'diagnostic_path',None)
    target=None
    try:
        folder=root/'logs';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        target=folder/'application.log'
        handler=RotatingFileHandler(target,maxBytes=524288,backupCount=2,encoding='utf-8')
    except OSError:handler=logging.StreamHandler();target=None
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logger.addHandler(handler)
    logger.diagnostic_path=target
    previous=sys.excepthook
    def report(kind,value,traceback):
        logger.error('Unhandled application error',exc_info=(kind,value,traceback))
        previous(kind,value,traceback)
    sys.excepthook=report
    return target
