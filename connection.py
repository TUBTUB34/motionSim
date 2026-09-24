"""Validated startup settings; credentials are held only in memory."""
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import PurePosixPath
from kinematics import MODELS

@dataclass(frozen=True)
class ConnectionSettings:
    ip: str
    username: str
    password: str = field(repr=False)
    installation: str = "default"
    directory: str = "/programs"
    model: str = "UR15"

    @property
    def can_load_payload(self):
        return bool(self.username and self.password)

    @property
    def remote_path(self):
        return str(PurePosixPath(self.directory) / (self.installation + ".installation"))

def make_settings(ip, username, password, installation, directory="/programs", model="UR15"):
    ip = ip.strip()
    try:
        ip_address(ip)
    except ValueError:
        raise ValueError("Enter a valid robot IP address, for example 192.168.0.10.") from None
    installation = installation.strip() or "default"
    if installation.endswith(".installation"):
        installation = installation[:-len(".installation")]
    if not installation or installation in (".", "..") or any(c in installation for c in "/\\\x00\r\n"):
        raise ValueError("Enter a filename; put its folder in Installation folder.")
    directory = directory.strip() or "/programs"
    if not directory.startswith("/") or any(c in directory for c in "\x00\r\n"):
        raise ValueError("Installation folder must be an absolute robot path, such as /programs.")
    if model not in MODELS:
        raise ValueError("Select a supported robot model.")
    return ConnectionSettings(ip, username.strip(), password, installation, directory, model)
