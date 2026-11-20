"""The A4 page frame shared by every PDF report: header, wrapped text, tables and page footers."""
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.image import imread
from matplotlib.lines import Line2D
from matplotlib.patches import Ellipse, Rectangle
from matplotlib.textpath import text_to_path

from motionstudio.core.about import APP_NAME
from motionstudio.core.rendering import RENDER_LOCK

PAGE_WIDTH, PAGE_HEIGHT = 595.32, 841.92
LEFT, RIGHT, BOTTOM = 56.0, 539.32, 758.0
INK, MUTED, BAR = '#073746', '#56707a', '#677f84'
ACCENT = '#17bcbd'  # APEX teal; the orange stays in the logo
LOGO = Path(__file__).with_name('apex_logo.png')
LOGO_HEIGHT = 19.0
# Status colours always sit beside their label, never alone.
STATUS_COLORS = {'Critical': '#d03b3b', 'High': '#ec835a', 'Attention required': '#ec835a',
                 'Medium': '#fab219', 'Monitor': '#fab219', 'Good': '#0ca30c',
                 'Low': '#7d9196', 'Info': '#b7c3c6', 'Limited evidence': '#b7c3c6', 'Not assessed': '#b7c3c6'}


@lru_cache(maxsize=1)
def logo():
    """The APEX logo as an RGBA array (cached; it is drawn on every page)."""
    return imread(LOGO)


class Chip(str):
    """A table cell drawn as a status dot followed by its label."""


