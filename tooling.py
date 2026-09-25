"""Illustrative flange-mounted tool geometry, in metres; no robot commands."""

import math
import numpy as np
from kinematics import pose_matrix

TOOLS = ("None", "Default flange-down gripper", "Default flange-out gripper",
         "Parallel gripper", "Flange-down gripper", "Wide frame gripper",
         "Suction cup", "Dispensing nozzle")
TCP_SOURCES = ("Tool preset", "Installation TCP", "Live TCP")


def tool_tcp(tool):
    """Illustrative working point and orientation relative to the mesh origin."""
    if tool == "Default flange-out gripper":
        return pose_matrix((.16, 0, .06, 0, math.pi/2, 0))
    position = {"None": (0, 0, 0), "Flange-down gripper": (.035, 0, .18),
                "Wide frame gripper": (0, 0, .22), "Suction cup": (0, 0, .118),
                "Dispensing nozzle": (0, 0, .1905)}.get(tool, (0, 0, .16))
    return pose_matrix((*position, 0, 0, 0))


def tool_placement(tool, flange, source="Tool preset", installation_tcp=None, live_tcp=None):
    """Resolve a display-space working frame and rigid mesh pose without changing robot data."""
    if source not in TCP_SOURCES:
        raise ValueError(f"Unknown TCP source: {source}")
    preset = tool_tcp(tool)
    if source == "Installation TCP" and installation_tcp is not None:
        target = flange @ pose_matrix(installation_tcp)
    elif source == "Live TCP" and live_tcp is not None:
        target = live_tcp
    else:
        return flange, flange @ preset, ("Tool preset" if source == "Tool preset"
                                         else f"{source} unavailable · using tool preset")
    return target @ np.linalg.inv(preset), target, source


def wide_frame_parts(opening):
    """Open roller frame inspired by the second reference, about 650 x 500 mm."""
    parts = []
    def box(center, dimensions, color="#aeb7bd"):
        parts.append(("box", center, dimensions, color))
    def rod(start, end, radius, color="#90999f"):
        parts.append(("cylinder", start, end, radius, radius, color))
    rod((0, 0, .02), (0, 0, .045), .065)
    box((0, 0, .057), (.22, .16, .024))
    # Open support rails and end brackets, leaving the center unobstructed.
    for y in (-.09, .09):
        box((0, y, .085), (.65, .023, .032))
    for x in (-.30, .30):
        box((x, 0, .11), (.025, .5, .035))
        for y in (-.235, .235):
            box((x, y, .165), (.025, .03, .14))
    for y in (-.235, .235):
        rod((-.32, y, .11), (.32, y, .11), .019)
        rod((-.32, y, .22), (.32, y, .22), .021)
    gap = .055 + .105*max(0., min(1., float(opening)))
    for side in (-1, 1):
        y = side*gap
        box((0, y, .195), (.68, .016, .05))
        box((0, y-side*.017, .222), (.68, .05, .009))
        for x in (-.19, .19):
            rod((x, side*.235, .16), (x, y, .16), .009, "#c4cdd3")
            box((x, y, .16), (.055, .035, .04))
            rod((x, side*.235, .16), (x, side*.18, .16), .016)
            rod((x, side*.235, .14), (x, side*.235, .12), .007, "#cf8b27")
        for x in (-.29, .29):
            rod((x, side*.235, .09), (x, side*.235, .045), .006, "#cf8b27")
            rod((x, side*.235, .045), (x, side*.235, .037), .007, "#35414a")
    return parts


