"""Compact desktop layouts must retain access to real controls and workflows."""
import os
import unittest


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires a graphical Tk session')
class CompactDesignTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from main import SimulatorApp
        self.root = tk.Tk()
        self.app = SimulatorApp(self.root)

    def tearDown(self):
        self.app.close()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def settle(self):
        self.root.update_idletasks()
        self.root.update()
        self.root.update_idletasks()

    def assert_fully_visible(self, widget, viewport):
        self.assertTrue(widget.winfo_ismapped())
        self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
        self.assertGreaterEqual(widget.winfo_rootx(), viewport.winfo_rootx())
        self.assertGreaterEqual(widget.winfo_rooty(), viewport.winfo_rooty())
        self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),
                             viewport.winfo_rootx()+viewport.winfo_width()+1)
        self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),
                             viewport.winfo_rooty()+viewport.winfo_height()+1)

    def live_view(self):
        from connection import make_settings
        self.app.settings = make_settings('127.0.0.1', '', '', '')
        self.app.show_view()
        self.root.geometry('980x620')
        self.settle()

    def test_catalog_keeps_actions_below_long_reference_details(self):
        from tkinter import ttk
        from model_catalog import show_catalog
        window = show_catalog(self.root, self.app.model)
        try:
            window.geometry('820x580')
            tree = next(w for w in self.descendants(window) if isinstance(w, ttk.Treeview))
            tree.selection_set('UR3e-540')
            self.settle()
            actions = [w for w in self.descendants(window) if isinstance(w, ttk.Button)]
            for action in actions:
                self.assert_fully_visible(action, window)
            choose = next(w for w in actions if w.cget('text') == 'Use selected model')
            self.assertTrue(choose.instate(['disabled']))
            tree.selection_set('UR18')
            self.settle()
            choose.invoke()
            self.assertEqual(self.app.model.get(), 'UR18')
        finally:
            if window.winfo_exists():
                window.destroy()

    def test_last_tool_control_is_revealed_by_keyboard_focus(self):
        from tkinter import ttk
        self.live_view()
        self.app.tabs.select(self.app.options_tab)
        self.settle()
        selector = next(w for w in self.descendants(self.app.options_tab)
                        if isinstance(w, ttk.Combobox) and w.get() == 'Installation TCP')
        selector.focus_force()
        self.settle()
        self.assert_fully_visible(selector, self.app.options_tab.canvas)
        self.assertEqual(self.root.focus_get(), selector)

    def test_camera_reset_remains_reachable_with_populated_telemetry(self):
        from tkinter import ttk
        self.live_view()
        self.app.joints.set('\n'.join(f'Joint {i}: 120.00 degrees' for i in range(6)))
        self.app.tcp_text.set('\n'.join(f'{axis}: 0.12345' for axis in ('X', 'Y', 'Z', 'RX', 'RY', 'RZ')))
        self.app.details.set('Saved installation details\n' * 12)
        self.settle()
        reset = next(w for w in self.descendants(self.app.sidebar_scroll)
                     if isinstance(w, ttk.Button) and w.cget('text') == 'Reset camera')
        reset.focus_force()
        self.settle()
        self.assert_fully_visible(reset, self.app.sidebar_scroll.canvas)
        self.app.viewer.zoom = 2.4
        reset.invoke()
        self.assertEqual(self.app.viewer.zoom, 1.35)

    def test_setup_validation_keeps_connect_and_offline_actions_visible(self):
        from tkinter import ttk
        self.root.geometry('760x720')
        self.app.ip.set('999.2.3.4')
        self.app.submit()
        self.settle()
        self.assertIsNone(self.app.session)
        entry = self.app.setup_entries[str(self.app.ip)]
        self.assertTrue(entry.instate(['invalid']))
        self.assertTrue(self.app.error.get())
        names = {'Connect to robot', 'Logs & graphs', 'Flight reports & playback'}
        actions = [w for w in self.descendants(self.root)
                   if isinstance(w, ttk.Button) and w.cget('text') in names]
        self.assertEqual(len(actions), 3)
        for action in actions:
            self.assert_fully_visible(action, self.root)

    def test_workspace_shortcut_respects_modal_confirmation(self):
        from unittest.mock import Mock
        from ui_dialogs import MessageDialog
        action = Mock()
        dialog = MessageDialog(self.root, 'Confirm', 'Choose an action', question=True)
        dialog.deiconify()
        self.settle()
        dialog.grab_set()
        self.app.workspace_shortcut(action)
        action.assert_not_called()
        dialog.destroy()
        self.app.workspace_shortcut(action)
        action.assert_called_once()

    def test_simulator_full_backup_uses_active_robot_and_selected_folder(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from tkinter import ttk
        from connection import make_settings
        from backup_transfer import BackupDestination
        self.app.settings = make_settings('127.0.0.2', 'root', 'password', '')
        self.app.show_view()
        self.root.geometry('980x620')
        self.settle()
        button = next(w for w in self.descendants(self.app.simulator_tab)
                      if isinstance(w, ttk.Button) and w.cget('text') == 'Full backup…')
        self.assert_fully_visible(button, self.root)
        with tempfile.TemporaryDirectory() as folder, patch('backup_transfer.BackupJob.start') as start, patch(
                'log_view.filedialog.askdirectory', return_value=folder):
            previous = Path(folder) / 'full-backup.tar'
            previous.write_bytes(b'existing archive')
            button.invoke()
            window = self.app.log_window
            start.assert_called_once()
            self.assertTrue(window.job.full)
            self.assertEqual(window.job.connection.ip, '127.0.0.2')
            self.assertEqual(window.job.connection.password, 'password')
            self.assertEqual(window.save_directory, Path(folder))
            self.assertTrue(window.full_backup_button.instate(['disabled']))
            question = BackupDestination(filename='full-backup.tar')
            window.job.events.put(('backup_destination', question))
            window.after_cancel(window.poll_id)
            window.poll()
            self.assertTrue(question.answered.is_set())
            self.assertEqual(Path(question.path).parent, Path(folder))
            self.assertNotEqual(Path(question.path), previous)
            self.assertEqual(previous.read_bytes(), b'existing archive')
            window.finish()
            self.assertFalse(window.full_backup_button.instate(['disabled']))

    def test_cancel_full_backup_folder_does_not_start_robot_work(self):
        from unittest.mock import patch
        from connection import make_settings
        self.app.settings = make_settings('127.0.0.2', 'root', 'password', '')
        with patch('backup_transfer.BackupJob.start') as start, patch(
                'log_view.filedialog.askdirectory', return_value=''):
            self.app.make_full_backup()
            start.assert_not_called()
            self.assertIsNone(self.app.log_window.job)
            self.assertIn('No robot archive created', self.app.log_window.status.get())


if __name__ == '__main__':
    unittest.main()
