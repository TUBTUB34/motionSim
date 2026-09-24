"""Decode supported PolyScope XML/.installation gzip files without executing content."""
from dataclasses import dataclass
import gzip
import io
import math
import re
from defusedxml import ElementTree as ET

MAX_FILE_SIZE = 16 * 1024 * 1024

@dataclass(frozen=True)
class InstallationData:
    tcp: tuple | None = None
    tcp_name: str = ""
    payload_mass: float | None = None
    payload_cog: tuple | None = None
    mounting: tuple | None = None  # baseAngle, tiltAngle (radians)
    warnings: tuple = ()

def tag(element):
    return element.tag.rsplit("}", 1)[-1].rsplit(".", 1)[-1].removesuffix("Impl").lower()

def numbers(text, count):
    if text is None:
        raise ValueError("Value is missing.")
    values = tuple(float(x) for x in re.split(r"[,;\s]+", text.strip().removeprefix("p").strip("[]() ")) if x)
    if len(values) != count or not all(math.isfinite(x) for x in values):
        raise ValueError(f"Expected {count} finite numbers.")
    return values

def value(node, *names):
    for name in names:
        if name in node.attrib:
            return node.attrib[name]
        for child in node:
            if tag(child) == name.lower():
                return child.get("value", child.text)
    return None

def selected_node(container, candidates, selectors):
    selected = value(container, *selectors)
    if selected:
        matches = [e for e in candidates if selected in (e.get("id"), e.get("name"))]
    else:
        matches = [e for e in candidates if e.get("active") == "true" or e.get("default") == "true"]
        if not matches and len(candidates) == 1:
            matches = candidates
    if len(matches) != 1:
        raise ValueError("Active entry is missing or ambiguous.")
    return matches[0]

def decode_installation(data):
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("Installation file exceeds 16 MiB.")
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(MAX_FILE_SIZE + 1)
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("Decompressed installation exceeds 16 MiB.")
    root = ET.fromstring(data, forbid_dtd=True)
    if tag(root) != "installation":
        raise ValueError("Unsupported installation format: expected PolyScope Installation XML.")
    warnings = []
    tcp = cog = mounting = mass = None
    tcp_name = ""
    tcp_settings = next((e for e in root if tag(e) == "tcpsettings"), None)
    if tcp_settings is not None:
        try:
            entries = [e for e in tcp_settings.iter() if tag(e) == "tcp"]
            active = selected_node(tcp_settings, entries, ("activePose", "activeTCP"))
            tcp = numbers(value(active, "offset", "pose"), 6)
            tcp_name = active.get("name", "")
        except ValueError as error:
            warnings.append(f"TCP unavailable: {error}")
    else:
        warnings.append("TCP settings not found in this installation format.")

    # PolyScope 3 / early 5 stored payload with TCP settings. Later versions
    # can store a separate named payload collection.
    payload_settings = next((e for e in root if tag(e) == "payloadsettings"), None)
    payload_node = tcp_settings
    mass_names = ("toolPayload",)
    cog_names = ("toolPayloadCenterOfGravity", "toolPayloadCoG", "toolPayloadCog", "centerOfGravity")
    try:
        if payload_settings is not None:
            entries = [e for e in payload_settings.iter() if tag(e) == "payload"]
            payload_node = selected_node(payload_settings, entries, ("activePayload", "defaultPayload"))
            mass_names = ("mass", "payloadMass")
            cog_names = ("centerOfGravity", "cog")
        if payload_node is None:
            raise ValueError("Payload settings not found.")
        mass = numbers(value(payload_node, *mass_names), 1)[0]
        if mass < 0:
            mass = None
            raise ValueError("Payload mass cannot be negative.")
        cog_text = value(payload_node, *cog_names)
        if cog_text is not None:
            cog = numbers(cog_text, 3)
        elif value(payload_node, "useTCPAsCenterOfGravity") == "true" and tcp is not None:
            cog = tcp[:3]
        elif mass == 0:
            cog = (0., 0., 0.)
        else:
            warnings.append("Payload center of gravity missing; CoG marker is hidden.")
    except ValueError as error:
        warnings.append(f"Payload unavailable or incomplete: {error}")

    # WorldtoMarshal is the mounting transform in the built-in geometry view.
    # Only look within GeomFeatures, never arbitrary URCap feature transforms.
    geometry = next((e for e in root if tag(e) == "geomfeatures"), None)
    mounts = [] if geometry is None else [e for e in geometry.iter() if tag(e) == "worldtomarshal"]
    try:
        if len(mounts) != 1:
            raise ValueError("Mounting angles missing or ambiguous; showing robot base coordinates.")
        mounting = (numbers(mounts[0].get("baseAngle"), 1)[0],
                    numbers(mounts[0].get("tiltAngle"), 1)[0])
    except ValueError as error:
        warnings.append(str(error))
    return InstallationData(tcp, tcp_name, mass, cog, mounting, tuple(warnings))
