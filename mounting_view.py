"""PolyScope-style mounting buttons: one display-only adjustment shared by every robot view."""
import math
import tkinter as tk
from tkinter import ttk

from view import mounting_override, set_mounting_override

STEP = 45  # degrees, as PolyScope's Installation → Mounting arrows


def installation_shown(app, mode, source):
    """Installation of the robot a simulator surface shows: the loaded recording's or the live robot's."""
    if mode == 'Flight report':
        report = source.report if source else None
        return report.installation if report else None
    return app.installation_data


class MountingControls(ttk.Frame):
    def __init__(self, parent, installation):
        """installation() returns the shown robot's InstallationData, or None."""
        super().__init__(parent, padding=(12, 0, 12, 8), style='Toolbar.TFrame')
        self.installation = installation
        self.angles = (tk.StringVar(), tk.StringVar())
        self.state_text = tk.StringVar()
        self.drawn = None
        ttk.Label(self, text='Mounting', style='Panel.TLabel').pack(side='left', padx=(0, 10))
        for index, name in enumerate(('Base', 'Tilt')):
            ttk.Label(self, text=name, style='Muted.Panel.TLabel').pack(side='left', padx=(0, 6))
            for text, delta in (('− 45°', -STEP), ('+ 45°', STEP)):
                ttk.Button(self, text=text, width=5,
                           command=lambda index=index, delta=delta: self.step(index, delta)).pack(side='left', padx=(0, 4))
            entry = ttk.Entry(self, textvariable=self.angles[index], width=6, justify='right')
            entry.pack(side='left')
            entry.bind('<Return>', self.apply)
            entry.bind('<FocusOut>', self.apply)
            ttk.Label(self, text='°', style='Panel.TLabel').pack(side='left', padx=(3, 14))
        self.reset = ttk.Button(self, text='Use saved', command=lambda: set_mounting_override(self, None))
        self.reset.pack(side='left')
        ttk.Label(self, textvariable=self.state_text, style='Muted.Panel.TLabel').pack(side='left', padx=(12, 0))
        self.mounting_changed()

    def shown(self):
        """Base and tilt being drawn, in degrees: the adjustment, else the saved mounting, else upright."""
        data = self.installation()
        angles = mounting_override(self) or (data.mounting if data else None) or (0., 0.)
        return [round(math.degrees(angle), 6) % 360 for angle in angles]

    def mounting_changed(self, restore=False):
        """Follow the adjustment, or the saved mounting when the shown robot changes."""
        data = self.installation()
        adjusted = mounting_override(self) is not None
        drawn = [f'{angle:g}' for angle in self.shown()]
        if drawn != self.drawn or restore:  # Leave a half-typed angle alone while playback refreshes.
            self.drawn = drawn
            for variable, text in zip(self.angles, drawn):
                variable.set(text)
        self.reset.configure(state='normal' if adjusted else 'disabled')
        self.state_text.set('Adjusted · display only · every robot view' if adjusted else
                            'Saved installation' if data and data.mounting is not None else
                            'No saved mounting · shown upright')

    def step(self, index, delta):
        angles = self.shown()
        angles[index] += delta
        set_mounting_override(self, [math.radians(angle % 360) for angle in angles])

    def apply(self, _event=None):
        texts = [variable.get().strip() for variable in self.angles]
        if texts == self.drawn:
            return
        try:
            angles = [float(text) for text in texts]
            if not all(map(math.isfinite, angles)):
                raise ValueError(texts)
        except ValueError:
            self.mounting_changed(restore=True)
            return
        set_mounting_override(self, [math.radians(angle % 360) for angle in angles])
