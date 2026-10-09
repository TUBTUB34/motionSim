# UR Motion Studio

One desktop application for the existing UR simulator and native log analysis.
Charts open inside the application; no browser, web server or internet connection
is needed to view them. The simulator's motion, camera, overlays, tooling and RTDE
behavior are retained.

## Logs & graphs

1. Run `python main.py` and click **Logs & graphs**. This is also available while
   the simulator is connected; log analysis does not require an RTDE connection.
2. Enter the robot IP, SSH username (usually `root`) and password. The setup
   screen's credentials are copied when the log workspace first opens.
3. Check **Remote log file**. The suggested path is
   `/root/log_history.txt`, matching the [UR service manual](https://www.universal-robots.com/manuals/EN/HTML/SW5_22/Content/prod-serv-man/E-series/serv-man-SD-data.htm);
   change it for your controller if necessary.
   SSH/SFTP access must be enabled and reachable on port 22.
4. Click **Fetch log & graph** and verify the displayed SSH host fingerprint.
   The app retrieves both `log_history.bak` and `log_history.txt` from that folder,
   reads the older backup first, and combines them into one timeline. Overlapping
   incidents are counted once while distinct robot sessions remain separate.
   If the backup does not exist, the current log loads with a visible notice;
   other transfer failures retain the previous report.
   The download is read-only. Credentials stay in memory; temporary downloads
   are removed after parsing. If a different robot reuses an IP, a popup shows the
   old and new SSH fingerprints. Accepting replaces that IP's saved key after a
   successful reconnection; declining leaves it unchanged. Other hosts are kept.
   This uses the current user's `.ssh/known_hosts`: the Windows profile when run
   on Windows, or the Linux home directory when run in WSL. Those stores are separate.
5. Choose joints, incident types, codes and optional `YYYY-MM-DD` dates. Switch
   among bar, line and scatter charts, with daily, calendar-week or calendar-month
   buckets. The toolbar supports zoom, pan and saving a chart image.

**Open local log…** supports offline `.txt`, `.bak` and `.log` files in the original UR
log-history format. Selecting a `.txt` or `.bak` automatically includes its matching
partner in the same folder when present. **Incident details** shows up to 500 latest matches; CSV
export includes all filtered incidents. **Joint overview** summarizes the full
loaded log, independently of chart filters. Closing the log window retains its
data until you exit the application. An unsuccessful load retains the previous log.

The original graph generator's parser, incident deduplication, monitored codes and
joint diagnostic summaries are reused. Counts represent monitored joint incidents,
not all controller messages or failure predictions. Missing joint mappings and
malformed records are reported. Downloads/local files are limited to 64 MiB each (128 MiB per pair);
chart bins widen beyond 366 periods, and more than 18 code series are combined
into the 17 most frequent plus **Other series**, preserving total counts.
Transfers and parsing run in the background. Download status shows the current
file, transferred/total MiB and download speed. Downloads have no overall time
limit while data is arriving; a stalled read times out after 60 seconds without
incoming data. Initial connection/authentication timeouts remain separate.
Cancellation closes the connection. No robot credentials are stored on disk.

## Build a standalone Windows executable

On **Windows**, install 64-bit Python 3.12 with Tcl/Tk, then run:

```bat
build_windows.bat
```

The result is **`dist\URMotionStudio.exe`**, a single executable containing Python,
Tk, chart libraries and robot assets. Copy it to a Windows PC; the target does
not need Python or a browser. First launch/first simulator rendering can take
longer while libraries unpack and the renderer compiles. Keep the generated
executable outside the source tree when distributing it. Builds are unsigned.
The build runs an offline smoke check of the packaged parser, chart and robot
renderer. You can repeat it with `URMotionStudio.exe --smoke-test` (exit code 0
means success). It never connects to a robot.

Alternatively, run the repository's **Windows desktop executable** workflow from
GitHub Actions and download its artifact. It runs mocked desktop/simulator tests
before packaging. This workflow is manual and must be run after these files are
committed to the repository.

Windows executables must be built and tested on Windows; PyInstaller is not a
cross-compiler ([PyInstaller documentation](https://pyinstaller.org/en/latest/)).
Before distributing, launch the EXE on a clean Windows machine and check a local
log, a robot connection and the simulator. No physical robot or Windows host is
available in the Linux development environment used for this integration.

## Existing simulator

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

## Simulator and Status tabs

The **Simulator** tab gives the viewport more space and displays program state
and the controlling **URScript line number** above the robot. **View options**
contains the existing alignment, overlays and tool preview controls.

The **Status** tab groups controller, safety, motion, joint, TCP, electrical,
I/O, tool and live payload readings. Expand robot/safety flags to see individual
Yes/No states. Digital signals list standard, configurable and tool channels;
analog readings include controller-reported units. Horizontal and vertical
scrollbars keep long values accessible. Unknown enum values retain their numeric
code, and unsupported fields say **Unavailable on this controller**.

The client removes optional fields explicitly rejected by firmware and retries
without losing joint/TCP visualization. It never substitutes missing telemetry
with healthy/zero values. Live payload readings are separate from the saved
installation values used by the existing payload overlay.

The line readout uses `script_control_line`, available since PolyScope 5.9 / 3.14.
It identifies the script thread currently controlling motion; zero means no
thread holds that control. It is **not the PolyScope program-tree line**, and
RTDE does not supply the corresponding source text. Fast-changing lines can be
missed between samples; this is a monitor, not an execution trace or debugger.

Status readouts refresh at 5 Hz while the receiver continuously drains the
30 Hz stream into a single latest snapshot. Stationary poses skip mesh work;
hidden simulator tabs retain the newest pose without rendering every sample.
Disconnected/stale readouts are explicitly marked as last received values.

Sources: [UR RTDE field and protocol reference](https://www.universal-robots.com/manuals/EN/HTML/SW10_10/Content/Prod-RTDE/Real_Time_Data_Exchange_RTDE.htm),
[UR telemetry and state constants](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html).
Actual availability depends on controller firmware. No physical robot was used
for automated verification; tests use synthetic packets and a loopback robot.

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

Use the **Display overlays** checkboxes in **View options** to toggle the ground grid,
coordinate axes, live TCP, installation TCP, payload/center of gravity, robot
labels, and viewport information. **Hide all overlays** leaves just the robot;
**Show all overlays** restores them. These choices persist while editing or
reconnecting during the current app session and do not affect the robot data.

Use **Align view to** in **View options** to choose:

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
and axis-angle rotation. Choose **Live TCP** to place the tool's working point at
the controller's active TCP position while keeping its flange-mounted orientation.
For the suction cup, this places the center of its contact face at the live TCP;
the controller's TCP axis rotation does not flip the cup upside down. Choose
**Tool preset** for the illustrative flange-mounted dimensions. If the selected
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
The client negotiates RTDE v2 and requests joint, TCP and supported status outputs
at 30 Hz. It does not send motion commands, URScript or RTDE inputs.
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

The viewport uses Universal Robots' official model-specific visual geometry:
CB-Series for UR3/UR5/UR10, e-Series for UR3e/UR5e/UR10e/UR16e, and UR Series
for UR15/UR20/UR30. The selected model supplies its own shoulder housings,
blue covers, link proportions, wrist assembly and flange. These are compact
meshes derived from UR's published robot descriptions, not generic cylinders.

A depth buffer resolves overlapping housings correctly. Smooth surface lighting
and supersampling soften curved joints and silhouettes. The renderer draws only
the robot's screen region into a single Tk image and caches model geometry.
The first render may take a moment while Numba compiles its pixel renderer;
subsequent runs use its disk cache. Install the updated `requirements.txt` for
Numba and Pillow. Model assets are bundled and require no runtime downloads.
See [asset provenance, licenses and rebuild instructions](assets/robots/README.md).

Small surface markings are approximated. It is a kinematic visualization,
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
