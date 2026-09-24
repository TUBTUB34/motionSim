# UR Robot Simulator

A desktop, read-only live viewer for Universal Robots. **UR15 is selected by default**;
UR3, UR5, UR10, UR3e, UR5e, UR10e, UR16e and UR20 are also selectable.

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

1. Enter the robot IP and select the exact model.
2. Optionally enter SSH username/password to retrieve its installation.
3. Leave the installation name blank for `default.installation`, or enter a name
   with or without the `.installation` extension.
4. Set the remote installation folder. It defaults to **/programs** as configured
   for this project; change it to `/programs` or another folder if that is where
   your controller stores installations.
5. Click **Connect**. Drag the 3D view to orbit and scroll to zoom.

Enable RTDE on the controller and make TCP port **30004** reachable.
The client negotiates RTDE v2 and requests `actual_q`, `actual_TCP_pose` and
`timestamp` at 30 Hz. It does not send motion commands, URScript or RTDE inputs.
The display waits for actual joint samples before drawing an arm.

SCP uses SSH port **22** and the supplied credentials. SSH must be enabled and the
account must be able to read the chosen file. Known SSH hosts are checked against
your existing known-hosts file. An unknown host prompts for fingerprint verification
and is trusted only for that connection; changed known keys are rejected.
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
named `PayloadSettings` collections, and `GeomFeatures/.../WorldtoMarshal`
mounting angles. Public UR examples from PolyScope 3.2, 5.1 and 5.6 were checked.
Installation XML is not a stable public schema: **a particular UR15/software
version may need additional field mappings**. Missing, ambiguous or unrecognized
fields are reported in the sidebar; they are not presented as successfully loaded.
PolyScope X formats other than this XML layout are not supported.

The green marker is the controller's live TCP. The purple marker is the saved
installation TCP calculated from nominal flange kinematics. The amber marker
shows the saved payload center of gravity and mass. Mounting orients the entire
arm and its markers; when unavailable, the view uses robot base coordinates.
Mounting is an orientation, not a surveyed world position.

This is a kinematic visualization with simplified cylindrical link geometry,
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
