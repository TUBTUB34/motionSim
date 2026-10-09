# Build on Windows: python -m PyInstaller --noconfirm MotionStudio.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
robot_assets = [(str(p), 'assets/robots') for p in (root / 'assets' / 'robots').iterdir()
                if p.suffix in ('.npz', '.json', '.txt', '.md')]
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)],
    binaries=[], datas=robot_assets + collect_data_files('numba'),
    hiddenimports=['matplotlib.backends.backend_tkagg', 'PIL._tkinter_finder', 'PIL._imagingtk'],
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['TkAgg']}},
    runtime_hooks=[], excludes=['pytest', 'IPython'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='URMotionStudio',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False)
