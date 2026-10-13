# Build on Windows: python -m PyInstaller --noconfirm MotionStudio.spec
from pathlib import Path
import sys
from zipfile import ZipFile
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
for module in ('log_parser.py', 'log_data.py', 'log_view.py'):
    if not (root / module).is_file():
        raise RuntimeError(f'Missing desktop log module: {module}')
# Python 3.14 on Windows ships Tcl/Tk scripts in ZIPs, rather than the
# directories collected by the standard hook. Supply the runtime directories.
tk_data = []
for library, destination in (('tcl', '_tcl_data'), ('tk', '_tk_data')):
    for archive in (Path(sys.base_prefix) / 'tcl').glob(f'lib{library}*.zip'):
        with ZipFile(archive) as bundle:
            for member in bundle.namelist():
                relative = Path(member)
                if (not member.endswith('/') and relative.parts[0] == f'{library}_library'
                        and '..' not in relative.parts and not relative.is_absolute()):
                    source = root / 'build' / 'tk-runtime' / relative
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_bytes(bundle.read(member))
                    tk_data.append((str(source), str(Path(destination) / Path(*relative.parts[1:]).parent)))
robot_assets = [(str(p), 'assets/robots') for p in (root / 'assets' / 'robots').iterdir()
                if p.suffix in ('.npz', '.json', '.txt', '.md')]
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)],
    binaries=[], datas=robot_assets + tk_data + collect_data_files('numba'),
    hiddenimports=['matplotlib.backends.backend_tkagg', 'PIL._tkinter_finder', 'PIL._imagingtk'],
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['TkAgg']}},
    runtime_hooks=[], excludes=['pytest', 'IPython', 'trimesh', 'collada', 'fast_simplification', 'scipy', 'yaml'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='URMotionStudio',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False)
