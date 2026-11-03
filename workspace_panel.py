"""A persistent workspace panel, with an optional standalone host for comparison."""
import tkinter as tk
import ui_theme as theme


class WorkspacePanel(tk.Frame):
    def __init__(self, master, *, page_host=None, on_show=None, on_hide=None, **kwargs):
        background = kwargs.pop('background', None)
        self._window = None if page_host is not None else tk.Toplevel(master)
        if self._window is not None and background:
            self._window.configure(background=background)
        super().__init__(page_host if page_host is not None else self._window,
                         background=background or theme.BACKGROUND, **kwargs)
        self.embedded = page_host is not None
        self._on_show, self._on_hide = on_show, on_hide
        if not self.embedded:
            self.pack(fill='both', expand=True)

    def title(self, value):
        if self._window is not None:
            self._window.title(value)

    def geometry(self, value):
        if self._window is not None:
            self._window.geometry(value)

    def minsize(self, width, height):
        if self._window is not None:
            self._window.minsize(width, height)

    def protocol(self, name, callback):
        if self._window is not None:
            self._window.protocol(name, callback)

    def deiconify(self):
        if self._window is not None:
            self._window.deiconify()
        elif self._on_show is not None:
            self._on_show()

    def lift(self, aboveThis=None):
        if self._window is not None:
            self._window.lift(aboveThis)
        elif self._on_show is not None:
            self._on_show()

    def withdraw(self):
        if self._window is not None:
            self._window.withdraw()
        elif self._on_hide is not None:
            self._on_hide()

    def state(self, newstate=None):
        if self._window is not None:
            return self._window.state(newstate)
        return 'normal' if self.winfo_viewable() else 'withdrawn'

    def destroy(self):
        host, self._window = self._window, None
        super().destroy()
        if host is not None and host.winfo_exists():
            host.destroy()
