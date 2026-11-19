"""The Tk canvas every chart page draws into: Agg rendering, scroll-friendly repaints."""
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from motionstudio.core.rendering import serialized_render
from motionstudio.ui import theme


class ChartCanvas(FigureCanvasTkAgg):
    """Set ``charts`` to an object with ``axes`` and ``_layout()`` to lay panels out before each draw."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_tk_widget().configure(background=theme.BACKGROUND, highlightthickness=0, borderwidth=0)
        self._repaint_id = None
        self.get_tk_widget().bind('<Expose>', self.refresh_pixels, add='+')

    def resize(self, event):
        # Moving the embedded window while scrolling also emits Configure.
        # Rebuilding the PhotoImage at unchanged dimensions flashes blank strips.
        if (event.width, event.height) == (self._tkphoto.width(), self._tkphoto.height()):
            self.refresh_pixels()
            return
        return super().resize(event)

    def refresh_pixels(self, _event=None):
        """Repair newly exposed Tk pixels from Agg, without rendering traces again."""
        if self._repaint_id is None:
            self._repaint_id = self.get_tk_widget().after_idle(self._refresh_pixels)

    @serialized_render
    def _refresh_pixels(self):
        self._repaint_id = None
        if self.get_tk_widget().winfo_exists() and hasattr(self, 'renderer'):
            self.blit()

    @serialized_render
    def draw(self):
        charts = getattr(self, 'charts', None)
        if charts is not None and charts.axes:
            # Set margins before drawing, including Tk's idle resize draws.
            # Scheduling another draw from draw_event can be coalesced away.
            charts._layout()
        return super().draw()
