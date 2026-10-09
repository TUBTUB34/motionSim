"""Soft, image-backed ttk controls; retain native keyboard and focus behavior."""
import tkinter as tk
from tkinter import font, ttk

from PIL import Image, ImageDraw, ImageTk

BACKGROUND = '#f5f7fa'
INK = '#223548'
MUTED = '#62758a'
ACCENT = '#167d9a'


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    available = set(font.families(root))
    family = next((name for name in ('Segoe UI', 'Noto Sans', 'DejaVu Sans') if name in available), 'TkDefaultFont')
    font.nametofont('TkDefaultFont', root=root).configure(family=family, size=10)
    style.configure('.', background=BACKGROUND, foreground=INK, font=(family, 10))
    images = []

    def rounded(fill, border, size=(40, 36), radius=9):
        scale = 3
        im = Image.new('RGBA', (size[0]*scale, size[1]*scale))
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((1, 1, size[0]*scale-2, size[1]*scale-2), radius=radius*scale,
                               fill=fill, outline=border, width=scale)
        photo = ImageTk.PhotoImage(im.resize(size, Image.Resampling.LANCZOS), master=root)
        images.append(photo)
        return photo

    def element(name, normal, *states):
        # Reuse the Tcl element when another app is constructed in the same root.
        if name not in style.element_names():
            style.element_create(name, 'image', normal, *states, border=11, padding=0, sticky='nsew')

    disabled = rounded('#edf1f5', '#edf1f5')
    element('Studio.button', rounded('#ffffff', '#dce4ec'),
            ('disabled', disabled), ('pressed', rounded('#dceaf0', '#9dbdcc')),
            ('focus', rounded('#ffffff', ACCENT)), ('active', rounded('#edf5f8', '#c5dce6')))
    element('Studio.accent', rounded(ACCENT, ACCENT),
            ('disabled', disabled), ('pressed', rounded('#105b73', '#105b73')),
            ('focus', rounded('#167d9a', '#17334a')), ('active', rounded('#198cac', '#198cac')))
    for name, background in (('TButton', 'Studio.button'), ('Accent.TButton', 'Studio.accent')):
        style.layout(name, [(background, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, padding=(15, 9), borderwidth=0, anchor='center', background=BACKGROUND)
        style.map(name, foreground=[('disabled', '#8a98a7')],
                  background=[('disabled', BACKGROUND), ('!disabled', BACKGROUND)])
    style.configure('Accent.TButton', foreground='#ffffff')
    element('Studio.field', rounded('#ffffff', '#dce4ec'),
            ('disabled', disabled), ('focus', rounded('#ffffff', ACCENT)))
    style.layout('TEntry', [('Studio.field', {'sticky': 'nsew', 'children': [
        ('Entry.padding', {'sticky': 'nsew', 'children': [('Entry.textarea', {'sticky': 'nsew'})]})]})])
    style.configure('TEntry', padding=(10, 7), fieldbackground='#ffffff', insertcolor=INK)
    arrow = Image.new('RGBA', (18*3, 18*3))
    ImageDraw.Draw(arrow).line([(5*3, 7*3), (9*3, 11*3), (13*3, 7*3)], fill=MUTED, width=4)
    arrow = ImageTk.PhotoImage(arrow.resize((18, 18), Image.Resampling.LANCZOS), master=root)
    images.append(arrow)
    style.element_create('Studio.arrow', 'image', arrow)
    style.layout('TCombobox', [('Studio.field', {'sticky': 'nsew', 'children': [
        ('Combobox.padding', {'sticky': 'nsew', 'children': [
            ('Studio.arrow', {'side': 'right', 'sticky': ''}),
            ('Combobox.textarea', {'sticky': 'nsew'})]})]})])
    style.configure('TCombobox', padding=(10, 7), arrowsize=18)
    style.map('TCombobox', fieldbackground=[('readonly', '#ffffff')],
              selectbackground=[('readonly', '#ffffff')], selectforeground=[('readonly', INK)])
    root.option_add('*TCombobox*Listbox.background', '#ffffff')
    root.option_add('*TCombobox*Listbox.foreground', INK)
    root.option_add('*TCombobox*Listbox.selectBackground', '#dfedf3')
    root.option_add('*TCombobox*Listbox.selectForeground', INK)
    element('Studio.tab', rounded(BACKGROUND, BACKGROUND),
            ('selected', rounded('#ffffff', '#dce4ec')), ('active', rounded('#e9eff5', '#e9eff5')))
    style.layout('TNotebook.Tab', [('Studio.tab', {'sticky': 'nsew', 'children': [
        ('Notebook.padding', {'sticky': 'nsew', 'children': [('Notebook.label', {'sticky': 'nsew'})]})]})])
    style.configure('TNotebook', borderwidth=0, tabmargins=(0, 0, 0, 8),
                    bordercolor=BACKGROUND, lightcolor=BACKGROUND, darkcolor=BACKGROUND)
    style.configure('TNotebook.Tab', padding=(18, 9), foreground=MUTED, background=BACKGROUND)
    style.map('TNotebook.Tab', foreground=[('selected', ACCENT), ('focus', INK)])
    style.configure('TLabelframe', borderwidth=0, relief='flat')
    style.configure('TLabelframe.Label', foreground=MUTED, font=(family, 10, 'bold'))
    style.configure('Title.TLabel', font=(family, 17, 'bold'), foreground=INK)
    style.configure('Hero.TLabel', font=(family, 23, 'bold'), foreground=INK)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Hint.TLabel', background='#ffffff', foreground='#8996a5')
    style.configure('Section.TLabel', font=(family, 9, 'bold'), foreground=MUTED)
    style.configure('Treeview', rowheight=32, background='#ffffff', fieldbackground='#ffffff', borderwidth=0)
    style.map('Treeview', background=[('selected', '#dceef4')], foreground=[('selected', '#164f65')])
    style.configure('Treeview.Heading', padding=(10, 9), relief='flat', background=BACKGROUND,
                    font=(family, 10, 'bold'), borderwidth=0)
    style.configure('TSeparator', background='#e3e9ef')
    style.configure('TScrollbar', background='#d5dee7', troughcolor=BACKGROUND,
                    borderwidth=0, arrowsize=12, relief='flat')
    style.element_create('Studio.trough', 'image', rounded('#e5edf3', '#e5edf3', (18, 8), 4),
                         border=4, padding=0, sticky='nsew')
    style.element_create('Studio.progress', 'image', rounded(ACCENT, ACCENT, (18, 8), 4),
                         border=4, padding=0, sticky='nsew')
    style.layout('Horizontal.TProgressbar', [('Studio.trough', {'sticky': 'nsew', 'children': [
        ('Studio.progress', {'side': 'left', 'sticky': 'ns'})]})])
    style.configure('Horizontal.TProgressbar', borderwidth=0, thickness=8)
    root.configure(background=BACKGROUND)
    root._studio_theme_images = images  # Keep Tcl image resources alive.
    return style
