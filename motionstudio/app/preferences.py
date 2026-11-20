"""Small local preferences store: saved robot addresses, appearance and the working folder."""
from ipaddress import ip_address
import json
import os
from pathlib import Path
import sys
import tempfile

from motionstudio.core.paths import validate_project_name


MAX_ADDRESSES = 50
MAX_FILE_BYTES = 64 * 1024
THEMES = ('dark', 'light')
TEXT_SCALES = (90, 100, 115, 130, 150)  # percent of the designed text size


def default_preferences_path():
    """Keep settings in the current user's platform configuration directory."""
    override = os.environ.get('MOTIONSIM_PREFERENCES_PATH')
    if override:
        return Path(override).expanduser()
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        configured = os.environ.get('XDG_CONFIG_HOME', '')
        base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / '.config'
    return base / 'URMotionStudio' / 'preferences.json'


def normalize_address(value):
    """Canonicalize IPv4/IPv6 without storing hostnames, URLs or credentials."""
    if not isinstance(value, str):
        raise ValueError('Enter a valid robot IP address, for example 192.168.0.10.')
    value = value.strip()
    try:
        if len(value) > 128 or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError()
        return str(ip_address(value))
    except ValueError:
        raise ValueError('Enter a valid robot IP address, for example 192.168.0.10.') from None


class Preferences:
    """Read once; persist explicit changes before publishing them in memory.

    Address order is stable. A full list requires an explicit removal rather
    than silently forgetting a saved robot. Credentials are never part of state.
    """
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_preferences_path()
        self._state = {'theme': 'dark', 'addresses': (), 'workspace': None, 'project': None, 'text_scale': 100}
        self.load_error = ''
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError('settings file exceeds 64 KiB')
            value = json.loads(raw)
            if (not isinstance(value, dict) or type(value.get('version')) is not int
                    or value['version'] != 1):
                raise ValueError('unsupported settings format')
            theme = value.get('theme', 'dark')
            addresses = value.get('addresses', [])
            if theme not in THEMES or not isinstance(addresses, list):
                raise ValueError('invalid display theme or address list')
            normalized = tuple(dict.fromkeys(normalize_address(address) for address in addresses))
            if len(normalized) > MAX_ADDRESSES:
                raise ValueError(f'more than {MAX_ADDRESSES} saved addresses')
            self._state.update(theme=theme, addresses=normalized)
            # Optional later fields: an unusable one falls back alone and never costs the saved robots.
            workspace = value.get('workspace')
            if isinstance(workspace, str) and '\x00' not in workspace and Path(workspace).is_absolute():
                self._state['workspace'] = workspace
            try:
                self._state['project'] = validate_project_name(value.get('project'))
            except ValueError:
                pass
            if value.get('text_scale') in TEXT_SCALES and type(value.get('text_scale')) is int:
                self._state['text_scale'] = value['text_scale']
        except FileNotFoundError:
            pass
        except (OSError, ValueError, UnicodeError, RecursionError) as error:
            self.load_error = (f'Could not read saved preferences at {self.path}: {error}. '
                               'Using default settings. Save an address or choose a theme to create new preferences.')

    @property
    def addresses(self):
        return self._state['addresses']

    @property
    def theme(self):
        return self._state['theme']

    @property
    def workspace(self):
        """The chosen working folder, or None for the default."""
        return Path(self._state['workspace']) if self._state['workspace'] else None

    @property
    def project(self):
        return self._state['project']

    @property
    def text_scale(self):
        return self._state['text_scale']

    def add_address(self, address):
        address = normalize_address(address)
        addresses = self.addresses
        if address not in addresses:
            if len(addresses) >= MAX_ADDRESSES:
                raise ValueError(f'You have {MAX_ADDRESSES} saved addresses. Remove one before adding another.')
            addresses += (address,)
        self._commit(addresses=addresses)
        return address

    def remove_address(self, address):
        address = normalize_address(address)
        if address not in self.addresses:
            return False
        self._commit(addresses=tuple(item for item in self.addresses if item != address))
        return True

    def set_theme(self, theme):
        if theme not in THEMES:
            raise ValueError('Choose dark or light mode.')
        self._commit(theme=theme)

    def set_text_scale(self, scale):
        if scale not in TEXT_SCALES:
            raise ValueError('Choose one of the listed text sizes.')
        self._commit(text_scale=scale)

    def set_project(self, workspace, project):
        """Save the working folder and the project inside it together."""
        workspace = Path(workspace)
        if not workspace.is_absolute():
            raise ValueError('Choose a working folder with a full path.')
        self._commit(workspace=str(workspace), project=validate_project_name(project))

    def _commit(self, **changes):
        state = {**self._state, **changes}
        # This fixed projection cannot accidentally serialize credentials or
        # unrelated connection settings attached by a caller.
        text = json.dumps({'version': 1, 'theme': state['theme'], 'addresses': list(state['addresses']),
                           'workspace': state['workspace'], 'project': state['project'],
                           'text_scale': state['text_scale']}, indent=2) + '\n'
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                    dir=self.path.parent, prefix='.preferences-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise OSError(f'Could not save preferences at {self.path}. Check folder permissions and available disk space. '
                          'Your previous saved settings are unchanged.') from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        state['addresses'] = tuple(state['addresses'])
        self._state = state
        self.load_error = ''
