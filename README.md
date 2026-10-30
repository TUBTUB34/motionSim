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
4. Click **Fetch log & graph**. Choose whether to also save the downloaded logs
   on your PC. If you choose yes, a save dialog lets you name the `.txt` file;
   the matching `.bak` is saved beside it when available. Declining or cancelling
   the save dialog still fetches and graphs the logs. The chosen folder is
   remembered until you exit the application and is also used for robot backups.
   Verify the displayed SSH host fingerprint.
   The app retrieves both `log_history.bak` and `log_history.txt` from that folder,
   reads the older backup first, and combines them into one timeline. Overlapping
   incidents are counted once while distinct robot sessions remain separate.
   If the backup does not exist, the current log loads with a visible notice;
   other transfer failures retain the previous report.
   The download is read-only. Credentials stay in memory; temporary downloads
   are removed after parsing. If a different robot reuses an IP, a popup shows the
   old and new SSH fingerprints. Accepting replaces that IP's saved key after a
   successful reconnection; declining leaves it unchanged. Other hosts are kept.
   A local save failure is reported without discarding the loaded graph.
   This uses the current user's `.ssh/known_hosts`: the Windows profile when run
   on Windows, or the Linux home directory when run in WSL. Those stores are separate.
5. Choose joints, incident types, codes and optional `YYYY-MM-DD` dates. Switch
   among bar, line and scatter charts, with daily, calendar-week or calendar-month
   buckets. Check **Aggregate** to show a running total over time for each joint:
   5 incidents on one day followed by 10 the next plots 5, then 15. Uncheck it to
   return to counts for each day, week or month. Both modes work with bar, line
   and scatter charts. Totals start within the selected date range and respect
   the joint, incident-type and code filters; **Separate codes** keeps a running
   total for each joint/code series. The toolbar
   supports zoom, pan and saving a chart image.

**Open local log…** supports offline `.txt`, `.bak` and `.log` files in the original UR
log-history format. Selecting a `.txt` or `.bak` automatically includes its matching
partner in the same folder when present. **Incident details** shows up to 500 latest matches; CSV
export includes all filtered incidents. **Joint overview** summarizes the full
loaded log, independently of chart filters. Closing the log window retains its
data until you exit the application. An unsuccessful load retains the previous log.

The parser, incident deduplication, monitored codes and joint diagnostic summaries
are maintained in this repository's `log_parser.py`, with chart aggregation in
`log_data.py`. Counts represent monitored joint incidents,
not all controller messages or failure predictions. Missing joint mappings and
malformed records are reported. Downloads/local files are limited to 64 MiB each (128 MiB per pair);
chart bins widen beyond 366 periods, and more than 18 code series are combined
into the 17 most frequent plus **Other series**, preserving total counts.
Transfers and parsing run in the background. Download status shows the current
file, transferred/total MiB and download speed. Downloads have no overall time
limit while data is arriving; a stalled read times out after 60 seconds without
incoming data. Initial connection/authentication timeouts remain separate.
Cancellation closes the connection. No robot credentials are stored on disk.

## Robot backups

Use **Simulator → Full backup…** for a combined robot-data archive. The action
uses the current robot's SSH settings and opens the transfer workspace. Choose
a local folder; progress and **Cancel** remain available in **Logs & graphs**.
If SSH credentials are missing, enter them there and click **Full backup…**.
The archive includes the available `/programs`, `/apps`, `/data/apps`,
`/root/log_history.txt`, `/root/log_history.bak`, and `/root/flightreports` sources.
An edited remote log path or flight-retrieval folder is used when configured.
Missing files/folders are listed as skipped; access or archive errors still stop
the job. It saves `full-backup.tar` in the selected directory, adding a unique
suffix when that name already exists. Cancelling the folder chooser creates no
robot archive. After the complete download is verified and flushed to disk, the
app removes only its temporary TAR and directory from the robot.

