# Build on Windows: python -m PyInstaller --noconfirm MotionStudio.spec
from pathlib import Path
import re
import sys
from zipfile import ZipFile
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.config import CONF
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                   VarFileInfo, VarStruct, VSVersionInfo)

root = Path(SPECPATH)
if not (root / 'motionstudio' / 'app' / 'main_window.py').is_file():
    raise RuntimeError('Missing the motionstudio package next to MotionStudio.spec.')
# The built file is ArmChair-v<version>.exe; the app itself is still UR Motion Studio.
version = re.search(r"__version__ = '([0-9.]+)'", (root / 'motionstudio' / '__init__.py').read_text()).group(1)
name = f'ArmChair-v{version}'
numbers = (*map(int, version.split('.')), 0)
file_version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[StringFileInfo([StringTable('040904B0', [
              StringStruct('ProductName', 'UR Motion Studio'), StringStruct('FileDescription', 'UR Motion Studio'),
              StringStruct('ProductVersion', version), StringStruct('FileVersion', version),
              StringStruct('InternalName', 'ArmChair'), StringStruct('OriginalFilename', f'{name}.exe')])]),
          VarFileInfo([VarStruct('Translation', [1033, 1200])])])
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
                    source = Path(CONF['workpath']) / 'tk-runtime' / relative
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_bytes(bundle.read(member))
                    tk_data.append((str(source), str(Path(destination) / Path(*relative.parts[1:]).parent)))
robot_assets = [(str(p), 'assets/robots') for p in (root / 'assets' / 'robots').iterdir()
                if p.suffix in ('.npz', '.json', '.txt', '.md')]
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)],
    binaries=[], datas=robot_assets + tk_data + collect_data_files('numba'),
    hiddenimports=['matplotlib.backends.backend_tkagg', 'matplotlib.backends.backend_pdf',
                   'PIL._tkinter_finder', 'PIL._imagingtk', *collect_submodules('asyncssh'),
                   # Pages are imported when first opened; collect every module explicitly.
                   *collect_submodules('motionstudio')],
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['TkAgg']}},
    runtime_hooks=[], excludes=['pytest', 'IPython', 'trimesh', 'collada', 'fast_simplification', 'scipy', 'yaml'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, version=file_version,
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False)
