"""Searchable native help, usable offline and in the packaged application."""
import tkinter as tk
from tkinter import ttk

from help_content import TOPICS
from ui_theme import BACKGROUND, INK, ACCENT, style_tree, style_text


class HelpWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, background=BACKGROUND)
        self.title('UR Motion Studio · How to')
        self.geometry('1040x760')
        self.minsize(700, 520)
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.bind('<Escape>', lambda _: self.withdraw())
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        heading = ttk.Frame(self, style='Toolbar.TFrame', padding=(18, 14))
        heading.grid(row=0, column=0, columnspan=2, sticky='ew')
        ttk.Label(heading, text='How to use Motion Studio', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(heading, text='Close guide', command=self.withdraw).pack(side='right')
        navigation = ttk.Frame(self, padding=12)
        navigation.grid(row=1, column=0, sticky='ns')
        navigation.columnconfigure(0, weight=1)
        navigation.rowconfigure(2, weight=1)
        ttk.Label(navigation, text='Find a topic', style='Section.TLabel').grid(row=0, column=0, sticky='w')
        self.query = tk.StringVar()
        self.search = ttk.Entry(navigation, textvariable=self.query, width=23)
        self.search.grid(row=1, column=0, sticky='ew', pady=(6, 10))
        self.topics = ttk.Treeview(navigation, show='tree', selectmode='browse', height=12)
        self.topics.column('#0', width=205, minwidth=180)
        style_tree(self.topics)
        self.topics.grid(row=2, column=0, sticky='ns')
        scroll = ttk.Scrollbar(navigation, orient='vertical', command=self.topics.yview)
        scroll.grid(row=2, column=1, sticky='ns')
        self.topics.configure(yscrollcommand=scroll.set)
        self.topics.bind('<<TreeviewSelect>>', self._selected)
        content = ttk.Frame(self, padding=(8, 12, 16, 12))
        content.grid(row=1, column=1, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        self.topic = tk.StringVar()
        ttk.Label(content, textvariable=self.topic, style='Title.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 12))
        self.text = tk.Text(content, wrap='word', width=35, height=10, padx=16, pady=12,
                            spacing1=3, spacing3=10, font=('TkDefaultFont', 10), relief='flat')
        style_text(self.text)
        self.text.grid(row=1, column=0, sticky='nsew')
        bar = ttk.Scrollbar(content, orient='vertical', command=self.text.yview)
        bar.grid(row=1, column=1, sticky='ns')
        self.text.configure(yscrollcommand=bar.set)
        self.text.tag_configure('section', foreground=ACCENT, font=('TkDefaultFont', 11, 'bold'), spacing1=12)
        self.text.tag_configure('body', foreground=INK)
        ttk.Label(self, text='Offline guide · F1 opens help · Search matches topic titles and instructions',
                  style='Muted.TLabel', padding=(18, 10)).grid(row=2, column=0, columnspan=2, sticky='ew')
        self.query.trace_add('write', self._filter)
        self._filter()

    def _filter(self, *_):
        query = self.query.get().casefold().strip()
        previous = self.topic.get()
        self.topics.delete(*self.topics.get_children())
        for title, sections in TOPICS.items():
            content = ' '.join([title, *(text for pair in sections for text in pair)]).casefold()
            if all(word in content for word in query.split()):
                self.topics.insert('', 'end', iid=title, text=title)
        choices = self.topics.get_children()
        if choices:
            self.topics.selection_set(previous if previous in choices else choices[0])
            self._selected()
        else:
            self.topic.set('No matching topics')
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
            self.text.insert('end', 'Try a shorter search, such as torque, backup, connection or PDF.')
            self.text.configure(state='disabled')

    def _selected(self, _event=None):
        selected = self.topics.selection()
        if not selected:
            return
        title = selected[0]
        self.topic.set(title)
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for heading, body in TOPICS[title]:
            self.text.insert('end', heading + '\n', 'section')
            self.text.insert('end', body + '\n\n', 'body')
        self.text.configure(state='disabled')
        self.text.yview_moveto(0)

    def show_topic(self, topic='Getting started'):
        self.query.set('')
        self.topics.selection_set(topic if topic in TOPICS else 'Getting started')
        self.topics.see(self.topics.selection()[0])
        self._selected()
        self.deiconify()
        self.lift()

    def shutdown(self):
        self.destroy()
