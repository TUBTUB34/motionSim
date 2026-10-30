"""Shared native desktop design tokens and controls from the URacle references.

The application uses dark, dense panels with fine borders and blue interaction
accents. PDF export deliberately keeps its separate print palette.
"""
import tkinter as tk
from tkinter import font, ttk
from PIL import Image, ImageDraw, ImageTk

BACKGROUND = '#090e14'
PANEL = '#101922'
FIELD = '#0b1219'
BORDER = '#263744'
INK = TEXT = '#dce6ee'
MUTED = '#91a9bb'
ACCENT = ACTUAL = '#39a9ed'
TARGET = SUCCESS = '#99d74c'
SELECTED = '#244156'
DANGER = '#f16b75'
WARNING = '#e8b45d'
HOVER = '#1a2c3b'
PRESSED = '#21435a'
DISABLED = '#667b8c'
CHART_SERIES = (ACCENT, TARGET, WARNING, '#c795d8', DANGER, '#73d4c5')
SPACING = (4, 8, 12, 16, 24)
RADIUS = 3
BORDER_WIDTH = 1
CONTROL_HEIGHT = 34
TYPE_SIZE = {'small': 9, 'body': 10, 'section': 9, 'title': 17, 'hero': 23}
# The references use borders and active outlines, not drop shadows.
SHADOW = None


