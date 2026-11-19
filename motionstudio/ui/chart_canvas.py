"""The Tk canvas every chart page draws into: Agg rendering, scroll-friendly repaints."""
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from motionstudio.core.rendering import serialized_render
from motionstudio.ui import theme


class ChartCanvas(FigureCanvasTkAgg):
    """Set ``charts`` to an object with ``axes`` and ``_layout()`` to lay panels out before each draw.

    A page that scrolls a tall canvas sets ``visible_region`` to a callable returning the
    on-screen part as a display-space Bbox, so repairs copy that part instead of the
    whole image.

    Setting ``content_height`` (viewport height -> figure height) makes the canvas
    scroll itself: the figure keeps that height and Tk shows the scrolled part of the
    image natively, so scrolling moves no windows and renders nothing. Use the Tk
    widget's ``yview``/``yscrollcommand`` like any scrolled canvas.
    """
    visible_region = None
    content_height = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_tk_widget().configure(background=theme.BACKGROUND, highlightthickness=0, borderwidth=0)
        self._repaint_id = None
        self.get_tk_widget().bind('<Expose>', self.refresh_pixels, add='+')

    def resize(self, event):
        height = event.height if self.content_height is None else self.content_height(event.height)
        # Moving the embedded window while scrolling also emits Configure.
        # Rebuilding the PhotoImage at unchanged dimensions flashes blank strips.
        if (event.width, height) == (self._tkphoto.width(), self._tkphoto.height()):
            self.refresh_pixels()
            return
        self._resize_figure_for_canvas_size(event.width, height)
        if self.content_height is not None:
            self.get_tk_widget().configure(scrollregion=(0, 0, event.width, height))

    def fit_content(self):
        """Apply a changed ``content_height`` (e.g. a different panel count) at the current widget size."""
        widget = self.get_tk_widget()
        if widget.winfo_width() > 1:
            self.resize(type('Size', (), {'width': widget.winfo_width(), 'height': widget.winfo_height()}))

    def refresh_pixels(self, _event=None):
        """Repair newly exposed Tk pixels from Agg, without rendering traces again."""
        if self._repaint_id is None:
            self._repaint_id = self.get_tk_widget().after_idle(self._refresh_pixels)

    @serialized_render
    def _refresh_pixels(self):
        self._repaint_id = None
        if self.get_tk_widget().winfo_exists() and hasattr(self, 'renderer'):
            self.blit(self.visible_region() if self.visible_region is not None else None)

    @serialized_render
    def draw(self):
        charts = getattr(self, 'charts', None)
        if charts is not None and charts.axes:
            # Set margins before drawing, including Tk's idle resize draws.
            # Scheduling another draw from draw_event can be coalesced away.
            charts._layout()
        return super().draw()
