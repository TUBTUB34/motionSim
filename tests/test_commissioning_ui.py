"""Native checklist flow; no network or robot connection."""
import gc
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class CommissioningUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from types import SimpleNamespace
        from commissioning_view import CommissioningPage
        from ui_theme import apply_theme
        self.root = tk.Tk()
        self.root.geometry('920x650')
        apply_theme(self.root)
        app = SimpleNamespace(root=self.root, navigate_panel=lambda _: None)
        self.page = CommissioningPage(app, self.root)
        self.page.pack(fill='both', expand=True)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.temp = tempfile.TemporaryDirectory()
        self.root.update()

    def tearDown(self):
        self.page.shutdown()
        self.root.destroy()
        self.temp.cleanup()
        del self.page, self.root
        gc.collect()
        self.assertEqual(self.errors, [])

    def select(self, key):
        self.page.table.selection_set(key)
        self.root.update()

    def test_notes_survive_switch_and_save_open_roundtrip(self):
        self.page.metadata['project'].set('Application 1')
        self.page.status.set('Action needed')
        self.page.notes.insert('1.0', 'Owner: Alex\nReview the bracket.')
        self.select('electrical')
        self.assertEqual(self.page.record['items']['mounting']['note'], 'Owner: Alex\nReview the bracket.')
        self.assertTrue(self.page.dirty)
        path = str(Path(self.temp.name) / 'review.json')
        with patch('commissioning_view.filedialog.asksaveasfilename', return_value=path):
            self.assertTrue(self.page.save())
        self.assertFalse(self.page.dirty)
        self.page.new()
        self.assertEqual(self.page.metadata['project'].get(), '')
        with patch('commissioning_view.filedialog.askopenfilename', return_value=path):
            self.page.open()
        self.assertEqual(self.page.metadata['project'].get(), 'Application 1')
        self.assertEqual(self.page.notes.get('1.0', 'end-1c'), 'Owner: Alex\nReview the bracket.')
        self.assertEqual(self.page.status.get(), 'Action needed')
        self.assertFalse(self.page.dirty)

    def test_invalid_open_and_cancelled_save_keep_current_draft(self):
        self.page.metadata['project'].set('Keep this')
        invalid = Path(self.temp.name) / 'invalid.json'
        invalid.write_text('{"version": 99}', encoding='utf-8')
        with patch('commissioning_view.filedialog.askopenfilename', return_value=str(invalid)):
            self.page.open()
        self.assertEqual(self.page.metadata['project'].get(), 'Keep this')
        self.assertIn('Could not open', self.page.error.get())
        with patch('commissioning_view.filedialog.asksaveasfilename', return_value=''):
            self.assertFalse(self.page.save())
        self.assertTrue(self.page.dirty)
        with patch('commissioning_view.messagebox.askyesno', return_value=False):
            self.assertFalse(self.page.confirm_discard())
            self.page.new()
        self.assertEqual(self.page.metadata['project'].get(), 'Keep this')

    def test_filters_empty_state_and_footer_available_at_compact_size(self):
        self.page.filter.set('Open actions')
        self.page._populate()
        self.root.update()
        self.assertEqual(self.page.table.get_children(), ())
        self.assertEqual(str(self.page.state_selector.cget('state')), 'disabled')
        self.page.filter.set('All items')
        self.page._populate()
        self.root.update()
        self.assertEqual(len(self.page.table.get_children()), 14)
        self.assertLess(self.page.save_button.winfo_rooty() + self.page.save_button.winfo_height(),
                        self.root.winfo_rooty() + self.root.winfo_height())
        self.assertGreater(self.page.save_button.winfo_width(), 60)

    def test_csv_export_does_not_mark_draft_saved(self):
        self.page.metadata['project'].set('Still unsaved')
        path = str(Path(self.temp.name) / 'review.csv')
        with patch('commissioning_view.filedialog.asksaveasfilename', return_value=path):
            self.page.export()
        self.assertTrue(Path(path).is_file())
        self.assertTrue(self.page.dirty)
        self.assertIn('unsaved changes', self.page.file_status.get())

    def test_review_rows_are_visible_pointer_targets_beside_evidence(self):
        self.root.lift()
        self.root.update()
        x, y, width, height = self.page.table.bbox('mounting', 'title')
        target = self.root.winfo_containing(self.page.table.winfo_rootx() + x + width // 2,
                                            self.page.table.winfo_rooty() + y + height // 2)
        self.assertIs(target, self.page.table)
        self.assertGreater(self.page.notes.winfo_rootx(), self.page.table.winfo_rootx())
        self.assertLess(self.page.notes.winfo_rooty(), self.page.save_button.winfo_rooty())


if __name__ == '__main__':
    unittest.main()
