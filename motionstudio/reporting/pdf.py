"""The A4 page frame shared by every PDF report: header, wrapped text, tables and page footers."""
from datetime import datetime, timezone

from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.textpath import text_to_path

from motionstudio.core.rendering import RENDER_LOCK

PAGE_WIDTH, PAGE_HEIGHT = 595.32, 841.92
LEFT, RIGHT, BOTTOM = 70.0, 525.32, 758.0
INK, MUTED, BAR = '#073746', '#56707a', '#677f84'


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
        self.text(LEFT, 38, 'UR MOTION STUDIO', 10, INK, 'bold')
        self.text(LEFT, 52, self.subtitle, 7.5, MUTED)
        if self.source:
            first, *rest = self.lines(self.source, 260, 7.5)
            self.text(RIGHT, 52, first + ('…' if rest else ''), 7.5, MUTED, ha='right')
        self.rule(66)
        self.y = 88
        if title:
            self.paragraph(title, size=9 if continued else 20, color=MUTED if continued else INK)
            self.y += 8 if continued else 3
        self.top = self.y

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
            self.text(LEFT, 816, 'Generated ' + self.generated, 7.5, MUTED)
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
