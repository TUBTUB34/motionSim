# UR Robot Simulator

A desktop, read-only live viewer for Universal Robots. **UR15 is selected by default**;
UR3, UR5, UR10, UR3e, UR5e, UR10e, UR16e, UR20 and UR30 are also selectable.

## Run

Requires Python 3.10+ and Tkinter in a graphical desktop session.

```bash
# Ubuntu/Debian, if Tkinter is missing:
sudo apt install python3-tk

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

On Windows, install Python with Tcl/Tk support and activate with
`.venv\Scripts\activate` instead.

## Connect

1. Enter the robot IP (default **10.22.33.81**) and select the exact model.
2. Optionally enter an SSH password to retrieve its installation; the username defaults to **root**.
   Empty fields show their defaults as hints. Leave them blank to use those values,
   or type to override them. There is no default password.
3. Leave the installation name blank for `default.installation`, or enter a name
   with or without the `.installation` extension.
4. Set the remote installation folder. It defaults to **/programs** as configured
   for this project; change it to `/programs` or another folder if that is where
   your controller stores installations.
5. Click **Connect**. Drag the 3D view to orbit and scroll to zoom.

Use **Display overlays** above the viewport to toggle the ground grid,
coordinate axes, live TCP, installation TCP, payload/center of gravity, robot
labels, and viewport information. **Hide all overlays** leaves just the robot;
**Show all overlays** restores them. These choices persist while editing or
reconnecting during the current app session and do not affect the robot data.

Use **Align view to** above the viewport to choose:

- **World (mounting)**: display the installed mounting orientation.
- **Robot base**: place the base at the grid origin with its XYZ axes aligned.
- **Live TCP**: align the grid to the TCP pose at the moment you select it, then
  hold that reference fixed. The base stays stationary and the TCP moves as the
  joints move. Select Live TCP again to align to its new pose. If selected before
  data arrives, the first received TCP pose becomes the reference.

Use **Preview tool** to attach a default flange-down or flange-out gripper,
parallel gripper, reference flange-down gripper, wide frame gripper, suction cup, or dispensing
nozzle to the flange, or select **None** to remove it. The gripper slider moves
its jaws from closed to an 80 mm gap. Tools follow the flange in every display
frame, and choices persist across reconnects in the current app session.
These are illustrative fixed-size tools, not vendor CAD models. They do not
change the live/saved TCP, payload, or robot settings, send tool commands, or
simulate contact, gripping forces, suction, or material flow. A live joint
sample is required to display the robot and attached tool.

The **Flange-down gripper** is a simplified version of the supplied reference
images, with an open mounting frame, long actuator body, guide rods, two roller
rails, pneumatic cylinders and orange sensors. The opening slider spreads the
roller rails. Its roughly 550 mm long body and jaw travel are illustrative;
dimensions and mechanism motion have not been measured from the real tool.

The **Wide frame gripper** approximates the second reference with an open frame,
four long crossbars, sliding jaw rails, pneumatic actuators and orange sensors.
**Default flange-down gripper** points its fingers along flange +Z;
**Default flange-out gripper** points along flange +X with a right-angle adapter.
These directions are relative to the flange, not world gravity.

**Tool working point** defaults to **Installation TCP**, using both its position
and axis-angle rotation. Choose **Live TCP** to follow the controller's active TCP,
or **Tool preset** for the illustrative flange-mounted dimensions. If the selected
TCP is unavailable, the preview falls back to its preset and labels that fallback.
The amber **Tool TCP** marker shows the resulting working point and can be hidden
in Display overlays. Alignment moves the rigid preview so its working point
matches the selected TCP; a generic adapter connects any mounting offset. This
does not infer the real tool's dimensions from a TCP, and the adapter is not a
mechanical design. Nominal flange calibration differences can affect live alignment.
Saved payload and CoG remain installation data; tool selection does not estimate
or replace mass, CoG, or controller settings.

The arm, saved TCP and payload markers all use the same selected frame. This is
only a visualization change; the live numeric TCP readout stays in robot-base
coordinates. Orbit/zoom and Reset camera preserve the locked reference. The
selected mode persists when reconnecting; a new connection captures a fresh TCP reference.

Enable RTDE on the controller and make TCP port **30004** reachable.
The client negotiates RTDE v2 and requests `actual_q`, `actual_TCP_pose` and
`timestamp` at 30 Hz. It does not send motion commands, URScript or RTDE inputs.
The display waits for actual joint samples before drawing an arm.

SCP uses SSH port **22** and the supplied credentials. SSH must be enabled and the
account must be able to read the chosen file. Known SSH hosts are checked against
your existing known-hosts file. An unknown host prompts for fingerprint verification
and is trusted only for that connection. If a different robot reuses the IP and
its key has changed, a prompt shows the saved and received fingerprints. Accepting
clears the stale key for that connection and retries with the accepted key;
your on-disk known-hosts file is unchanged.
An SCP/login/file error is shown without stopping RTDE or the 3D view.
Missing either credential skips SCP and displays the payload-info notice.

**Disconnect** stops receiving and retains the last pose. **Reconnect** starts a
fresh session. Stale data and lost connections are explicitly indicated.
Settings and passwords are kept only in memory. Downloaded files are temporary
and removed after parsing.

## Installation and accuracy

The loader handles gzip-compressed or plain PolyScope XML. It decodes the selected
TCP offset (metres and axis-angle radians), payload mass (kg), available payload
center of gravity relative to the flange (metres), and mounting angles.

Supported layouts include `TCPSettings` and Java-qualified `TCPSettingsImpl`
with `availablePoses/tcp`, legacy `toolPayload` and recognized CoG attributes,
named `PayloadSettings` collections (including per-entry `defaultPayload` flags),
and mounting angles in `GeomFeatures/.../WorldtoMarshal` or
`Features/CameraView/worldTransform/WorldtoMarshal`. Public UR examples from
PolyScope 3.2, 5.1 and 5.6 and a supplied PolyScope 5.22 installation were checked.
Installation XML is not a stable public schema: **a particular UR15/software
version may need additional field mappings**. Missing, ambiguous or unrecognized
fields are reported in the sidebar; they are not presented as successfully loaded.
PolyScope X formats other than this XML layout are not supported.

The green marker is the controller's live TCP. The purple marker is the saved
installation TCP calculated from nominal flange kinematics. The amber marker
shows the saved payload center of gravity and mass. Mounting orients the entire
arm and its markers; when unavailable, the view uses robot base coordinates.
Mounting is an orientation, not a surveyed world position.

The viewport uses tapered metal arm shoulders, slimmer tubes with recessed end
seals, satin-blue motor caps with machined rims and socket fasteners, a bolted
mounting plate, and a tool flange with a concentric center recess. Metal
and polymer finishes use different lighting; mesh detail adapts to zoom. It is a kinematic visualization with
approximate procedural geometry,
not a collision or dynamics simulator. Payload does not change the measured
joint angles. Saved installation settings can differ from active values changed
by a running program. Nominal dimensions do not include robot-specific factory
calibration, so the predicted and measured TCP positions may differ.

## Verification

```bash
python -m unittest discover -v
# Also exercise a real Tk window against a loopback mock RTDE robot:
MOTIONSIM_UI_TESTS=1 python -m unittest tests.test_ui -v
```

Tests use synthetic data and mocked SCP; no physical robot is contacted.

## References

- [UR RTDE protocol](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html)
- [UR nominal kinematic dimensions, including UR15](https://www.universal-robots.com/articles/ur/application-installation/dh-parameters-for-calculations-of-kinematics-and-dynamics)
- [UR installation example (PolyScope 3.2)](https://www.universal-robots.com/articles/ur/application-installation/using-a-barcode-reader-directly-with-the-robot/)
- [Official ROS driver's installation fixture (PolyScope 5.1)](https://github.com/UniversalRobots/Universal_Robots_ROS2_Driver/blob/main/.github/dockerursim/.vol/default.installation)
