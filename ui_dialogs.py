"""Application-owned dialogs sharing the desktop palette and keyboard behavior.

The functions mirror the messagebox calls used by the application. Native file
choosers remain in tkinter.filedialog so their path and overwrite semantics stay
under the operating system's control.
"""
import tkinter as tk
from tkinter import ttk
import textwrap

import ui_theme as theme
from ui_theme import apply_theme, style_text


class MessageDialog(tk.Toplevel):
    def __init__(self, parent, title, message, *, question=False, kind='info', default='yes', detail=None):
        previous_focus = parent.focus_get()
        super().__init__(parent, background=theme.BACKGROUND)
        self.previous_focus = previous_focus
        self.withdraw()
        self.parent = parent
        self.result = False if question else 'ok'
        self.question = question
        self.title(title)
        self.resizable(True, True)
        if parent.winfo_viewable():
            self.transient(parent.winfo_toplevel())
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.bind('<Escape>', lambda _event: self.cancel())
        self.bind('<Return>', self._return)
        header = ttk.Frame(self, padding=(20, 14), style='Panel.TFrame')
        header.pack(fill='x')
        color = theme.DANGER if kind == 'error' else theme.WARNING if kind in ('warning', 'question') else theme.ACCENT
        ttk.Label(header, text=title, style='Panel.Title.TLabel', foreground=color,
                  wraplength=520).pack(anchor='w')
        body = ttk.Frame(self, padding=(20, 16))
        body.pack(fill='both', expand=True)
        content = str(message) + ('\n\n'+str(detail) if detail else '')
        lines = sum(max(1, len(textwrap.wrap(line, 72))) for line in content.splitlines())
        self.message = tk.Text(body, wrap='word', width=68, height=min(18, max(3, lines+1)),
                               font=('TkDefaultFont', 10), takefocus=True)
        style_text(self.message)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.message.yview)
        self.message.configure(yscrollcommand=scroll.set)
        self.message.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.message.insert('1.0', content)
        self.message.configure(state='disabled')
        actions = ttk.Frame(self, padding=(20, 0, 20, 16))
        actions.pack(fill='x')
        ttk.Button(actions, text='Copy details', command=self.copy_details).pack(side='left', padx=(0, 12))
        self.buttons = {}
        if question:
            for value, caption in ((True, 'Yes'), (False, 'No')):
                button = ttk.Button(actions, text=caption, width=9,
                                    style='Accent.TButton' if caption.lower() == default else 'TButton',
                                    command=lambda answer=value: self.accept(answer))
                button.pack(side='right', padx=(8, 0))
                self.buttons[caption.lower()] = button
            self.default_button = self.buttons.get(default, self.buttons['no'])
        else:
            self.default_button = ttk.Button(actions, text='OK', width=9, style='Accent.TButton',
                                            command=lambda: self.accept('ok'))
            self.default_button.pack(side='right')
            self.buttons['ok'] = self.default_button
        self.update_idletasks()
        width, height = max(440, self.winfo_reqwidth()), self.winfo_reqheight()
        x = max(0, parent.winfo_rootx() + (parent.winfo_width()-width)//2)
        y = max(0, parent.winfo_rooty() + (parent.winfo_height()-height)//2)
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.minsize(440, min(height, 280))

    def _return(self, _event):
        focus = self.focus_get()
        (focus if isinstance(focus, ttk.Button) else self.default_button).invoke()
        return 'break'

    def accept(self, result):
        self.result = result
        self.destroy()

    def cancel(self):
        self.accept(False if self.question else 'ok')

    def copy_details(self):
        self.clipboard_clear()
        self.clipboard_append(self.message.get('1.0', 'end-1c'))

    def show(self):
        previous_grab = self.grab_current()
        previous_focus = self.previous_focus
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        self.default_button.focus_set()
        self.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        if previous_focus is not None and previous_focus.winfo_exists():
            previous_focus.focus_set()
        return self.result


def _show(title, message, *, parent=None, question=False, kind='info', default='yes', detail=None, **_options):
    parent = parent or tk._get_default_root('show a dialog')
    root = parent._root()
    apply_theme(root)
    return MessageDialog(parent, title or 'UR Motion Studio', message or '', question=question,
                         kind=kind, default=default, detail=detail).show()


def askyesno(title=None, message=None, **options):
    return bool(_show(title, message, question=True, kind='question', **options))


def showinfo(title=None, message=None, **options):
    return _show(title, message, kind='info', **options)


def showwarning(title=None, message=None, **options):
    return _show(title, message, kind='warning', **options)


def showerror(title=None, message=None, **options):
    return _show(title, message, kind='error', **options)
