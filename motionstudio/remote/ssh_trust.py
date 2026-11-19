"""Robot SSH trust: the shared host-key prompt, and saving approved identities to the trust file."""
import base64
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import tempfile
import threading

import paramiko


def known_hosts_path():
    # Native Windows uses the Windows profile; WSL uses the Linux home directory.
    return Path.home() / '.ssh' / 'known_hosts'


def host_key_prompt(question, context=None):
    """(title, message) for a HostKeyQuestion; accepted keys are saved for later connections."""
    if question.replacement:
        return ('Replace saved robot SSH key?', f'{question.fingerprint}\n\n'
                'Remove the old key and save the new one? You will not be asked again for this robot.')
    robot = f'{context}: {question.hostname}' if context else f'Robot: {question.hostname}'
    return ('Verify robot SSH host', f'{robot}\n\n{question.fingerprint}\n\n'
            'Verify this fingerprint with your robot administrator. Trust this robot? '
            f'Its key is saved in {known_hosts_path()}, so you will not be asked again '
            'unless the robot\'s key changes.')


def replace_host_key(path, hostname, key):
    if not isinstance(key, paramiko.PKey):
        raise TypeError('Only a verified SSH public key can be saved to the trust file.')
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


@dataclass
class HostKeyQuestion:
    hostname: str
    fingerprint: str
    answered: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False
    replacement: bool = False


def host_fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class ConfirmHostKey(paramiko.MissingHostKeyPolicy):
    """Ask once per new robot; ``save`` persists the approved key after a successful connect."""
    def __init__(self, confirm, trust_path=None):
        self.confirm = confirm
        self.trust_path = trust_path
        self.accepted = None

    def missing_host_key(self, client, hostname, key):
        fingerprint = host_fingerprint(key)
        if not self.confirm(hostname, fingerprint):
            raise paramiko.SSHException("SSH host key was not accepted.")
        client.get_host_keys().add(hostname, key.get_name(), key)
        self.accepted = (hostname, key)

    def save(self):
        """Remember the approved robot identity so later connections do not ask again.

Credentials are never stored. A changed key is still reported on every
connection until the user approves the replacement.
"""
        if self.accepted is None or self.trust_path is None:
            return
        try:
            replace_host_key(self.trust_path, *self.accepted)
        except OSError as error:
            raise OSError(f"Could not save the robot SSH key to {self.trust_path}: {error}") from None
        self.accepted = None
