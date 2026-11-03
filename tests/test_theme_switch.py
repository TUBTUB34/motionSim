"""Appearance switches preserve controls and cache native theme resources."""
import os
import tkinter as tk
from tkinter import ttk
import unittest

import ui_theme as theme


def contrast(first, second):
    def luminance(value):
        rgb = [int(value[i:i+2], 16) / 255 for i in (1, 3, 5)]
        rgb = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * w for c, w in zip(rgb, (.2126, .7152, .0722)))
    a, b = sorted((luminance(first), luminance(second)))
    return (b + .05) / (a + .05)


class PaletteTests(unittest.TestCase):
    def test_body_muted_and_status_text_have_readable_contrast_in_both_modes(self):
        for mode, colors in theme.PALETTES.items():
            for background in ('BACKGROUND', 'PANEL', 'FIELD'):
                for foreground in ('INK', 'MUTED', 'ACCENT', 'DANGER', 'WARNING', 'TARGET'):
                    with self.subTest(mode=mode, background=background, foreground=foreground):
                        self.assertGreaterEqual(contrast(colors[foreground], colors[background]), 4.5)


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class ThemeSwitchTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('700x550')
        theme.apply_theme(self.root, 'dark')

    def tearDown(self):
        self.root.destroy()

    def test_repeated_switch_preserves_editing_selection_state_and_resources(self):
        text = theme.style_text(tk.Text(self.root, height=4, undo=True))
        text.pack()
        text.insert('1.0', 'Unsaved checklist evidence')
        text.mark_set('insert', '1.8')
        text.tag_add('sel', '1.0', '1.7')
        text.tag_configure('source', foreground=theme.ACCENT)
        tree = theme.style_tree(ttk.Treeview(self.root, height=2))
        tree.pack()
        tree.insert('', 'end', iid='finding', text='Retained finding', tags=('odd',))
        tree.selection_set('finding')
        combo = ttk.Combobox(self.root, values=('192.0.2.1', '192.0.2.2'))
        combo.set('192.0.2.2')
        combo.pack()
        button = ttk.Button(self.root, text='Export', state='disabled')
        button.pack()
        popup = combo.tk.call('ttk::combobox::PopdownWindow', str(combo))
        self.root.update()
        theme.apply_theme(self.root, 'light')
        self.root.update()
        image_count = len(self.root.tk.call('image', 'names'))
        for _ in range(6):
            theme.apply_theme(self.root, 'dark')
            theme.apply_theme(self.root, 'light')
        self.root.update()
        self.assertEqual(len(self.root.tk.call('image', 'names')), image_count)
        self.assertEqual(len(self.root._studio_themes), 2)
        self.assertEqual(text.get('1.0', 'end-1c'), 'Unsaved checklist evidence')
        self.assertEqual(text.index('insert'), '1.8')
        self.assertEqual(tuple(map(str, text.tag_ranges('sel'))), ('1.0', '1.7'))
        self.assertEqual(text.tag_cget('source', 'foreground'), theme.ACCENT)
        self.assertEqual(tree.selection(), ('finding',))
        self.assertEqual(str(tree.tag_configure('odd', 'background')), theme.FIELD)
        self.assertEqual(combo.get(), '192.0.2.2')
        self.assertTrue(button.instate(['disabled']))
        self.assertEqual(self.root.tk.call(str(popup)+'.f.l', 'cget', '-background'), theme.FIELD)
        self.assertEqual(text.cget('background'), theme.FIELD)
        self.assertEqual(ttk.Style(self.root).lookup('TLabel', 'foreground'), theme.INK)
        self.assertEqual(self.root.cget('background'), theme.BACKGROUND)
        text.edit_undo()
        self.assertEqual(text.get('1.0', 'end-1c'), '')

    def test_dialogs_and_new_windows_preserve_selected_light_mode(self):
        from types import SimpleNamespace
        from ui_dialogs import MessageDialog
        from help_view import HelpWindow
        theme.apply_theme(self.root, 'light')
        theme.apply_theme(self.root)
        self.assertEqual(theme.MODE, 'light')
        dialog = MessageDialog(self.root, 'Details', 'Saved review', kind='warning')
        guide = HelpWindow(SimpleNamespace(root=self.root))
        self.assertEqual(dialog.cget('background'), theme.BACKGROUND)
        self.assertEqual(dialog.message.cget('foreground'), theme.INK)
        self.assertEqual(guide.text.tag_cget('section', 'foreground'), theme.ACCENT)
        theme.apply_theme(self.root, 'dark')
        self.assertEqual(dialog.message.cget('background'), theme.FIELD)
        self.assertEqual(guide.text.tag_cget('section', 'foreground'), theme.ACCENT)
        guide.destroy()
        dialog.destroy()

    def test_callbacks_run_once_per_switch_and_invalid_mode_does_not_mutate(self):
        calls = []
        panel = ttk.Frame(self.root)
        theme.register_theme_callback(panel, lambda: calls.append(theme.MODE))
        theme.apply_theme(self.root, 'light')
        theme.apply_theme(self.root)
        theme.apply_theme(self.root, 'light')
        self.assertEqual(calls, ['light'])
        with self.assertRaises(ValueError):
            theme.apply_theme(self.root, 'invalid')
        self.assertEqual(theme.MODE, 'light')
        panel.destroy()
        theme.apply_theme(self.root, 'dark')
        self.assertEqual(calls, ['light'])


if __name__ == '__main__':
    unittest.main()
