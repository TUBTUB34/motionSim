"""Launch the read-only UR robot viewer with python main.py."""
import math
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

from connection import make_settings
from kinematics import MODELS, VIEW_FRAMES
from session import RobotSession
from view import RobotView

PAYLOAD_NOTICE = "Payload info will not be loaded without both a username and password."

class SimulatorApp:
    def __init__(self, root):
        self.root = root
        self.session = None
        self.settings = None
        self.installation_data = None
        self.last_sample = None
        root.title("UR Robot Simulator")
        root.geometry("650x640")
        root.minsize(600, 580)
        self.ip = tk.StringVar()
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.installation = tk.StringVar()
        self.directory = tk.StringVar(value="/programs")
        self.model = tk.StringVar(value="UR15")
        self.view_frame = tk.StringVar(value=VIEW_FRAMES[0])
        self.notice = tk.StringVar(value=PAYLOAD_NOTICE)
        self.error = tk.StringVar()
        self.status = tk.StringVar(value="Disconnected")
        self.details = tk.StringVar()
        self.details_view = None
        self.details.trace_add("write", self.update_details)
        self.joints = tk.StringVar()
        self.tcp_text = tk.StringVar()
        self.username.trace_add("write", self.update_notice)
        self.password.trace_add("write", self.update_notice)
        self.closed = False
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.show_setup()
        self.poll_id = self.root.after(33, self.poll)

    def clear(self):
        self.details_view = None
        for widget in self.root.winfo_children():
            widget.destroy()

    def update_details(self, *_):
        if self.details_view is not None:
            self.details_view.configure(state="normal")
            self.details_view.delete("1.0", "end")
            self.details_view.insert("1.0", self.details.get())
            self.details_view.configure(state="disabled")

    def update_notice(self, *_):
        available = self.username.get().strip() and self.password.get()
        self.notice.set("" if available else PAYLOAD_NOTICE)

    def show_setup(self):
        self.clear()
        self.root.geometry("650x640")
        self.root.minsize(600, 580)
        panel = ttk.Frame(self.root, padding=28)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="UR Robot Simulator", font=("TkDefaultFont", 20, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(panel, text="Connect to see your robot’s live joint positions in 3D.").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 20))
        fields = [
            ("Robot IP address", self.ip), ("Username (optional)", self.username),
            ("Password (optional)", self.password), ("Installation name", self.installation),
            ("Installation folder", self.directory),
        ]
        for row, (label, variable) in enumerate(fields, start=2):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=8)
            entry = ttk.Entry(panel, textvariable=variable, show="*" if variable is self.password else "")
            entry.grid(row=row, column=1, sticky="ew", pady=8)
            if variable is self.ip:
                entry.focus_set()
        ttk.Label(panel, text="Robot model").grid(row=7, column=0, sticky="w", pady=8)
        ttk.Combobox(panel, textvariable=self.model, values=tuple(MODELS), state="readonly").grid(
            row=7, column=1, sticky="ew", pady=8)
        ttk.Label(panel, text="Blank name uses default.installation. Blank folder uses /programs.",
                  wraplength=550).grid(row=8, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(panel, textvariable=self.notice, wraplength=550, foreground="#805100").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Label(panel, textvariable=self.error, wraplength=550, foreground="#b42318").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(panel, text="Connect", command=self.submit).grid(row=11, column=1, sticky="e", pady=12)
        self.root.bind("<Return>", self.submit)

    def submit(self, _event=None):
        if self.session is not None:
            return
        try:
            self.settings = make_settings(self.ip.get(), self.username.get(), self.password.get(),
                                          self.installation.get(), self.directory.get(), self.model.get())
        except ValueError as error:
            self.error.set(str(error))
            return
        self.error.set("")
        self.installation.set(self.settings.installation)
        self.directory.set(self.settings.directory)
        self.installation_data = self.last_sample = None
        self.status.set("Connecting to RTDE…")
        self.details.set("Installation will load after RTDE connects." if self.settings.can_load_payload else PAYLOAD_NOTICE)
        self.joints.set("Waiting for joints…")
        self.tcp_text.set("")
        self.show_view()
        self.session = RobotSession(self.settings)
        self.session.start()

    def show_view(self):
        self.root.unbind("<Return>")
        self.clear()
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
        bar = ttk.Frame(self.root, padding=12)
        bar.pack(fill="x")
        ttk.Label(bar, text=f"{self.settings.model}  ·  {self.settings.ip}",
                  font=("TkDefaultFont", 16, "bold")).pack(side="left")
        ttk.Button(bar, text="Edit connection", command=self.edit_connection).pack(side="right", padx=6)
        self.connect_button = ttk.Button(bar, text="Disconnect", command=self.toggle_connection)
        self.connect_button.pack(side="right")
        ttk.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 10)).pack(fill="x")
        controls = ttk.Frame(self.root, padding=(12, 0, 12, 10))
        controls.pack(fill="x")
        ttk.Label(controls, text="Align view to:").pack(side="left", padx=(0, 8))
        self.frame_selector = ttk.Combobox(controls, textvariable=self.view_frame,
                                           values=VIEW_FRAMES, state="readonly", width=20)
        self.frame_selector.pack(side="left")
        self.frame_selector.bind("<<ComboboxSelected>>", self.change_view_frame)
        ttk.Label(controls, text="Grid origin and XYZ axes follow the selected frame.").pack(side="left", padx=12)
        content = ttk.Frame(self.root)
        content.pack(fill="both", expand=True)
        self.viewer = RobotView(content, self.settings.model)
        self.viewer.pack(side="left", fill="both", expand=True)
        self.viewer.set_view_frame(self.view_frame.get())
        sidebar = ttk.Frame(content, padding=16, width=300)
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="Joint positions (degrees)", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(sidebar, textvariable=self.joints, font=("TkFixedFont", 11), justify="left").pack(anchor="w", pady=(8, 18))
        ttk.Label(sidebar, text="Live TCP in robot base (m / rad)", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        ttk.Label(sidebar, textvariable=self.tcp_text, justify="left").pack(anchor="w", pady=(8, 18))
        ttk.Label(sidebar, text="Installation", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(sidebar, text=self.settings.remote_path, wraplength=265).pack(anchor="w", pady=6)
        self.details_view = ScrolledText(sidebar, width=28, height=4, wrap="word",
                                         font=("TkDefaultFont", 10), relief="flat", padx=6, pady=6)
        self.details_view.pack(fill="both", expand=True, pady=6)
        self.update_details()
        ttk.Button(sidebar, text="Reset camera", command=self.viewer.reset_camera).pack(anchor="w", pady=16)
        ttk.Label(self.root, text="Nominal arm geometry · Read-only visualization · Saved payload is not a dynamics simulation",
                  padding=10).pack(fill="x")

    def change_view_frame(self, _event=None):
        self.viewer.set_view_frame(self.view_frame.get())

    def disconnect(self, message="Disconnected · Last received pose retained"):
        if self.session:
            self.session.stop()
            self.session = None
        self.status.set(message)
        self.viewer.set_stream_state("Disconnected · Last received pose")
        self.connect_button.configure(text="Reconnect")

    def toggle_connection(self):
        if self.session:
            self.disconnect()
        else:
            self.submit()

    def edit_connection(self):
        self.disconnect()
        self.show_setup()

    def describe_installation(self, data):
        lines = []
        if data.tcp is not None:
            lines.append(f"TCP: {data.tcp_name or 'active'}")
            lines.append("Offset (m): " + ", ".join(f"{x:.4f}" for x in data.tcp[:3]))
            lines.append("Rotation vector (rad): " + ", ".join(f"{x:.3f}" for x in data.tcp[3:]))
        if data.payload_mass is not None:
            name = f" ({data.payload_name})" if data.payload_name else ""
            lines.append(f"Payload: {data.payload_mass:g} kg{name}")
        if data.payload_cog is not None:
            lines.append("CoG at flange (m): " + ", ".join(f"{x:.4f}" for x in data.payload_cog))
        if data.mounting is not None:
            base, tilt = (math.degrees(x) for x in data.mounting)
            lines.append(f"Mounting: base {base:.1f}°, tilt {tilt:.1f}°")
        lines.extend(data.warnings)
        lines.append("Saved settings may differ from the running program.")
        return "\n\n".join(lines)

    def poll(self):
        session = self.session
        if session is not None:
            while self.session is session:
                try:
                    kind, data = session.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "connected":
                    self.status.set("Connected · Receiving live joints")
                    if self.settings.can_load_payload:
                        self.details.set("Downloading installation via SCP…")
                elif kind == "error":
                    self.disconnect("Connection lost / failed: " + data)
                elif kind == "installation_error":
                    self.details.set(data + "\n\nShowing robot base coordinates; saved TCP and payload unavailable.")
                elif kind == "installation":
                    self.installation_data = data
                    self.details.set(self.describe_installation(data))
                    if self.last_sample:
                        self.viewer.update_robot(self.last_sample, data)
                elif kind == "host_key":
                    data.accepted = messagebox.askyesno("Verify robot SSH host",
                        f"First SSH connection to {data.hostname}.\n\nHost fingerprint:\n{data.fingerprint}\n\n"
                        "Verify this fingerprint with your robot administrator. Trust it for this connection?",
                        parent=self.root)
                    data.answered.set()
            if self.session is session:
                sample = session.sample()
                if sample and sample is not self.last_sample:
                    self.last_sample = sample
                    self.viewer.update_robot(sample, self.installation_data)
                    names = ("Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3")
                    self.joints.set("\n".join(f"{name:9} {math.degrees(q):8.2f}°" for name, q in zip(names, sample.joints)))
                    self.tcp_text.set("\n".join(f"{name:2}  {v: .5f}" for name, v in zip(("X", "Y", "Z", "RX", "RY", "RZ"), sample.tcp_pose)))
                if sample:
                    age = time.monotonic() - sample.received_at
                    self.viewer.set_stream_state("Live joint geometry" if age < 1 else "Stale · Last received pose")
                    self.status.set(f"Live · RTDE · data age {age*1000:.0f} ms" if age < 1
                                    else f"Stale data · No update for {age:.1f} s · Last pose retained")
        if not self.closed:
            self.poll_id = self.root.after(33, self.poll)

    def close(self):
        self.closed = True
        if self.session:
            self.session.stop()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    SimulatorApp(root)
    root.mainloop()
