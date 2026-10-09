"""Replace one explicitly approved robot identity in the user's SSH trust file."""
import os
from pathlib import Path
import tempfile

import paramiko


def known_hosts_path():
    # Native Windows uses the Windows profile; WSL uses the Linux home directory.
    return Path.home() / '.ssh' / 'known_hosts'


def replace_host_key(path, hostname, key):
    path = Path(path).resolve()
    original = path.read_bytes().decode('utf-8', errors='surrogateescape') if path.exists() else ''
    lines = []
    for line in original.splitlines(keepends=True):
        fields = line.split()
        if not fields or line.lstrip().startswith(('#', '@')):
            lines.append(line)
            continue
        remaining = []
        for name in fields[0].split(','):
            matches = name == hostname
            if name.startswith('|1|'):
                try:
                    matches = paramiko.HostKeys.hash_host(hostname, name) == name
                except (ValueError, TypeError):
                    pass
            if not matches:
                remaining.append(name)
        if remaining:
            lines.append(line.replace(fields[0], ','.join(remaining), 1))
    text = ''.join(lines)
    if text and not text.endswith('\n'):
        text += '\n'
    text += f'{hostname} {key.get_name()} {key.get_base64()}\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text.encode('utf-8', errors='surrogateescape'))
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