In **Logs & graphs**, enter the robot's SSH credentials, select **Programs**
(`/programs`), **Apps** (`/data/apps`), or both under **Backup contents**, and click
**Make backup…**. Both are selected by default; at least one must be selected.
**Full backup…** always includes all available full-backup sources, independently
of these checkboxes.
This works without loading logs or connecting the simulator. The app runs
`tar -P -cf backup.tar` with the selected available directories, using a unique private temporary
directory on the robot so an existing backup is not overwritten. The uncompressed
archive includes absolute paths, as requested by `-P`.
Missing directories are skipped and listed in the completion message. If neither
directory exists, no archive is created. Permission and archive errors still fail
the backup. **Status details…** opens the full message and lets you copy it.

Once creation succeeds, choose where to save `backup.tar` on your PC. If you
already selected a folder for logs or a previous backup in this session, the
backup saves there automatically. Existing archives are preserved by adding a
unique suffix to the new backup's name when needed. Download
progress, speed and estimated remaining time appear in the status bar. The app checks the downloaded byte count,
flushes the file to disk and replaces the chosen destination only after the
complete download. It then removes its archive and temporary directory from the
robot. Backups have no log-file size limit; both devices need space for the archive.

Cancelling the save dialog, an interrupted transfer or a local save failure keeps
the robot archive and any existing PC backup. The status shows the remote path
for manual recovery or cleanup; an archive interrupted during creation may be
incomplete. If remote cleanup fails after saving, the status confirms the local
save and identifies the remote location to check. SSH host verification is shared
with log downloads, and credentials stay in memory.

## Build a standalone Windows executable

On **Windows**, install 64-bit (x64) Python 3.14 with Tcl/Tk, then run:

```bat
build_windows.bat
```

Use a local Windows project folder for builds and tests; loading Python libraries
through a WSL/network share can be much slower.

The result is **`dist\URMotionStudio.exe`**, a single executable containing Python,
Tk, chart libraries and robot assets. Copy it to a Windows PC; the target does
not need Python or a browser. First launch/first simulator rendering can take
longer while libraries unpack and the renderer compiles. Keep the generated
executable outside the source tree when distributing it. Builds are unsigned.
The build runs an offline smoke check of the packaged log and flight workspaces,
PDF export, retrieval controls, transfer helper and robot renderer. You can repeat
it with `URMotionStudio.exe --smoke-test` (exit code 0
means success). It never connects to a robot.
The desktop log parser is included in `log_parser.py`; no separate
graph-generator checkout or Git submodule is required to run, test or package the
log page. The retired `Vibe_UR_Graph_generator/` directory is ignored if present,
so pre-existing local edits can be kept without maintaining a second repository.

Alternatively, run the repository's **Windows desktop executable** workflow from
GitHub Actions and download its artifact. It runs mocked desktop/simulator tests
before packaging. This workflow is manual and must be run after these files are
committed to the repository.

