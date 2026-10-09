"""Grouped, scrollable read-only controller telemetry for the desktop UI."""
from tkinter import ttk

from telemetry import (BASE_FIELDS, STATUS_FIELDS, ROBOT_FLAGS, SAFETY_FLAGS,
                       ROBOT_MODES, SAFETY_MODES, RUNTIME_STATES, enum_text, format_value)


class StatusView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=16)
        ttk.Label(self, text="Controller status", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="Live RTDE outputs · Unsupported fields are marked unavailable",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))
        summaries = ttk.Frame(self)
        summaries.pack(fill="x", pady=(0, 14))
        self.summary_labels = {}
        for column, (key, title) in enumerate((("robot_mode", "ROBOT"),
                                               ("safety_status", "SAFETY"), ("runtime_state", "PROGRAM"))):
            panel = ttk.Frame(summaries, padding=(0, 4, 24, 4))
            panel.grid(row=0, column=column, sticky="ew")
            summaries.columnconfigure(column, weight=1)
            ttk.Label(panel, text=title, style="Muted.TLabel").pack(anchor="w")
            label = ttk.Label(panel, text="Waiting for data", style="Title.TLabel", wraplength=270)
            label.pack(anchor="w", pady=(4, 0))
            self.summary_labels[key] = label
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("value",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Signal")
        self.tree.heading("value", text="Controller value")
        self.tree.column("#0", width=310, minwidth=240, stretch=False)
        self.tree.column("value", width=720, minwidth=620)
        vertical = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree.tag_configure("group", background="#dce6ee", foreground="#17334a", font=("TkDefaultFont", 10, "bold"))
        self.tree.tag_configure("unavailable", foreground="#667787")
        self.fields = BASE_FIELDS + STATUS_FIELDS
        self.previous = {}
        for group in ("Controller", "Safety", "Motion", "Joints", "TCP", "Electrical", "I/O", "Tool", "Payload"):
            self.tree.insert("", "end", iid=group, text=group, open=True, tags=("group",))
        for field in self.fields:
            self.tree.insert(field.group, "end", iid=field.name, text=field.label, values=("Waiting for data",))
            labels = ROBOT_FLAGS if field.name == "robot_status_bits" else SAFETY_FLAGS if field.name == "safety_status_bits" else ()
            for bit, label in enumerate(labels):
                self.tree.insert(field.name, "end", iid=f"{field.name}.{bit}", text=label, values=("Waiting for data",))
        ttk.Label(self, text="Values are the last received snapshot. Check connection freshness above before interpreting them.",
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

    def update_sample(self, sample, fresh=True):
        values = dict(sample.telemetry)
        values.update(timestamp=sample.timestamp, actual_q=sample.joints, actual_TCP_pose=sample.tcp_pose)
        for field in self.fields:
            value = values.get(field.name)
            text = format_value(field, value, values)
            if self.previous.get(field.name) != text:
                self.tree.item(field.name, values=(text,), tags=("unavailable",) if value is None else ())
                self.previous[field.name] = text
                for child in self.tree.get_children(field.name):
                    bit = int(child.rsplit(".", 1)[1])
                    self.tree.item(child, values=("Unavailable" if value is None else "Yes" if value & (1 << bit) else "No",))
        for key, mapping in (("robot_mode", ROBOT_MODES), ("safety_status", SAFETY_MODES), ("runtime_state", RUNTIME_STATES)):
            value = values.get(key)
            if key == "safety_status" and value is None:
                value = values.get("safety_mode")
            text = enum_text(value, mapping)
            color = "#17334a"
            if key == "safety_status" and value is not None:
                color = "#16704c" if value == 1 else "#a94813"
            if not fresh:
                text = "Last: " + text
                color = "#667787"
            self.summary_labels[key].configure(text=text, foreground=color)