def apply_theme(root):
    """Install the shared ttk/Tk palette once per interpreter, preserving focus."""
    style = ttk.Style(root)
    if getattr(root, '_studio_theme_applied', False):
        return style
    style.theme_use('clam')
    available = set(font.families(root))
    family = next((name for name in ('Segoe UI', 'Noto Sans', 'DejaVu Sans') if name in available), 'TkDefaultFont')
    font.nametofont('TkDefaultFont', root=root).configure(family=family, size=TYPE_SIZE['body'])
    font.nametofont('TkTextFont', root=root).configure(family=family, size=TYPE_SIZE['body'])
    style.configure('.', background=BACKGROUND, foreground=INK, font=(family, TYPE_SIZE['body']),
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    focuscolor=ACCENT, troughcolor=FIELD, selectbackground=SELECTED,
                    selectforeground=INK, insertcolor=INK)
    images = []

    def surface(fill, border, size=(40, CONTROL_HEIGHT), radius=RADIUS, stripe=False):
        scale = 3
        im = Image.new('RGBA', (size[0]*scale, size[1]*scale))
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((1, 1, size[0]*scale-2, size[1]*scale-2), radius=radius*scale,
                               fill=fill, outline=border, width=scale)
        if stripe:
            draw.line((radius*scale, 2*scale, (size[0]-radius)*scale, 2*scale), fill=ACCENT, width=3*scale)
        photo = ImageTk.PhotoImage(im.resize(size, Image.Resampling.LANCZOS), master=root)
        images.append(photo)
        return photo

    def element(name, normal, *states, border=4):
        style.element_create(name, 'image', normal, *states, border=border, padding=0, sticky='nsew')

    disabled = surface(PANEL, BORDER)
    element('Studio.button', surface(FIELD, BORDER), ('disabled', disabled),
            ('pressed', surface(PRESSED, ACCENT)), ('focus', surface(FIELD, ACCENT)),
            ('active', surface(HOVER, ACCENT)))
    element('Studio.accent', surface(PRESSED, ACCENT), ('disabled', disabled),
            ('pressed', surface(SELECTED, INK)), ('focus', surface(PRESSED, INK)),
            ('active', surface('#28516b', ACCENT)))
    for name, background in (('TButton', 'Studio.button'), ('Accent.TButton', 'Studio.accent')):
        style.layout(name, [(background, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, padding=(12, 6), borderwidth=0, anchor='center', background=BACKGROUND, foreground=INK)
        style.map(name, foreground=[('disabled', DISABLED)], background=[('!disabled', BACKGROUND)])
    element('Studio.field', surface(FIELD, BORDER), ('disabled', disabled),
            ('invalid', surface(FIELD, DANGER)), ('focus', surface(FIELD, ACCENT)),
            ('active', surface(FIELD, MUTED)))
    style.layout('TEntry', [('Studio.field', {'sticky': 'nsew', 'children': [
        ('Entry.padding', {'sticky': 'nsew', 'children': [('Entry.textarea', {'sticky': 'nsew'})]})]})])
    style.configure('TEntry', padding=(9, 6), fieldbackground=FIELD, insertcolor=INK)
    style.map('TEntry', foreground=[('disabled', DISABLED)], fieldbackground=[('disabled', PANEL)])
    arrow = Image.new('RGBA', (16*3, 16*3))
    ImageDraw.Draw(arrow).line([(4*3, 6*3), (8*3, 10*3), (12*3, 6*3)], fill=MUTED, width=4)
    arrow = ImageTk.PhotoImage(arrow.resize((16, 16), Image.Resampling.LANCZOS), master=root)
    images.append(arrow)
    style.element_create('Studio.arrow', 'image', arrow)
    style.layout('TCombobox', [('Studio.field', {'sticky': 'nsew', 'children': [
        ('Combobox.padding', {'sticky': 'nsew', 'children': [
            ('Studio.arrow', {'side': 'right', 'sticky': ''}),
            ('Combobox.textarea', {'sticky': 'nsew'})]})]})])
    style.configure('TCombobox', padding=(9, 6), arrowsize=16, fieldbackground=FIELD)
    style.map('TCombobox', fieldbackground=[('disabled', PANEL), ('readonly', FIELD)],
              foreground=[('disabled', DISABLED), ('readonly', INK)],
              selectbackground=[('readonly', FIELD)], selectforeground=[('readonly', INK)])
    style.configure('TSpinbox', padding=(8, 6), fieldbackground=FIELD, arrowcolor=MUTED)
    style.map('TSpinbox', bordercolor=[('focus', ACCENT)], foreground=[('disabled', DISABLED)])
    style.configure('TMenubutton', background=FIELD, foreground=INK, arrowcolor=MUTED,
                    padding=(9, 6), borderwidth=1, bordercolor=BORDER)
    style.map('TMenubutton', background=[('pressed', PRESSED), ('active', HOVER)],
              foreground=[('disabled', DISABLED)], bordercolor=[('focus', ACCENT)])
    element('Studio.tab', surface(PANEL, BORDER), ('selected', surface(FIELD, ACCENT, stripe=True)),
            ('focus', surface(PANEL, ACCENT)), ('active', surface(HOVER, MUTED)))
    style.layout('TNotebook.Tab', [('Studio.tab', {'sticky': 'nsew', 'children': [
        ('Notebook.padding', {'sticky': 'nsew', 'children': [('Notebook.label', {'sticky': 'nsew'})]})]})])
    style.configure('TNotebook', borderwidth=0, tabmargins=(0, 0, 0, 0), background=BACKGROUND)
    style.configure('TNotebook.Tab', padding=(15, 8), foreground=MUTED, background=PANEL)
    style.map('TNotebook.Tab', foreground=[('disabled', DISABLED), ('selected', INK), ('focus', ACCENT)])
    for name in ('TCheckbutton', 'TRadiobutton'):
        style.configure(name, background=BACKGROUND, foreground=INK, padding=(2, 4),
                        indicatorbackground=FIELD, indicatorforeground=INK, indicatorsize=15,
                        indicatormargin=(0, 0, 8, 0), bordercolor=BORDER)
        style.map(name, background=[('active', PANEL)], foreground=[('disabled', DISABLED)],
                  indicatorbackground=[('disabled', PANEL), ('selected', ACCENT), ('!selected', FIELD)],
                  bordercolor=[('focus', ACCENT), ('active', ACCENT)])
    style.configure('TScale', background=MUTED, troughcolor=FIELD, bordercolor=BORDER,
                    lightcolor=MUTED, darkcolor=MUTED, sliderlength=18, sliderthickness=10)
    style.map('TScale', background=[('disabled', BORDER), ('active', ACCENT)],
              bordercolor=[('focus', ACCENT)])
    for name in ('TFrame', 'TLabel'):
        style.configure(name, background=BACKGROUND)
    for name in ('Panel.TFrame', 'Toolbar.TFrame'):
        style.configure(name, background=PANEL)
    style.configure('Card.TFrame', background=PANEL, borderwidth=1, relief='solid')
    style.configure('TLabelframe', background=BACKGROUND, borderwidth=1, relief='solid', bordercolor=BORDER)
    style.configure('TLabelframe.Label', background=BACKGROUND, foreground=MUTED, font=(family, 9, 'bold'))
    style.configure('Title.TLabel', font=(family, TYPE_SIZE['title'], 'bold'), foreground=INK)
    style.configure('Hero.TLabel', font=(family, TYPE_SIZE['hero'], 'bold'), foreground=INK)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Hint.TLabel', background=FIELD, foreground=MUTED)
    style.configure('Section.TLabel', font=(family, TYPE_SIZE['section'], 'bold'), foreground=MUTED)
    for base, color, size in (('', INK, 10), ('Muted.', MUTED, 10), ('Title.', INK, 17), ('Section.', MUTED, 9)):
        for name in {f'{base}Panel.TLabel', f'Panel.{base}TLabel'}:
            style.configure(name, background=PANEL, foreground=color,
                            font=(family, size, 'bold') if base in ('Title.', 'Section.') else (family, size))
    for name, color in (('Warning.TLabel', WARNING), ('Error.TLabel', DANGER), ('Success.TLabel', SUCCESS)):
        style.configure(name, foreground=color)
    style.configure('Treeview', rowheight=30, background=FIELD, fieldbackground=FIELD,
                    foreground=INK, borderwidth=1, relief='solid', bordercolor=BORDER)
    style.map('Treeview', background=[('selected', SELECTED)], foreground=[('selected', INK)],
              bordercolor=[('focus', ACCENT)])
    style.configure('Treeview.Heading', padding=(9, 8), relief='flat', background=PANEL,
                    foreground=MUTED, font=(family, 9, 'bold'), borderwidth=1)
    style.map('Treeview.Heading', background=[('active', HOVER)], foreground=[('active', INK)])
    style.configure('TSeparator', background=BORDER)
    style.configure('TScrollbar', background=BORDER, troughcolor=FIELD, arrowcolor=MUTED,
                    borderwidth=0, arrowsize=12, relief='flat')
    style.map('TScrollbar', background=[('active', MUTED), ('pressed', ACCENT)], arrowcolor=[('disabled', BORDER)])
    element('Studio.trough', surface(FIELD, BORDER, (18, 8), 2), border=3)
    element('Studio.pbar', surface(ACCENT, ACCENT, (18, 8), 2), border=3)
    style.layout('Horizontal.TProgressbar', [('Studio.trough', {'sticky': 'nsew', 'children': [
        ('Studio.pbar', {'side': 'left', 'sticky': 'ns'})]})])
    style.configure('Horizontal.TProgressbar', borderwidth=0, thickness=8)
    # Classic Tk widgets, Matplotlib tools/tooltips and Tk-native file selectors.
    options = {'background': PANEL, 'foreground': INK, 'activeBackground': HOVER,
               'activeForeground': INK, 'disabledForeground': DISABLED,
               'selectBackground': SELECTED, 'selectForeground': INK,
               'highlightBackground': BORDER, 'highlightColor': ACCENT,
               'insertBackground': INK, 'troughColor': FIELD}
    for option, value in options.items():
        root.option_add('*'+option, value)
    for klass in ('Text', 'Entry', 'Listbox', 'Spinbox'):
        root.option_add(f'*{klass}.background', FIELD)
        root.option_add(f'*{klass}.selectBackground', SELECTED)
        root.option_add(f'*{klass}.selectForeground', INK)
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', INK)
    root.option_add('*TCombobox*Listbox.selectBackground', SELECTED)
    root.option_add('*TCombobox*Listbox.selectForeground', INK)
    root.option_add('*Menu.tearOff', False)
    root.configure(background=BACKGROUND)
    root._studio_theme_images = images
    root._studio_theme_applied = True
    return style


def style_text(widget):
    """Use the same bordered text surface for scripts, details, and messages."""
    widget.configure(background=FIELD, foreground=INK, insertbackground=INK,
                     selectbackground=SELECTED, selectforeground=INK, relief='flat',
                     borderwidth=0, highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=ACCENT, padx=8, pady=8)
    # ScrolledText owns a classic Tk frame/scrollbar outside the Text widget.
    if hasattr(widget, 'frame'):
        widget.frame.configure(background=FIELD)
        widget.vbar.configure(background=BORDER, troughcolor=FIELD, activebackground=MUTED,
                              highlightbackground=BORDER, borderwidth=0)
    return widget


def style_tree(tree):
    tree.tag_configure('odd', background=FIELD)
    tree.tag_configure('even', background=PANEL)
    tree.tag_configure('group', background=SELECTED, foreground=INK, font=('TkDefaultFont', 10, 'bold'))
    tree.tag_configure('unavailable', foreground=MUTED)
    return tree


def style_toolbar(toolbar):
    """Style Matplotlib's classic widgets without replacing its interactions."""
    toolbar.configure(background=PANEL, borderwidth=0, highlightthickness=0)
    for widget in toolbar.winfo_children():
        try:
            widget.configure(background=PANEL)
            if isinstance(widget, (tk.Button, tk.Checkbutton)):
                widget.configure(foreground=MUTED, activebackground=HOVER, activeforeground=INK,
                                 disabledforeground=DISABLED, relief='flat', borderwidth=0,
                                 highlightthickness=1, highlightbackground=PANEL,
                                 highlightcolor=ACCENT, takefocus=True)
                if isinstance(widget, tk.Checkbutton):
                    widget.configure(selectcolor=SELECTED)
                if hasattr(widget, '_image_file'):
                    toolbar._set_image_for_button(widget)
            elif isinstance(widget, tk.Label):
                widget.configure(foreground=MUTED)
        except tk.TclError:
            pass
    if not hasattr(toolbar, '_studio_subplots_original'):
        toolbar._studio_subplots_original = toolbar.configure_subplots

        def configure_subplots(*args):
            tool = toolbar._studio_subplots_original(*args)
            _style_subplot_tool(tool)
            return tool

        toolbar.configure_subplots = configure_subplots
        subplot_button = getattr(toolbar, '_buttons', {}).get('Subplots')
        if subplot_button is not None:
            subplot_button.configure(command=configure_subplots)
    return toolbar


def _style_subplot_tool(tool):
    """Restyle this one Matplotlib tool without changing global PDF rcParams."""
    from matplotlib.text import Text
    figure = tool.figure
    figure.set_facecolor(BACKGROUND)
    for axes in figure.axes:
        axes.set_facecolor(PANEL)
        for spine in axes.spines.values():
            spine.set_edgecolor(BORDER)
    for text in figure.findobj(match=Text):
        text.set_color(INK)
    for name in ('left', 'bottom', 'right', 'top', 'wspace', 'hspace'):
        slider = getattr(tool, 'slider'+name)
        slider.track.set_facecolor(FIELD)
        slider.poly.set_facecolor(ACCENT)
    tool.buttonreset.color = FIELD
    tool.buttonreset.hovercolor = HOVER
    tool.buttonreset.ax.set_facecolor(FIELD)
    tool.buttonreset.label.set_color(INK)
    window = getattr(figure.canvas.manager, 'window', None)
    if window is not None:
        window.configure(background=BACKGROUND)
    figure.canvas.draw_idle()


def status_color(text):
    """Shared semantic status colors; wording remains the source of state."""
    value = str(text).lower()
    if any(word in value for word in ('failed', 'failure', 'error', 'could not', 'rejected')):
        return DANGER
    if any(word in value for word in ('stale', 'cancel', 'warning', 'unavailable')):
        return WARNING
    if any(word in value for word in ('loading', 'finding', 'fetching', 'exporting', 'connecting')):
        return ACCENT
    return MUTED


class ScrollableFrame(ttk.Frame):
    """A vertically scrolling native form that keeps keyboard focus visible.

    Add children to ``content``. The inner frame follows the viewport width;
    there is no horizontal scroll range. Wheel bindings are local to this form,
    and controls with their own scrolling retain their normal wheel behavior.
    """
    def __init__(self, parent, *, style='TFrame', padding=0, **kwargs):
        super().__init__(parent, style=style, **kwargs)
        background = ttk.Style(self).lookup(style, 'background') or BACKGROUND
        self.canvas = tk.Canvas(self, background=background, borderwidth=0,
                                highlightthickness=0, width=1, height=1, takefocus=False)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scroll_changed)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.content = ttk.Frame(self.canvas, style=style, padding=padding)
        self._window = self.canvas.create_window(0, 0, anchor='nw', window=self.content)
        self._bound = set()
        self._pending = None
        self._destroying = False
        self.canvas.bind('<Configure>', self._resize)
        self.content.bind('<Configure>', self._content_changed)
        for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.canvas.bind(event, self._wheel, add='+')

    def _scroll_changed(self, first, last):
        self.scrollbar.set(first, last)
        needed = float(first) > .0001 or float(last) < .9999
        if needed and not self.scrollbar.winfo_manager():
            self.scrollbar.grid(row=0, column=1, sticky='ns')
        elif not needed and self.scrollbar.winfo_manager():
            self.scrollbar.grid_remove()

    def _resize(self, event):
        self.canvas.itemconfigure(self._window, width=event.width,
                                  height=max(event.height, self.content.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _content_changed(self, _event=None):
        self.canvas.itemconfigure(self._window, height=max(self.canvas.winfo_height(),
                                                          self.content.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self._bind_children(self.content)

    def _bind_children(self, widget):
        if str(widget) not in self._bound:
            self._bound.add(str(widget))
            widget.bind('<FocusIn>', lambda event: self.reveal(event.widget), add='+')
            for event in ('<Configure>', '<Destroy>', '<Map>', '<Unmap>'):
                widget.bind(event, self._schedule_content, add='+')
            # Text, data tables, selectors and sliders have their own gestures.
            if not isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox,
                                       ttk.Spinbox, ttk.Scale, tk.Scale, ttk.Scrollbar)):
                for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                    widget.bind(event, self._wheel, add='+')
        for child in widget.winfo_children():
            self._bind_children(child)

    def _schedule_content(self, _event=None):
        if not self._destroying and self._pending is None:
            self._pending = self.after_idle(self._update_content)

    def _update_content(self):
        self._pending = None
        if not self._destroying:
            self._content_changed()

    def destroy(self):
        self._destroying = True
        if self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None
        super().destroy()

    def _wheel(self, event):
        if not self.scrollbar.winfo_manager():
            return None
        if getattr(event, 'num', None) in (4, 5):
            units = -3 if event.num == 4 else 3
        else:
            delta = getattr(event, 'delta', 0)
            if not delta:
                return None
            units = int(-delta / 120) or (-1 if delta > 0 else 1)
        self.canvas.yview_scroll(units, 'units')
        return 'break'

    def reveal(self, widget):
        """Scroll the focused descendant fully into view, without taking focus."""
        if not widget.winfo_ismapped():
            return
        y = widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = y + widget.winfo_height()
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        total = max(1, self.content.winfo_height())
        if y < top:
            self.canvas.yview_moveto(max(0, y-4) / total)
        elif bottom > top + height:
            self.canvas.yview_moveto((bottom-height+4) / total)
