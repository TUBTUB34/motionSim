"""Chart styling shared by every chart page: joint colours, theme palette and panel layout."""
from matplotlib.lines import Line2D

from motionstudio.core.rendering import serialized_render
from motionstudio.ui import theme

JOINT_COLORS = ('#53b8f2', '#e8ac58', '#ad91ef', '#66d3b4', '#ef819a', '#d7d676')
LIGHT_JOINT_COLORS = ('#006da5', '#996000', '#7751b5', '#16765d', '#b53760', '#757314')


def joint_colors():
    return LIGHT_JOINT_COLORS if theme.MODE == 'light' else JOINT_COLORS


def chart_palette():
    """Snapshot the colors an interactive figure currently uses."""
    palette = {name: getattr(theme, name) for name in
               ('BACKGROUND', 'PANEL', 'FIELD', 'BORDER', 'INK', 'MUTED', 'ACTUAL', 'TARGET', 'DANGER')}
    palette.update({f'joint{index}': color for index, color in enumerate(joint_colors())})
    palette.update({f'log{index}': color for index, color in enumerate(theme.CHART_SERIES)})
    return palette


@serialized_render
def retheme_figure(figure, previous):
    """Recolor existing artists without replacing traces, limits or zoom history."""
    from matplotlib.collections import Collection
    from matplotlib.colors import to_rgb, to_rgba
    from matplotlib.patches import Patch
    from matplotlib.text import Text
    current = chart_palette()
    replacements = {to_rgb(color): current[name] for name, color in previous.items()}

    def recolor(value):
        try:
            rgba = to_rgba(value)
        except (ValueError, TypeError):
            return value
        replacement = replacements.get(rgba[:3])
        if replacement is None:
            return value
        return replacement if isinstance(value, str) and rgba[3] == 1 else (*to_rgb(replacement), rgba[3])

    for artist in figure.findobj():
        if isinstance(artist, Text):
            artist.set_color(recolor(artist.get_color()))
        elif isinstance(artist, Line2D):
            artist.set_color(recolor(artist.get_color()))
            artist.set_markerfacecolor(recolor(artist.get_markerfacecolor()))
            artist.set_markeredgecolor(recolor(artist.get_markeredgecolor()))
        elif isinstance(artist, Collection):
            artist.set_facecolors([recolor(color) for color in artist.get_facecolors()])
            artist.set_edgecolors([recolor(color) for color in artist.get_edgecolors()])
        elif isinstance(artist, Patch):
            artist.set_facecolor(recolor(artist.get_facecolor()))
            artist.set_edgecolor(recolor(artist.get_edgecolor()))
    # New tick labels created after panning also need the active palette.
    for axis in figure.axes:
        axis.tick_params(colors=theme.MUTED, grid_color=theme.BORDER)
    return current


class StackedPanels:
    """Fixed pixel gaps between vertically stacked panels; needs ``figure``, ``axes`` and ``_layout_key``."""

    def _layout(self):
        height = max(150., self.figure.bbox.height)
        key = (self.figure.bbox.width, self.figure.bbox.height, len(self.axes))
        if key == self._layout_key:
            return False
        self._layout_key = key
        bottom = min(.48, 46. / height)
        top = 1 - min(.2, 34. / height)
        # A fixed pixel gap keeps every card's title and time ticks clear of its neighbours.
        count = len(self.axes)
        room = max(40., (height * (top - bottom) - 58. * (count - 1)) / max(count, 1))
        self.figure.subplots_adjust(left=min(.2, 78. / max(self.figure.bbox.width, 1.)), right=.985, top=top,
                                    bottom=bottom, hspace=58. / room if count > 1 else .15)
        return True
