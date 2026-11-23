# Build from the repository root with PyInstaller and an isolated output directory.
from pathlib import Path
import runpy

root = Path(SPECPATH).resolve().parent
controls = runpy.run_path(str(root / 'packaging' / 'verify_playtest.py'))
datas = [(str(path), str(path.relative_to(root).parent))
         for path in controls['resource_files'](root)]

a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=[],
             datas=datas, hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='AlibAi-Playtest',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          upx_exclude=[], runtime_tmpdir=None, console=False,
          disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None)