class ReportDocument:
    """A4 frame for all reports: header, wrapped text, tables and 'Page N of M' footers."""
    section = ''

    def __init__(self, pdf, source, subtitle, note):
        self.pdf, self.source = pdf, str(source)
        self.subtitle, self.note = subtitle, note
        self.generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        self.figure = None
        self.pages = []

    def lines(self, text, width, size=9.5, weight='normal'):
        font = FontProperties(family='DejaVu Sans', size=size, weight=weight)
        def fits(value):
            # Unhinted advances are what the PDF embeds; hinted raster widths differ by several percent.
            return text_to_path.get_text_width_height_descent(value, font, False)[0] <= width
        result = []
        with RENDER_LOCK:
            for paragraph in str(text).split('\n'):
                current = ''
                for word in paragraph.split():
                    if current and not fits(current + ' ' + word):
                        result.append(current)
                        current = ''
                    # Long filenames and source expressions must not escape their box;
                    # binary search keeps very long source paths inexpensive to wrap.
                    while not fits(word) and len(word) > 1:
                        low, high = 1, len(word)
                        while low < high:
                            middle = (low+high+1)//2
                            if fits(word[:middle]):
                                low = middle
                            else:
                                high = middle-1
                        # Prefer to break a path or URL after a separator near the end of the line.
                        low = max(word.rfind(mark, low//2, low) for mark in '/_-.,=:')+1 or low
                        result.append(word[:low])
                        word = word[low:]
                    current = (current + ' ' + word).strip()
                result.append(current)
        return result

    def text(self, x, y, text, size=9.5, color=INK, weight='normal', **kwargs):
        return self.figure.text(x/PAGE_WIDTH, 1-y/PAGE_HEIGHT, str(text),
                                fontsize=size, color=color, weight=weight,
                                family='DejaVu Sans', va='top', **kwargs)

    def rect(self, x, y, width, height, color, edge='#d1d9db'):
        self.figure.add_artist(Rectangle((x/PAGE_WIDTH, 1-(y+height)/PAGE_HEIGHT),
                                        width/PAGE_WIDTH, height/PAGE_HEIGHT,
                                        transform=self.figure.transFigure,
                                        facecolor=color, edgecolor=edge, linewidth=.7, zorder=-1))

    def rule(self, y):
        self.figure.add_artist(Line2D([LEFT/PAGE_WIDTH, RIGHT/PAGE_WIDTH], [1-y/PAGE_HEIGHT]*2,
                                      transform=self.figure.transFigure, color='#c3ced1', linewidth=.7))

    def new_page(self, title=None):
        self.finish_page()
        continued = bool(title) and title.endswith(' (continued)')
        if title and not continued:
            self.section = title
        self.figure = Figure(figsize=(PAGE_WIDTH/72, PAGE_HEIGHT/72), dpi=72, facecolor='white')
        try:
            image = logo()
            width = LOGO_HEIGHT * image.shape[1] / image.shape[0]
            axes = self.figure.add_axes((LEFT/PAGE_WIDTH, 1-(30+LOGO_HEIGHT)/PAGE_HEIGHT,
                                         width/PAGE_WIDTH, LOGO_HEIGHT/PAGE_HEIGHT))
            axes.imshow(image, interpolation='lanczos')
            axes.set_axis_off()
        except OSError:  # a missing asset must not stop a report; --smoke-test checks it is packaged
            self.text(LEFT, 34, APP_NAME, 11, INK, 'bold')
        self.text(RIGHT, 31, self.subtitle, 7.5, INK, 'bold', ha='right')
        if self.source:
            first, *rest = self.lines(self.source, 260, 7.5)
            self.text(RIGHT, 42, first + ('…' if rest else ''), 7.5, MUTED, ha='right')
        self.figure.add_artist(Line2D([LEFT/PAGE_WIDTH, RIGHT/PAGE_WIDTH], [1-60/PAGE_HEIGHT]*2,
                                      transform=self.figure.transFigure, color=ACCENT, linewidth=1.4))
        self.y = 84
        if title:
            self.paragraph(title, size=9 if continued else 20, color=MUTED if continued else INK)
            self.y += 8 if continued else 3
        self.top = self.y

    def start_section(self, title, room=260):
        """Start a titled section, on the current page when at least ``room`` points remain."""
        if self.figure is None or self.y > BOTTOM-room:
            self.new_page(title)
            return
        self.section = title
        self.y += 22
        self.paragraph(title, size=20)
        self.y += 3

    def finish_page(self):
        if self.figure is not None:
            self.pages.append(self.figure)
            self.figure = None

    def close(self):
        """Write every page; footers wait until here because they state the page count."""
        self.finish_page()
        note = self.lines(self.note, RIGHT-LEFT, 7)
        for number, self.figure in enumerate(self.pages, 1):
            self.rule(768)
            for index, line in enumerate(note):
                self.text(LEFT, 784+index*9.5, line, 7, MUTED)
            self.text(LEFT, 816, f'Generated {self.generated} · {APP_NAME}', 7.5, MUTED)
            self.text(RIGHT, 816, f'Page {number} of {len(self.pages)}', 7.5, INK, ha='right')
            with RENDER_LOCK:
                self.pdf.savefig(self.figure, dpi=144)
                self.figure.clear()
        self.pages, self.figure = [], None

    def paragraph(self, text, x=LEFT, width=RIGHT-LEFT, size=9.5, color=INK, weight='normal'):
        leading = size * (1.6 if size >= 13 else 1.45)
        for line in self.lines(text, width, size, weight):
            if self.y + leading > BOTTOM:
                self.new_page(self.section + ' (continued)')
            self.text(x, self.y, line, size, color, weight)
            self.y += leading

    def heading(self, title, size=13):
        if self.y + 90 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        elif self.y > self.top:
            self.y += 10
        self.paragraph(title, size=size, weight='bold')
        self.y += 1

    def bullet(self, text, marker='•', size=9.5, indent=16):
        if self.y + size*1.45 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        self.text(LEFT+2, self.y, marker, size)
        self.paragraph(text, LEFT+indent, RIGHT-LEFT-indent, size)
        self.y += 4

    def bar(self, title):
        self.rect(LEFT, self.y, RIGHT-LEFT, 20, BAR, BAR)
        self.text(LEFT+8, self.y+5.5, title, 8.5, 'white', 'bold')
        self.y += 20

    def rows(self, title, rows, label_width=110):
        """Two-column table; a value is text or a list of (text, color, weight) paragraphs."""
        if not rows:
            return
        wrapped = []
        for label, value in rows:
            parts = value if isinstance(value, (list, tuple)) else [(value, INK, 'normal')]
            wrapped.append((self.lines(label, label_width-16, 8.5, 'bold'),
                            [(line, color, weight) for text, color, weight in parts
                             for line in self.lines(text, RIGHT-LEFT-label_width-16, 9, weight)]))
        # A title bar is never left alone at the foot of a page.
        if self.y + 32 + min(max(map(len, wrapped[0])), 32)*13 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        self.bar(title)
        for index, (left, right) in enumerate(wrapped):
            count = max(len(left), len(right))
            offset = 0
            while offset < count:
                room = int((BOTTOM-self.y-12) // 13)
                remaining = count-offset
                # Keep ordinary rows together; split oversized evidence safely.
                if room < 1 or (offset == 0 and remaining <= 32 and remaining > room):
                    self.new_page(self.section + ' (continued)')
                    self.bar(title + ' (continued)')
                    room = int((BOTTOM-self.y-12) // 13)
                take = min(room, remaining)
                height = take*13+12
                self.rect(LEFT, self.y, RIGHT-LEFT, height, '#f0f4f5' if index % 2 == 0 else 'white')
                for n, line in enumerate(left[offset:offset+take]):
                    self.text(LEFT+8, self.y+6+n*13, line, 8.5, INK, 'bold')
                for n, (line, color, weight) in enumerate(right[offset:offset+take]):
                    self.text(LEFT+label_width+8, self.y+6+n*13, line, 9, color, weight)
                self.y += height
                offset += take
        self.y += 14

    def ensure(self, height):
        """Start a continued page unless ``height`` points still fit."""
        if self.y + height > BOTTOM:
            self.new_page(self.section + ' (continued)')

    def _cell(self, value, width, size, weight):
        if isinstance(value, Chip):
            return [(line, INK, 'bold', value if n == 0 else None)
                    for n, line in enumerate(self.lines(value, width-12, size, 'bold'))]
        text, color, weight = value if isinstance(value, tuple) else (value, INK, weight)
        return [(line, color, weight, None) for line in self.lines(text, width, size, weight)]

    def table(self, columns, rows, widths, *, size=8.5, title=None, bold_first=False, header=True):
        """Wrapped multi-column table; the header repeats after a page break.

        ``widths`` are fractions of the text width. A cell is text, a (text, color,
        weight) tuple, or a ``Chip`` (status dot + label, colour from STATUS_COLORS).
        """
        if not rows:
            return
        total = RIGHT-LEFT
        widths = [w*total for w in widths]
        lead, pad = size*1.42, 5
        def draw_header():
            if title:
                self.bar(title)
            if not header:
                return
            x = LEFT
            self.rect(LEFT, self.y, total, lead+2*pad, '#e8eef0', '#e8eef0')
            for label, width in zip(columns, widths):
                self.text(x+6, self.y+pad, label, size-.5, MUTED, 'bold')
                x += width
            self.y += lead+2*pad
        laid = [[self._cell(cell, width-12, size, 'bold' if bold_first and n == 0 else 'normal')
                 for n, (cell, width) in enumerate(zip(row, widths))] for row in rows]
        first = max(map(len, laid[0]))*lead+2*pad
        self.ensure((20 if title else 0)+(lead+2*pad if header else 0)+first)
        draw_header()
        for index, cells in enumerate(laid):
            count = max(map(len, cells))
            offset = 0
            while offset < count:
                room = int((BOTTOM-self.y-2*pad) // lead)
                remaining = count-offset
                # Keep ordinary rows together; split a row taller than a page.
                if room < 1 or (offset == 0 and remaining <= 30 and remaining > room):
                    self.new_page(self.section + ' (continued)')
                    draw_header()
                    continue
                take = min(room, remaining)
                height = take*lead+2*pad
                self.rect(LEFT, self.y, total, height, '#f5f8f9' if index % 2 else 'white', '#dde5e7')
                x = LEFT
                for lines, width in zip(cells, widths):
                    indent = 12 if lines and lines[0][3] is not None else 0
                    for n, (line, color, weight, chip) in enumerate(lines[offset:offset+take]):
                        if chip is not None:
                            self.figure.add_artist(Ellipse(((x+9)/PAGE_WIDTH, 1-(self.y+pad+size*.62)/PAGE_HEIGHT),
                                                   6.4/PAGE_WIDTH, 6.4/PAGE_HEIGHT, transform=self.figure.transFigure,
                                                   facecolor=STATUS_COLORS.get(chip, '#b7c3c6'), edgecolor='none'))
                        self.text(x+6+indent, self.y+pad+n*lead, line, size, color, weight)
                    x += width
                self.y += height
                offset += take
        self.y += 12

    def tiles(self, items, height=58):
        """A row of headline numbers: (value, label) pairs."""
        self.ensure(height+10)
        gap = 10
        width = (RIGHT-LEFT-gap*(len(items)-1))/len(items)
        for n, (value, label) in enumerate(items):
            x = LEFT+n*(width+gap)
            self.rect(x, self.y, width, height, '#f5f8f9', '#dde5e7')
            self.text(x+10, self.y+9, value, 18, INK, 'bold')
            for k, line in enumerate(self.lines(label, width-20, 7.5)[:2]):
                self.text(x+10, self.y+35+k*9.5, line, 7.5, MUTED)
        self.y += height+16

    def banner(self, status, headline, text):
        """Overall status: a coloured edge, the status in words, then a short assessment."""
        lines = self.lines(text, RIGHT-LEFT-30, 9.5)
        height = 34+len(lines)*13.8+6
        self.ensure(height)
        self.rect(LEFT, self.y, RIGHT-LEFT, height, '#f5f8f9', '#dde5e7')
        self.rect(LEFT, self.y, 5, height, STATUS_COLORS.get(status, BAR), STATUS_COLORS.get(status, BAR))
        self.text(LEFT+18, self.y+10, headline, 13, INK, 'bold')
        for n, line in enumerate(lines):
            self.text(LEFT+18, self.y+32+n*13.8, line, 9.5, INK)
        self.y += height+14

    def link(self, title, url, size=8.5):
        """A clickable reference title; the address stays in the PDF link, not the text."""
        self.ensure(size*1.5)
        self.text(LEFT+16, self.y, title, size, '#1c5cab', url=url)
        self.text(LEFT+2, self.y, '•', size)
        self.y += size*1.6
