"""Where the app keeps its data: a working folder holding one folder per project.

Every project has the same subfolders, so recordings, downloads, backups and
reports from different jobs never mix. Folders are created only when something
is saved, never just because a page opened.
"""
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

DEFAULT_PROJECT = 'General'
PROJECT_MARKER = '.urmotionstudio-project'
PROJECT_FOLDERS = {'flights': 'Flight reports', 'programs': 'Robot programs', 'status': 'Robot Status',
                   'backups': 'Backups', 'logs': 'Logs', 'reports': 'Reports', 'checklists': 'Checklists'}
# Earlier versions kept these directly in the app data folder.
LEGACY_FOLDERS = {'flightreports': 'flights', 'Robot programs': 'programs', 'Robot Status': 'status'}
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)), *(f'LPT{n}' for n in range(1, 10))}


def app_data_folder():
    """Hidden per-user data folder used by earlier versions."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return base / 'URMotionStudio'


def documents_folder():
    if sys.platform == 'win32':
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        # CSIDL_PERSONAL follows a Documents folder moved to OneDrive or another drive.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    documents = Path.home() / 'Documents'
    return documents if documents.is_dir() else Path.home()


def default_workspace():
    override = os.environ.get('MOTIONSIM_WORKSPACE')
    return Path(override).expanduser() if override else documents_folder() / 'UR Motion Studio'


def default_library():
    """The flight-report library of the default project."""
    return default_workspace() / DEFAULT_PROJECT / PROJECT_FOLDERS['flights']


def project_folder(app, kind=None):
    """The active project's folder, or one of its standard subfolders (see PROJECT_FOLDERS)."""
    root = getattr(app, 'project_root', None) or default_workspace() / DEFAULT_PROJECT
    return Path(root) if kind is None else Path(root) / PROJECT_FOLDERS[kind]


def start_folder(app, kind, *, create=True):
    """A file dialog's starting folder in the project, or None to let the dialog choose.

    Save dialogs create the subfolder they are about to save into, but only inside a working
    folder that exists (or the default one): an unplugged drive is never recreated elsewhere.
    Open dialogs (create=False) create nothing and start at the deepest existing project folder.
    """
    folder = project_folder(app, kind)
    workspace = folder.parent if kind is None else folder.parent.parent
    try:
        if create and (workspace.is_dir() or workspace == default_workspace()):
            folder.mkdir(parents=True, exist_ok=True)
        for candidate in (folder, folder.parent):
            if candidate.is_dir():
                return str(candidate)
    except (OSError, ValueError):
        pass  # the dialog opens in its usual place; the save itself reports real errors
    return None


def validate_project_name(name):
    name = name.strip() if isinstance(name, str) else ''
    if not name or len(name) > 80:
        raise ValueError('Enter a project name of up to 80 characters.')
    if any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name):
        raise ValueError('A project name cannot contain < > : " / \\ | ? or *.')
    if name.endswith(('.', ' ')) or name in ('.', '..') or name.split('.')[0].upper() in _RESERVED:
        raise ValueError(f'"{name}" cannot be used as a folder name. Choose another name.')
    return name


def is_project(folder):
    folder = Path(folder)
    return folder.is_dir() and ((folder / PROJECT_MARKER).is_file()
                                or any((folder / name).is_dir() for name in PROJECT_FOLDERS.values()))


def list_projects(workspace):
    """Project folders only, so a working folder can also hold unrelated folders."""
    try:
        children = list(Path(workspace).iterdir())
    except OSError:
        return []
    return sorted((child.name for child in children if is_project(child)), key=str.casefold)


def create_project(workspace, name):
    """Create (or adopt an existing plain folder as) a project; names differing only in case are the same."""
    name = validate_project_name(name)
    existing = next((item for item in list_projects(workspace) if item.casefold() == name.casefold()), None)
    if existing is not None:
        raise ValueError(f'Project "{existing}" already exists in this working folder.')
    folder = Path(workspace) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PROJECT_MARKER).write_text('UR Motion Studio project folder\n', encoding='utf-8')
    return folder


def legacy_data(base=None):
    """Earlier-version folders that still hold files, as (folder, kind) pairs."""
    base = Path(base) if base is not None else app_data_folder()
    found = []
    for name, kind in LEGACY_FOLDERS.items():
        folder = base / name
        try:
            if folder.is_dir() and any(folder.iterdir()):
                found.append((folder, kind))
        except OSError:
            continue
    return found


def _move(source, destination):
    """Move one file or folder; across drives it is copied under a temporary name first,
    so an interrupted copy never looks like a finished one."""
    try:
        os.rename(source, destination)  # same drive: instant and all-or-nothing
        return
    except OSError:
        if destination.exists() or not source.exists():
            raise
    partial = destination.with_name(f'.{destination.name}.partial-{uuid4().hex[:8]}')
    try:
        if source.is_dir():
            shutil.copytree(source, partial)
        else:
            shutil.copy2(source, partial)
        os.rename(partial, destination)
    except BaseException:
        if partial.is_dir():
            shutil.rmtree(partial, ignore_errors=True)
        else:
            partial.unlink(missing_ok=True)
        raise
    if source.is_dir():
        shutil.rmtree(source)
    else:
        source.unlink()


def move_legacy_data(project_root, base=None):
    """Move earlier-version data into a project. Never overwrites.

    Returns (moved, skipped, failed) item names; failed holds 'name: reason'.
    """
    moved, skipped, failed = [], [], []
    for folder, kind in legacy_data(base):
        target = Path(project_root) / PROJECT_FOLDERS[kind]
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(folder.iterdir()):
            name, destination = f'{PROJECT_FOLDERS[kind]}/{child.name}', target / child.name
            if destination.exists():
                skipped.append(name)
                continue
            try:
                _move(child, destination)
            except OSError as error:
                failed.append(f'{name}: {error.strerror or error}')
                continue
            moved.append(name)
        try:
            folder.rmdir()  # only succeeds when everything moved
        except OSError:
            pass
    return moved, skipped, failed
