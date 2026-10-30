"""Native design-system behavior checks; use MOTIONSIM_UI_TESTS=1."""
import os
import tkinter as tk
from tkinter import ttk
import unittest

from ui_theme import apply_theme, style_text, style_toolbar, ScrollableFrame, FIELD, INK
from ui_dialogs import askyesno, MessageDialog


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires local graphical display')
class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('640x420')
        apply_theme(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_reapply_keeps_images_alive_and_compact_controls_accessible(self):
        button = ttk.Button(self.root, text='Connect to robot')
        button.pack()
        combo = ttk.Combobox(self.root, state='readonly', values=('UR10e', 'UR15'))
        combo.pack()
        combo.current(0)
        apply_theme(self.root)
        self.root.update()
        self.assertGreaterEqual(button.winfo_height(), 32)
        self.assertGreaterEqual(combo.winfo_height(), 32)
        self.assertEqual(combo.get(), 'UR10e')
        button.state(['disabled'])
        self.assertTrue(button.instate(['disabled']))

    def test_entries_keep_keyboard_traversal_and_readonly_selection(self):
        entry = ttk.Entry(self.root)
        entry.pack()
        button = ttk.Button(self.root, text='Continue')
        button.pack()
        self.root.update()
        self.root.focus_force()
        entry.focus_set()
        entry.insert(0, 'readable text')
        entry.event_generate('<Tab>')
        self.root.update()
        self.assertIs(self.root.focus_get(), button)
        self.assertEqual(entry.get(), 'readable text')
        entry.state(['readonly'])
        entry.selection_range(0, 'end')
        self.assertTrue(entry.selection_present())

    def test_progress_indicator_reflects_fraction_instead_of_fixed_icon_width(self):
        progress = ttk.Progressbar(self.root, value=65, maximum=100)
        progress.pack(fill='x')
        self.root.update()
        width, middle = progress.winfo_width(), progress.winfo_height() // 2
        self.assertIn('pbar', progress.identify(int(width*.5), middle))
        self.assertIn('trough', progress.identify(int(width*.9), middle))

    def test_text_and_toolbar_remain_functional_on_dark_surfaces(self):
        from tkinter.scrolledtext import ScrolledText
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from matplotlib.figure import Figure
        text = ScrolledText(self.root, height=2)
        style_text(text)
        text.pack()
        text.insert('1.0', 'Recorded source details')
        text.configure(state='disabled')
        canvas = FigureCanvasTkAgg(Figure(figsize=(3, 1)), master=self.root)
        toolbar = NavigationToolbar2Tk(canvas, self.root)
        style_toolbar(toolbar)
        self.root.update()
        self.assertEqual(text.cget('background'), FIELD)
        self.assertEqual(text.cget('foreground'), INK)
        self.assertIn('Recorded source details', text.get('1.0', 'end'))
        self.assertEqual(str(toolbar._buttons['Home'].cget('takefocus')), '1')
        toolbar._buttons['Home'].invoke()

    def test_confirmation_default_no_and_escape_preserve_safe_cancel(self):
        def close():
            dialog = next(w for w in self.root.winfo_children() if isinstance(w, MessageDialog))
            self.assertIs(dialog.default_button, dialog.buttons['no'])
            dialog.event_generate('<Escape>')
        self.root.after(120, close)
        self.assertFalse(askyesno('Verify robot SSH host', 'Compare the supplied fingerprint.',
                                 parent=self.root, default='no'))

    def test_dialog_restores_keyboard_focus_and_copy_does_not_confirm(self):
        from unittest.mock import patch
        trigger = ttk.Button(self.root, text='Details')
        trigger.pack()
        self.root.update()
        trigger.focus_force()
        self.root.update()

        def copy_and_cancel():
            dialog = next(w for w in self.root.winfo_children() if isinstance(w, MessageDialog))
            copy = next(w for w in dialog.winfo_children()[-1].winfo_children()
                        if isinstance(w, ttk.Button) and w.cget('text') == 'Copy details')
            copy.focus_force()
            self.root.update()
            dialog._return(None)
            self.assertEqual(self.root.clipboard_get(), 'Archive recovery path')
            self.assertTrue(dialog.winfo_exists())
            dialog.cancel()

        self.root.after(120, copy_and_cancel)
        with patch.object(trigger, 'focus_set', wraps=trigger.focus_set) as restore:
            self.assertFalse(askyesno('Backup details', 'Archive recovery path', parent=self.root))
            restore.assert_called_once()

    def test_subplot_tool_keeps_slider_and_reset_behavior_without_global_palette_changes(self):
        import matplotlib as mpl
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from matplotlib.backend_bases import MouseEvent
        from matplotlib.figure import Figure
        original_background = mpl.rcParams['figure.facecolor']
        figure = Figure(figsize=(3, 2))
        figure.add_subplot().plot([0, 1], [0, 1])
        canvas = FigureCanvasTkAgg(figure, master=self.root)
        toolbar = NavigationToolbar2Tk(canvas, self.root)
        style_toolbar(toolbar)
        toolbar._buttons['Subplots'].invoke()
        tool = toolbar.subplot_tool
        try:
            initial = figure.subplotpars.left
            tool.sliderleft.set_val(.2)
            self.assertAlmostEqual(figure.subplotpars.left, .2)
            tool.figure.canvas.draw()
            x, y = tool.buttonreset.ax.transAxes.transform((.5, .5))
            for name in ('button_press_event', 'button_release_event'):
                event = MouseEvent(name, tool.figure.canvas, x, y, button=1)
                tool.figure.canvas.callbacks.process(name, event)
            self.assertAlmostEqual(figure.subplotpars.left, initial)
            self.assertEqual(mpl.rcParams['figure.facecolor'], original_background)
        finally:
            tool.figure.canvas.manager.destroy()

    def test_confirmation_yes_and_long_messages_support_scrolling(self):
        def accept():
            dialog = next(w for w in self.root.winfo_children() if isinstance(w, MessageDialog))
            self.assertLessEqual(int(dialog.message.cget('height')), 18)
            self.assertIn('Fingerprint', dialog.message.get('1.0', 'end'))
            dialog.buttons['yes'].invoke()
        self.root.after(120, accept)
        self.assertTrue(askyesno('Save robot logs?', 'Fingerprint detail\n'*50,
                                parent=self.root, default='no'))

    def test_scrollable_form_keeps_tab_focus_visible_without_horizontal_scroll(self):
        panel = ScrollableFrame(self.root, padding=12, style='Panel.TFrame')
        panel.pack(fill='both', expand=True)
        fields = []
        for index in range(24):
            entry = ttk.Entry(panel.content)
            entry.insert(0, f'Field {index}')
            entry.pack(fill='x', pady=4)
            fields.append(entry)
        self.root.update()
        self.assertEqual(panel.scrollbar.winfo_manager(), 'grid')
        self.root.focus_force()
        fields[-1].focus_set()
        self.root.update()
        self.assertGreater(panel.canvas.yview()[0], 0)
        self.assertGreaterEqual(fields[-1].winfo_rooty(), panel.canvas.winfo_rooty())
        self.assertLessEqual(fields[-1].winfo_rooty()+fields[-1].winfo_height(),
                             panel.canvas.winfo_rooty()+panel.canvas.winfo_height())
        fields[0].focus_force()
        self.root.update()
        self.assertLess(panel.canvas.yview()[0], .03)
        self.assertGreaterEqual(fields[0].winfo_rooty(), panel.canvas.winfo_rooty())
        self.assertEqual(panel.content.winfo_width(), panel.canvas.winfo_width())
        for entry in fields[1:]:
            entry.destroy()
        self.root.update()
        self.assertFalse(panel.scrollbar.winfo_manager())


if __name__ == '__main__':
    unittest.main()
