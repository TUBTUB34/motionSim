"""Full-size recorded robot playback in the application's main window."""
import tkinter as tk
from tkinter import ttk
from view import RobotView
from flight_motion import sample_at


class RecordedPlayback(ttk.Frame):
    def __init__(self, parent, source):
        super().__init__(parent)
        self.source = source
        self.robot = None
        self.report = None
        self.setting_scale = False
        self.mounting = tk.StringVar(value='TCP mounting')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self, padding=(16, 10), style='Toolbar.TFrame')
        bar.grid(row=0, column=0, sticky='ew')
        ttk.Label(bar, text='RECORDED PLAYBACK', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(bar, text='Flight charts', command=self.show_charts).pack(side='right')
        ttk.Button(bar, text='Reset camera', command=self.reset_camera).pack(side='right', padx=8)
        alignment = ttk.Frame(bar, style='Toolbar.TFrame')
        alignment.pack(side='left', padx=(20, 0))
        ttk.Label(alignment, text='Align view to', style='Panel.TLabel').pack(side='left', padx=(0, 8))
        self.mounting_selector = ttk.Combobox(alignment, textvariable=self.mounting,
                values=('TCP mounting', 'Robot base'), state='readonly', width=16)
        self.mounting_selector.pack(side='left')
        self.mounting_selector.bind('<<ComboboxSelected>>', self.change_mounting)
        self.caption = tk.StringVar()
        self.note = tk.StringVar()
        ttk.Label(self, textvariable=self.caption, padding=(16, 10), style='Muted.TLabel', wraplength=900).grid(row=1, column=0, sticky='ew')
        self.holder = ttk.Frame(self)
        self.holder.grid(row=2, column=0, sticky='nsew')
        self.holder.columnconfigure(0, weight=1)
        self.holder.rowconfigure(0, weight=1)
        self.notice = ttk.Label(self.holder, text='Load a recording in Flight reports.', anchor='center')
        self.notice.grid(row=0, column=0)
        controls = ttk.Frame(self, padding=16, style='Toolbar.TFrame')
        controls.grid(row=3, column=0, sticky='ew')
        controls.columnconfigure(3, weight=1)
        self.play = ttk.Button(controls, text='Play', command=source.toggle_play, style='Accent.TButton')
        self.play.grid(row=0, column=0)
        ttk.Button(controls, text='Restart', command=source.restart).grid(row=0, column=1, padx=8)
        speed = ttk.Combobox(controls, textvariable=source.speed, values=('0.25×', '0.5×', '1×', '2×', '4×'), state='readonly', width=6)
        speed.grid(row=0, column=2, padx=(0, 10))
        speed.bind('<<ComboboxSelected>>', source.change_speed)
        self.scale = ttk.Scale(controls, from_=0, to=1, command=self.seek)
        self.scale.grid(row=0, column=3, sticky='ew')
        ttk.Label(controls, textvariable=source.clock, width=24, anchor='e', style='Panel.TLabel').grid(row=0, column=4, padx=(10, 0))
        ttk.Label(self, textvariable=self.note, padding=(16, 8), wraplength=900, style='Muted.TLabel').grid(row=4, column=0, sticky='ew')
        self.script_position_label = ttk.Label(self, textvariable=source.script_position,
            padding=(16, 8), style='Section.Panel.TLabel', anchor='w')
        self.script_position_label.grid(row=5, column=0, sticky='ew')
        self.sync()

    def show_charts(self):
        self.source.deiconify()
        self.source.lift()

    def seek(self, value):
        if not self.setting_scale:
            self.source.scrub(value)

    def change_mounting(self, _event=None):
        if self.robot:
            self.robot.set_view_frame('Live TCP' if self.mounting.get() == 'TCP mounting' else 'Robot base')

    def reset_camera(self):
        if self.robot:
            self.robot.reset_camera()

    def sync(self):
        report = self.source.report
        if report is None:
            self.report = None
            if self.robot:
                self.robot.destroy()
                self.robot = None
            self.caption.set('No recording loaded')
            self.note.set('Choose a report in the current flight working folder.')
            self.notice.configure(text='Load a recording in Flight reports.')
            self.notice.grid()
            self.play.configure(text='Play', state='disabled')
            self.scale.configure(state='disabled')
            return
        self.scale.configure(state='normal')
        if report is not self.report:
            self.report = report
            if self.robot:
                self.robot.destroy()
                self.robot = None
            if report.model:
                self.robot = RobotView(self.holder, report.model)
                self.robot.grid(row=0, column=0, sticky='nsew')
                self.change_mounting()
                self.robot.set_stream_state('Recorded joint geometry')
            self.scale.configure(to=report.duration)
            self.caption.set(f'{report.name} · {report.model or "Unknown model"} · Recorded data; independent of live robot telemetry')
        self.setting_scale = True
        self.scale.set(self.source.timeline.elapsed)
        self.setting_scale = False
        self.play.configure(text='Pause' if self.source.timeline.playing else 'Play', state='disabled' if self.source.loading else 'normal')
        sample = sample_at(report, self.source.timeline.elapsed)
        if self.robot and sample is not None:
            self.notice.grid_remove()
            self.robot.grid()
            self.robot.update_robot(sample, None, render=bool(self.winfo_ismapped()))
            self.note.set('Left-drag: rotate · Right-drag: pan · Wheel: zoom · ' + sample.tcp_source)
        else:
            if self.robot:
                self.robot.sample = None
                self.robot.grid_remove()
            text = 'Robot model unavailable in this recording; geometry cannot be inferred.' if not report.model else 'No valid recorded motion at this time (missing data or recording gap).'
            self.notice.configure(text=text)
            self.notice.grid()
            self.note.set(text)
