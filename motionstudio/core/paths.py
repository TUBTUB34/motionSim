"""Where the app keeps its data on this computer."""
import os
from pathlib import Path
import sys


def default_library():
    """The flight-report library; its parent is the app's data folder."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return base / 'URMotionStudio' / 'flightreports'
