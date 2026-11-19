# A native one-directory build: explicit data resources, no source scenes or tests.
from pathlib import Path
import sys
root = Path(SPECPATH).parent
analysis = Analysis([str(root / 'lmthermal_desktop.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'lmtr_reference/schema.json'), 'lmtr_reference')],
    hiddenimports=['mvp_camera_worker'] if sys.platform.startswith('linux') else [],
    hookspath=[], runtime_hooks=[], excludes=['tkinter', 'PyQt5', 'PySide6'], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='LMThermal-Desktop',
    debug=False, strip=False, upx=False, console=True)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False,
    upx=False, name='LMThermal-Desktop')
