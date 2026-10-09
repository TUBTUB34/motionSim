"""Read-only RTDE telemetry schema and presentation helpers.

Field definitions: Universal Robots' RTDE guide (linked in README).
Missing optional outputs are unavailable, never inferred as zero/healthy.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    group: str
    label: str
    unit: str = ""


BASE_FIELDS = (
    Field("timestamp", "DOUBLE", "Controller", "Controller uptime", "s"),
    Field("actual_q", "VECTOR6D", "Joints", "Position · base → wrist 3", "rad"),
    Field("actual_TCP_pose", "VECTOR6D", "TCP", "Pose · XYZ / rotation vector", "m / rad"),
)
STATUS_FIELDS = (
    Field("actual_execution_time", "DOUBLE", "Controller", "Controller execution time", "ms"),
    Field("robot_mode", "INT32", "Controller", "Robot mode"),
    Field("runtime_state", "UINT32", "Controller", "Program state"),
    Field("script_control_line", "UINT32", "Controller", "Controlling URScript line"),
    Field("robot_status_bits", "UINT32", "Controller", "Robot flags"),
    Field("safety_mode", "INT32", "Safety", "Safety mode"),
    Field("safety_status", "INT32", "Safety", "Safety status"),
    Field("safety_status_bits", "UINT32", "Safety", "Safety flags"),
    Field("speed_scaling", "DOUBLE", "Motion", "Trajectory speed scaling", "%"),
    Field("time_scale_source", "INT32", "Motion", "Speed limiting reason"),
    Field("target_speed_fraction", "DOUBLE", "Motion", "Target speed fraction", "%"),
    Field("actual_qd", "VECTOR6D", "Joints", "Velocity · base → wrist 3", "rad/s"),
    Field("actual_current", "VECTOR6D", "Joints", "Current · base → wrist 3", "A"),
    Field("actual_current_as_torque", "VECTOR6D", "Joints", "Torque · base → wrist 3", "Nm"),
    Field("joint_temperatures", "VECTOR6D", "Joints", "Temperature · base → wrist 3", "°C"),
    Field("joint_mode", "VECTOR6INT32", "Joints", "Modes · base → wrist 3"),
    Field("actual_joint_voltage", "VECTOR6D", "Joints", "Voltage · base → wrist 3", "V"),
    Field("tcp_offset", "VECTOR6D", "TCP", "Active TCP offset", "m / rad"),
    Field("actual_TCP_speed", "VECTOR6D", "TCP", "Speed · XYZ / rotation", "m/s / rad/s"),
    Field("actual_TCP_force", "VECTOR6D", "TCP", "Wrench · Fx Fy Fz / Tx Ty Tz", "N / Nm"),
    Field("actual_main_voltage", "DOUBLE", "Electrical", "Main voltage", "V"),
    Field("actual_robot_voltage", "DOUBLE", "Electrical", "Robot voltage", "V"),
    Field("actual_robot_current", "DOUBLE", "Electrical", "Robot current", "A"),
    Field("actual_digital_input_bits", "UINT64", "I/O", "Digital inputs"),
    Field("actual_digital_output_bits", "UINT64", "I/O", "Digital outputs"),
    Field("analog_io_types", "UINT32", "I/O", "Analog channel types"),
    *(Field(f"standard_analog_{direction}{i}", "DOUBLE", "I/O", f"Analog {direction} {i}")
      for direction in ("input", "output") for i in range(2)),
    Field("io_current", "DOUBLE", "I/O", "I/O current", "mA"),
    Field("tool_analog_input_types", "UINT32", "Tool", "Analog input types"),
    Field("tool_analog_input0", "DOUBLE", "Tool", "Analog input 0"),
    Field("tool_analog_input1", "DOUBLE", "Tool", "Analog input 1"),
    Field("tool_mode", "UINT32", "Tool", "Tool mode"),
    Field("tool_output_voltage", "INT32", "Tool", "Output voltage", "V"),
    Field("tool_output_current", "DOUBLE", "Tool", "Output current", "mA"),
    Field("tool_temperature", "DOUBLE", "Tool", "Temperature", "°C"),
    Field("payload", "DOUBLE", "Payload", "Active payload mass", "kg"),
    Field("payload_cog", "VECTOR3D", "Payload", "Active center of gravity", "m"),
    Field("payload_inertia", "VECTOR6D", "Payload", "Inertia · Ixx Iyy Izz Ixy Ixz Iyz", "kg·m²"),
)
TYPE_FORMATS = {"DOUBLE": "d", "VECTOR6D": "6d", "VECTOR3D": "3d",
                "INT32": "i", "UINT32": "I", "UINT64": "Q", "VECTOR6INT32": "6i"}
ROBOT_MODES = dict(enumerate(("No controller", "Disconnected", "Confirm safety", "Booting",
    "Power off", "Power on", "Idle", "Backdrive", "Running", "Updating firmware"), -1))
ROBOT_MODES.update({9: "Powering off", 10: "Arm booting", 11: "Arm updating"})
SAFETY_MODES = {1: "Normal", 2: "Reduced", 3: "Protective stop", 4: "Recovery", 5: "Safeguard stop",
    6: "System emergency stop", 7: "Robot emergency stop", 8: "Violation", 9: "Fault",
    12: "Automatic mode safeguard stop", 13: "System three-position enabling stop",
    14: "Teach pendant three-position enabling stop", 15: "IMMI emergency stop", 16: "IMMI safeguard stop",
    17: "PROFIsafe waiting for parameters", 18: "PROFIsafe automatic safeguard stop",
    19: "PROFIsafe safeguard stop", 20: "PROFIsafe emergency stop", 22: "Safety API safeguard stop",
    23: "I/O plane stop"}
RUNTIME_STATES = {0: "Stopping", 1: "Stopped", 2: "Running", 3: "Pausing", 4: "Paused", 5: "Resuming", 6: "Retracting"}
JOINT_MODES = {235: "Reset", 236: "Shutting down", 238: "Backdrive", 239: "Power off",
    240: "Ready for power off", 245: "Not responding", 246: "Motor initialization", 247: "Booting",
    249: "Bootloader", 251: "Violation", 252: "Fault", 253: "Running", 255: "Idle"}
TIME_SCALE_SOURCES = {-1: "Other", 0: "Program not running", 1: "Not scaled", 2: "Joint torque limit",
    3: "Joint acceleration limit", 4: "Power supply limit", 5: "Momentum safety limit",
    6: "Stopping time safety limit", 7: "Stopping distance safety limit", 8: "Tool speed safety limit",
    9: "Elbow speed safety limit", 10: "Joint speed safety limit", 11: "Safety transition",
    12: "Safety API stopping distance", 13: "User tool wrench limit", 14: "External axis speed limit",
    15: "External axis stopping", 55: "Force limiting above base"}
ROBOT_FLAGS = ("Power on", "Program running", "Teach button pressed", "Power button pressed")
SAFETY_FLAGS = ("Normal mode", "Reduced mode", "Protective stopped", "Recovery mode", "Safeguard stopped",
    "System emergency stopped", "Robot emergency stopped", "Emergency stopped", "Violation", "Fault",
    "Stopped due to safety", "Three-position enabling input active")


def enum_text(value, mapping):
    return "Unavailable" if value is None else mapping.get(value, f"Unknown ({value})")


def script_line_text(values):
    line = values.get("script_control_line")
    if line is None:
        return "URScript line unavailable on this controller"
    return f"URScript line {line}" if line else "No thread currently controls robot motion (line 0)"


def format_value(field, value, values):
    if value is None:
        return "Unavailable on this controller"
    mapping = {"robot_mode": ROBOT_MODES, "runtime_state": RUNTIME_STATES,
               "safety_mode": SAFETY_MODES, "safety_status": SAFETY_MODES,
               "tool_mode": JOINT_MODES, "time_scale_source": TIME_SCALE_SOURCES}.get(field.name)
    if mapping:
        return enum_text(value, mapping)
    if field.name == "joint_mode":
        return " · ".join(enum_text(v, JOINT_MODES) for v in value)
    if field.name == "script_control_line":
        return script_line_text(values)
    if field.name in ("robot_status_bits", "safety_status_bits"):
        labels = ROBOT_FLAGS if field.name == "robot_status_bits" else SAFETY_FLAGS
        flags = [label for bit, label in enumerate(labels) if value & (1 << bit)]
        unknown = value & ~((1 << len(labels)) - 1)
        if unknown:
            flags.append(f"Unknown bits: 0x{unknown:X}")
        return ", ".join(flags) or "No flags set"
    if "digital_" in field.name:
        groups = [f"{label}: " + " ".join(str((value >> bit) & 1) for bit in range(start, stop))
                  for label, start, stop in (("Standard 0–7", 0, 8), ("Config 0–7", 8, 16), ("Tool 0–1", 16, 18))]
        return "  |  ".join(groups) + f"  [0x{value:X}]"
    if field.name == "tool_analog_input_types":
        return " / ".join("V" if value & (1 << i) else "mA" for i in range(2)) + " · IN0 / IN1"
    if field.name == "analog_io_types":
        return " / ".join("V" if value & (1 << i) else "mA" for i in range(4)) + " · IN0 / IN1 / OUT0 / OUT1"
    unit = field.unit
    if field.name.startswith(("standard_analog_", "tool_analog_input")):
        types = values.get("tool_analog_input_types" if field.name.startswith("tool_") else "analog_io_types")
        bit = int(field.name[-1]) + (2 if "output" in field.name else 0)
        unit = ("V" if types & (1 << bit) else "mA") if types is not None else "(unit unavailable)"
    if unit == "%":
        value *= 100
    text = "  ·  ".join(f"{v:.3f}" for v in value) if isinstance(value, tuple) else (
        f"{value:.3f}" if isinstance(value, float) else str(value))
    return f"{text} {unit}".strip()