def flange_down_parts(opening):
    """Simplified reference-image assembly; dimensions and jaw travel are illustrative.

    X follows the long rails, Y is jaw travel, and +Z extends away from the flange.
    """
    steel, dark, silver = "#8f9ca5", "#35414a", "#c4cdd3"
    parts = []

    def box(center, dimensions, color=steel):
        parts.append(("box", center, dimensions, color))

    def rod(start, end, radius, color=silver):
        parts.append(("cylinder", start, end, radius, radius, color))

    # Circular flange adapter and an open rectangular supporting plate.
    rod((0, 0, .02), (0, 0, .038), .064)
    for y in (-.065, .065):
        box((0, y, .05), (.32, .025, .024))
    for x in (-.1475, 0, .1475):
        box((x, 0, .05), (.025, .13, .024))
    # Long extruded actuator body, with bright longitudinal ribs.
    box((.04, 0, .083), (.44, .11, .04), dark)
    for y in (-.045, -.025, .025, .045):
        box((.04, y, .108), (.44, .007, .01), silver)
    for x in (-.19, .27):
        box((x, 0, .09), (.02, .14, .075))
    jaw_y = .085 + .055*max(0., min(1., float(opening)))
    for side in (-1, 1):
        y = side*jaw_y
        # Guide rods and bearing blocks carry each moving roller rail.
        for x in (-.14, .14):
            rod((x, 0, .132), (x, y, .132), .007)
            box((x, y, .132), (.04, .027, .044), dark)
            rod((x, y, .107), (x, y, .075), .006, "#c88729")
            rod((x, y, .074), (x, y, .067), .007, dark)
        box((.035, y, .161), (.55, .016, .025))
        for x in (-.21, -.135, -.06, .015, .09, .165, .24):
            rod((x, y-side*.018, .18), (x, y+side*.038, .18), .011)
            rod((x, y+side*.038, .18), (x, y+side*.041, .18), .006, dark)
        # Side pneumatic cylinder, piston and blue connector collars.
        rod((-.10, side*.082, .082), (.05, side*.082, .082), .014, dark)
        rod((.05, side*.082, .082), (.14, side*.082, .082), .005)
        for x in (-.09, .035):
            rod((x, side*.082, .065), (x, side*.082, .044), .007, dark)
            rod((x, side*.082, .05), (x, side*.082, .045), .009, "#58a9bb")
    return parts


def tool_parts(tool, opening=0.5):
    """Return boxes (center, dimensions) and cylinders (start, end, radii)."""
    if tool not in TOOLS:
        raise ValueError(f"Unknown tool: {tool}")
    if tool == "None":
        return []
    if tool == "Default flange-down gripper":
        return tool_parts("Parallel gripper", opening)
    if tool == "Default flange-out gripper":
        # Turn the standard jaws sideways; retain a flange-facing mounting adapter.
        rotation = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])
        offset = np.array([0, 0, .06])
        parts = [("cylinder", (0, 0, .003), (0, 0, .06), .032, .032, "#aabcc8")]
        for kind, *part in tool_parts("Parallel gripper", opening)[1:]:
            center, dimensions, color = part
            parts.append((kind, tuple(rotation @ center+offset),
                          tuple(abs(rotation) @ dimensions), color))
        return parts
    parts = [("cylinder", (0, 0, .003), (0, 0, .02), .032, .032, "#aabcc8")]
    if tool == "Parallel gripper":
        parts.append(("box", (0, 0, .05), (.105, .055, .06), "#354b5c"))
        gap = .08 * max(0., min(1., float(opening)))
        for side in (-1, 1):
            x = side*(gap/2+.01)
            parts.append(("box", (x, 0, .085), (.02, .043, .018), "#b6c7d1"))
            parts.append(("box", (x, 0, .125), (.02, .025, .07), "#b6c7d1"))
            parts.append(("box", (side*(gap/2+.002), 0, .143),
                          (.004, .027, .034), "#24313b"))
    elif tool == "Flange-down gripper":
        parts.extend(flange_down_parts(opening))
    elif tool == "Wide frame gripper":
        parts.extend(wide_frame_parts(opening))
    elif tool == "Suction cup":
        parts.extend([
            ("cylinder", (0, 0, .02), (0, 0, .085), .014, .014, "#b9cad4"),
            ("cylinder", (0, 0, .085), (0, 0, .11), .015, .037, "#425463"),
            ("cylinder", (0, 0, .11), (0, 0, .117), .039, .039, "#24313b"),
            ("cylinder", (0, 0, .117), (0, 0, .118), .031, .031, "#101923"),
        ])
    else:
        parts.extend([
            ("cylinder", (0, 0, .02), (0, 0, .11), .023, .023, "#6eabc9"),
            ("cylinder", (0, 0, .11), (0, 0, .145), .023, .006, "#b9cad4"),
            ("cylinder", (0, 0, .145), (0, 0, .19), .006, .003, "#d6c28b"),
            ("cylinder", (0, 0, .19), (0, 0, .1905), .0018, .0018, "#18242d"),
        ])
    return parts