Windows executables must be built and tested on Windows; PyInstaller is not a
cross-compiler ([PyInstaller documentation](https://pyinstaller.org/en/latest/)).
Before distributing, launch the EXE on a clean Windows machine and check a local
log, a robot connection and the simulator. Automated packaging checks run offline
and do not verify communication with a physical robot.

## Existing simulator

A desktop, read-only live viewer for Universal Robots. **UR15 is selected by default**;
UR3, UR5, UR10, UR3e, UR5e, UR7e, UR8 Long, UR10e, UR12e, UR16e, UR18, UR20
and UR30 are also selectable. All 14 include offline model meshes and nominal
kinematics from the [official UR robot description](https://github.com/UniversalRobots/Universal_Robots_ROS2_Description/tree/6662e15f32c23c12ece57d0050aee9a716e4fc41/config).

**Compare robot sizes** on the connection screen lists published reach and
payload for 22 model names across legacy CB, e-Series, UR Series and Gen 7.
The selected model's reach/payload also appears below the model selector.
The reference was checked on 9 October 2026 against the [current UR lineup](https://www.universal-robots.com/products/robot-arms/),
[manufacturer datasheets](https://www.universal-robots.com/manuals/EN/HTML/MainLanding/Content/Landingpages/LandingSheets.htm)
and [legacy CB brochure](https://www.universal-robots.com/media/1801258/eng_199901_ur_main-product_brochure_web_1.pdf).

The eight newer Gen 7 names are **reference only**, since their model-specific
geometry is absent from that official robot-description release. They cannot
be selected for simulation. The UR3e-540 and UR7e-920 product pages also disagree
with their linked legacy datasheets on reach; the catalog marks this discrepancy
and retains the verified legacy dimensions. Reach ratings are not used to scale
or invent robot kinematics. Extended payload ratings depend on the manufacturer's
operating conditions; this viewer does not enforce robot load limits.

The desktop follows the supplied dark reference screenshots throughout: near-black
canvases, navy panels, fine slate borders, cyan focus and selection, and compact
native controls. Connection setup, the robot catalog, simulator, Status, View
options, all log tabs, flight playback, and application confirmation/error dialogs
share `ui_theme.py` tokens and components. Charts retain distinct series colors;
recorded actual/target traces remain blue and lime. The theme uses the existing
Pillow dependency and ships inside the Windows executable.

Tab navigation and visible focus remain available throughout. Enter activates
the focused dialog action; Escape cancels confirmations. Compact windows keep
actions available and scroll longer settings panels vertically. File-open/save
dialogs retain the operating system's file-navigation and overwrite behavior;
their appearance varies by platform. The separate A4 PDF palette and template
remain unchanged by the desktop theme.

## Flight reports, graphs and recorded playback

Open **Flight reports & playback** on the connection screen (or **Flight reports**
in the live viewer). No robot connection is required. Use **Choose folder…** beside
**Working folder** to list the recording ZIPs and real-time CSVs in that directory,
including verified downloads in its robot subfolders. **Reload folder** discovers
new files. Select `assets/flightreports/` to browse the supplied recordings.
Use **Open…** for another recording ZIP or a real-time CSV. The flight selector
above the graphs switches all charts, metadata, script context and playback
together. Failed loads retain the previous flight. Changing the working folder
clears the previous recording and playback, and synchronizes the retrieval window.

The workspace follows the supplied screenshots and the
[UR Log Viewer flight-record workflow](https://www.universal-robots.com/articles/ur/robot-care-maintenance/ur-log-viewer-manual/):
select one of six joints and view position, velocity, acceleration, current,
torque, temperature, voltage and control mode. **One joint → All readings**
shows synchronized, vertically scrollable charts; choose a single metric for a
larger graph. **Compare joints** overlays one measurement for the ticked joints
on a shared scale. Choose **Actual**, **Target**, or **Actual + target**; each
joint has a fixed color, with solid actual and dashed target traces. Use the plot toolbar to
zoom and **Reset zoom** to restore the full recording. Hover while paused or
click a graph to move the shared playhead. **Play**, **Restart**, the timeline
slider and speed control drive the robot preview at the top right.

Torque uses the recorded `Target moment j0–j5 [Nm]` channels in the supplied
recordings. These are target joint torques, as described in the
[UR RTDE guide](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html).
Actual torque remains unavailable unless an explicit actual moment/torque channel
exists; the application never estimates it from current. Temperature and voltage
use the recorded joint channels, and control mode retains its numeric code.
The playhead readout shows the selected joints at the same sample; switching
view mode or metric preserves playback time. Missing selected series stay blank.

The five supplied `recording*.zip` files each contain 15,000 samples over
29.998 seconds at 500 Hz. Their `files/realtimedata.csv` headers define units:
joint positions in radians, velocity in rad/s, current in A, and TCP position
in metres with axis-angle rotation vectors in radians. The graph time axis is
controller uptime, and playback uses elapsed time from the first sample.
Incident wall-clock time is shown separately; it is not used to invent sample
dates. Script context follows the recorded `Script control line` field.

Actual acceleration is a finite-difference derivative of measured velocity
unless a recorded actual-acceleration channel exists. Missing/invalid samples
and timestamp discontinuities remain gaps. TCP rotations interpolate using
quaternion SLERP; wrapped joint angles interpolate across the angular boundary.
The preview uses recorded joint angles in robot-base coordinates and nominal
DH geometry, with the supplied recordings matching UR10e dimensions. Factory
calibration is not applied. If TCP channels are missing, any nominal flange
pose is explicitly labeled as derived; unknown arm models are not guessed.
No flight path or unrecorded current-limit envelope is generated.

**Export PDF…** saves a vector PDF for the selected flight, covering the entire
recording and all six joints regardless of graph zoom or selected joint. It
adapts the supplied A4 Program Check template: summary, checklist, source
observations, full-record charts, and action summary. Static script call counts
are evidence, not proof that branches executed or an automated compliance
verdict. Dynamic blend overlap and application-specific acceleration limits
require review. The report uses Motion Studio identification rather than
presenting the output as a manufacturer-issued assessment.
The PDF retains its position/velocity/acceleration/current chart template;
additional measurements and joint overlays are available in the interactive
workspace and can be saved as images with the chart toolbar.

### How to use the application

Choose **How to** on the connection, live, log, flight or retrieval screen, or
press **F1** anywhere in the application. The searchable guide works offline and
is bundled into Windows builds. Topics cover connection and SSH trust, the live
simulator, Status, View options, the robot catalog, logs and filters, backups,
flight playback, joint comparison, robot flight retrieval, exports, shortcuts
and troubleshooting. Search matches both topic names and instructions; Escape
closes the guide without closing the current workspace.
**Ctrl+1** returns to the simulator/connection window, **Ctrl+2** opens logs,
**Ctrl+3** opens flights and **Ctrl+4** opens Program Check. Workspace shortcuts
wait until any modal confirmation has closed. Dialogs offer **Copy details**;
**Logs & graphs → Status details…** keeps long transfer errors readable and copyable.

`psm.ura` holds program runtime/invocation statistics; `robot_configuration*.zip`
contains configuration rather than timestamped flight telemetry. Neither is
offered as an animated recording. No archive scripts are executed or extracted.
Parsing and PDF generation run in background workers; recently loaded flights
are cached. Limits are 256 MiB per telemetry file, 250,000 samples, 256 columns,
and 4 MiB per context file, with visible errors instead of silent truncation.
Recording ZIPs are user data and are not bundled into the Windows executable;
select their folder with **Choose folder…** or use **Open…** for a single file.

### Retrieve reports from a connected robot

Connect the main viewer to the robot, open **Flight reports**, then choose
**Retrieve from robot…**. The retrieval window uses the active robot address and SSH
credentials, with the same host-key verification as existing transfers. Enter
SSH credentials there if the connection was opened without them. Credentials
remain in memory. The default remote folder, `/root/flightreports`, is evidenced
by the supplied recordings and UR's service documentation; it can be edited
for a configured robot installation.

Choose **Refresh reports** to list recording filenames, flight dates or modified
times, sizes and local download status. Ctrl/Shift selects multiple rows;
**Download selected** retrieves those files and **Download all new** skips
unchanged, verified local copies in the selected working folder. **Choose folder…**
changes that folder in both retrieval and graphs. Refresh after switching folders:
a report downloaded into another folder is considered new here. Download status
remains separated by verified source robot identity within each folder.
Downloaded and validated reports appear automatically in the flight selector.
Discovery, transfer, validation and parsing run outside the UI thread. Per-file
and overall progress include transfer speed and an estimate when enough data
has arrived. Use **Cancel transfer** to stop, or **Retry failed** to retry a
failed/cancelled batch. Temporary partial files are discarded; retry restarts
the file because a stable remote snapshot cannot be assumed.

Retrieval only reads the robot's reports. A single persistent SSH/SFTP connection
serves serialized requests and multiple files. Explicit refresh updates the
listing, and reconnect invalidates it. Reports are separated by verified SSH
host identity and the robot serial when readable. Download status compares
remote path, size and modification time plus a SHA-256 check of the saved local
file. SFTP does not provide a reliable remote content hash: a remote file
replaced with identical size and modification time cannot be distinguished
without downloading it again. Files are checked before and after transfer,
validated as ZIP/telemetry, flushed to disk and published atomically. Existing
valid files survive failed transfers. Only validated, registered files appear
in the local library. Downloads stop on changed metadata, invalid data or disk
errors; transient connection failures receive two retries with bounded backoff.

### Transfer performance verification

Logs, backups and flight retrieval share bounded SFTP pipelining: at most 32
requests of 32 KiB, or 1 MiB per window. Previously, logs used serial reads while
flights and backups pipelined 16 requests. The shared helper overlaps request
waits without queueing an entire archive in memory. Growing logs still include
appended bytes within the 64 MiB limit; recordings and archives retain strict
size checks. Installation SCP already streams data and now uses a 256 KiB receive
buffer and a 60-second idle timeout, allowing large files to keep progressing.

Run the reproducible offline comparison with:

```bash
.venv/bin/python scripts/benchmark_transfer.py --rtt-ms 4 --files 4 --mib 2
```

For identical four 2 MiB files and a simulated 4 ms round-trip delay, the measured
run took **1.1325 s with serial reads**, **0.0936 s with 16 requests**, and
**0.0580 s with 32 requests** (19.53× versus serial; 1.61× versus the old pipeline).
All three variants copied 8,388,608 bytes and matched the source SHA-256 hashes.
This is an RTT-only simulation using in-memory files: it excludes SSH encryption,
network bandwidth limits, robot CPU, archive creation and disk I/O. It is not a
real-robot throughput claim. No robot was available for live verification;
real device timing and connection behavior still require an integration check.

Listing is measured separately with `scripts/benchmark_flight_listing.py`.
For five supplied filenames (52,349,926 bytes of files; no payload transferred),
five repeats at the same simulated 4 ms RTT produced median times of 24.761 ms
for names plus individual stat calls and 4.438 ms for batched attributes:
six metadata operations versus one on an already-connected session. The baseline
is a conventional alternative, since the previous app had no flight listing.
Connection setup, server work, and hashes of existing local downloads are excluded.

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
5. Click **Connect**. Left-drag the 3D view to rotate, right-drag to pan, and scroll
   to zoom. The same controls work in recorded playback. **Reset camera** clears
   panning and centers/fits the current robot pose without changing the locked
   mounting reference.

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
python -m unittest discover -s tests -v
# Also exercise a real Tk window against a loopback mock RTDE robot:
MOTIONSIM_UI_TESTS=1 python -m unittest tests.test_ui -v
```

Tests use synthetic data and mocked SCP; no physical robot is contacted.

## References

- [UR RTDE protocol](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html)
- [UR nominal kinematic dimensions, including UR15](https://www.universal-robots.com/articles/ur/application-installation/dh-parameters-for-calculations-of-kinematics-and-dynamics)
- [UR installation example (PolyScope 3.2)](https://www.universal-robots.com/articles/ur/application-installation/using-a-barcode-reader-directly-with-the-robot/)
- [Official ROS driver's installation fixture (PolyScope 5.1)](https://github.com/UniversalRobots/Universal_Robots_ROS2_Driver/blob/main/.github/dockerursim/.vol/default.installation)

### Program Check reports and full-size flight playback

Use **Program Check & PDF** from the connection screen (or **Program Check** in the
live/flight workspace). Choose **Local folder → Choose folder…** to scan your
saved files, or **Robot → Pull programs…** to download the robot's program sources.
Select a **Program (URP)** from the list to generate its report automatically.
Subfolders are included and paths distinguish programs with the same name.
The matching `.script` and the URP's referenced `.installation` are loaded for
that program. **Export report…** saves the selected program's PDF.

The PDF puts issues first and combines repeated results into one entry with all
affected script lines. Different values, limits, units and literal/estimated
results stay separate. Larger text and grouped source-value tables make the
report easier to scan; shared assumptions and references appear at the end.

The Robot option uses the existing SSH credentials when available, or accepts an
IP, SSH user and password in the report page. Choose where to save the download;
a new folder preserves the robot's `/programs` hierarchy (with `/program` as a
fallback). Only URP, script and installation sources are downloaded. Existing
files and robot contents are preserved. SSH host verification and cancellable,
pipelined transfer are shared with the other robot downloads. Failed or cancelled
downloads discard the incomplete snapshot; completed snapshots remain available
through Local folder. Live telemetry is not required.

**Advanced…** is closed by default. It contains the guidance profile, optional
joint/TCP velocity and acceleration overrides, and alternate ZIP / TAR backup or manual file
selection. **Generate report** applies changed options to the selected program. Installation
matching follows the URP's declared path within the supplied files, or the unique
installation beside the program if no path is declared. Saved payload/TCP values
and source hashes appear in the findings; ambiguous or missing installations are
reported as a visible warning. The report continues without installation settings;
script-derived payload/TCP and motion checks still run. A missing matching script
produces an explicitly limited metadata review. Nothing executes the source files
or changes the robot.

Use **Advanced… → Backup / configuration archive…** to review a `full-backup.tar`
directly. ZIP, TAR and compressed TAR sources are read without extraction;
missing installation references remain visible as review findings.

This is a static source review: it resolves bounded numeric expressions, aliases,
pose variables and numeric struct fields without executing URScript. Where runtime
flow is uncertain, unique unconditional source initializers produce explicitly
labelled estimates; their initialization and runtime values are not certified.
The report checks velocity and
acceleration inputs for MoveJ/L/P/C, OptiMove, speed and servo commands according
to their parameter semantics. Blank velocity specifications remain unassessed;
blank acceleration specifications use the selected UR guidance where applicable.
Duration-controlled moves and unused servo parameters require runtime review.
The automatic CB/e-Series acceleration recommendations are 800°/s² for joints
and 2.5 m/s² for linear moves. UR configures joint/tool velocity limits per robot
and Normal/Reduced safety mode; command defaults and undocumented serialized
installation fields are not interpreted as universal or active safety limits.

For resolvable Cartesian waypoints it calculates straight or circular move
distances, flags radii exceeding incoming/outgoing distances, and checks adjacent
radius sums for potential overlap. It reports the total **known source distance**
with segment coverage, including offsets of the same named rigid frame;
unknown initial poses, joint-space trajectories, branches,
repetitions and rotations are excluded. Runtime variables and uncertain flow stay
explicitly unresolved. Source lines, file hashes and official UR references are
included in both the on-screen findings and PDF.
UR's [deployment recommendations](https://www.universal-robots.com/articles/ur/robot-care-maintenance/important-deployment-points/)
are starting points for CB-Series/e-Series, not safety limits. The report accounts
for [motion version 2](https://www.universal-robots.com/manuals/EN/HTML/SW5_22/Content/prod-scriptmanual/all_scripts/motion_version_desc.htm)
when explaining why static overlap does not prove a skipped waypoint. It never
executes a program or certifies deployment safety.

**Export measurements** in Flight reports remains the separate recorded-telemetry
PDF. The flight workspace now defaults to one full-width Position graph; choose
**All readings** for the stacked measurements, or **Compare joints** to overlay
one measurement across selected joints. **Sample details** and **Recording & script**
hold the detailed readouts. **Open simulator** animates the recorded robot at full
size in the main window, with shared play/pause, speed and scrubbing controls.
In the **Simulator** tab, **Motion source → Live robot / Flight report** switches
between the two views. Live telemetry continues independently; switching back to
live pauses recorded playback and preserves its position. Offline, Live robot
offers connection setup. Script-line context remains pinned below recorded playback.
Missing model/joint data stays
unavailable rather than being guessed.

In **Compare joints**, **Graphs → Separate joints** gives each selected joint its
own graph with a shared time axis; **Combined** overlays them again.
Choose **Scale → Per joint** to see each joint’s variation, or **Shared** to
compare values using identical vertical scales.
The selection, measurement, actual/target choice and playback time are preserved.
Recorded playback defaults to **TCP mounting**. Its **Align view to** selector
switches between TCP mounting and Robot base. TCP alignment captures a fixed
reference when selected (or at the first valid sample), so the reference does
not move while playback runs. This changes only the visualization.
