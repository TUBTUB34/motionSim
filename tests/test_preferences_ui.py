"""Native saved-address and appearance workflows with isolated preferences."""
from copy import deepcopy
import gc
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app_preferences import Preferences


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class PreferencesUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'preferences.json'
        self.errors = []
        self.discovery = patch('flight_view.FlightWindow._discover')
        self.discovery.start()
        self.start_app()

    def start_app(self):
        import tkinter as tk
        from main import SimulatorApp
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = SimulatorApp(self.root, preferences_path=self.path)
        self.root.after_cancel(self.app.poll_id)
        self.app.show_workspace()
        self.settle()

    def stop_app(self):
        with patch('commissioning_view.messagebox.askyesno', return_value=True):
            self.app.close()
        del self.app, self.root
        gc.collect()

    def tearDown(self):
        self.stop_app()
        self.discovery.stop()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def settle(self):
        self.root.update_idletasks()
        self.root.update()
        self.root.update_idletasks()

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from PreferencesUITests.descendants(child)

    @staticmethod
    def fake_session():
        return SimpleNamespace(events=queue.Queue(), stop_event=threading.Event(),
                               sample=lambda: None, start=Mock(), stop=Mock())

    def save(self, address):
        self.app.ip.set(address)
        self.app.save_address_button.invoke()

    def test_save_invalid_duplicate_ipv6_and_forget_without_starting_robot(self):
        from address_book import SavedAddressCombobox
        field = self.app.setup_entries[str(self.app.ip)]
        self.assertIsInstance(field, SavedAddressCombobox)
        with patch('main.RobotSession') as robot:
            self.save('not-an-ip')
            self.assertTrue(field.instate(['invalid']))
            self.assertIn('valid robot IP', self.app.address_notice.get())
            self.assertFalse(self.path.exists())
            self.save(' 192.0.2.10 ')
            self.save('192.0.2.10')
            self.save('2001:0DB8:0:0::1')
            self.assertEqual(self.app.ip.get(), '2001:db8::1')
            self.assertEqual(self.app.preferences.addresses, ('192.0.2.10', '2001:db8::1'))
            self.assertFalse(field.instate(['invalid']))
            self.assertEqual(tuple(field.cget('values')), self.app.preferences.addresses)
            self.assertFalse(self.app.forget_address_button.instate(['disabled']))
            self.app.forget_address_button.invoke()
            self.assertEqual(Preferences(self.path).addresses, ('192.0.2.10',))
            self.assertTrue(self.app.forget_address_button.instate(['disabled']))
            robot.assert_not_called()
        self.assertIsNone(self.app.session)

    def test_saved_choices_and_theme_survive_complete_application_restart(self):
        self.save('192.0.2.10')
        self.save('2001:db8::1')
        self.app.password.set('never-save-this')
        self.assertTrue(self.app.set_theme('light'))
        self.stop_app()
        self.start_app()
        field = self.app.setup_entries[str(self.app.ip)]
        self.assertEqual(tuple(field.cget('values')), ('192.0.2.10', '2001:db8::1'))
        self.assertEqual(field.get(), '192.0.2.10')
        self.assertEqual(self.root._studio_theme_mode, 'light')
        self.assertFalse(self.app.dark_mode.get())
        self.assertEqual(self.app.password.get(), '')
        self.assertNotIn('never-save-this', self.path.read_text())
        self.assertEqual(set(json.loads(self.path.read_text())), {'version', 'theme', 'addresses'})
        self.assertIsNone(self.app.session)

    def test_existing_ip_selectors_share_saved_choices_without_overwriting_typed_values(self):
        from address_book import SavedAddressCombobox
        for action in (self.app.open_logs, self.app.open_backups,
                       self.app.open_program_check, self.app.open_diagnostics):
            action()
        fields = [widget for widget in self.descendants(self.root)
                  if isinstance(widget, SavedAddressCombobox)]
        self.assertEqual(len(fields), 5)
        main_field = self.app.setup_entries[str(self.app.ip)]
        for field in fields:
            if field is not main_field:
                field.set('192.0.2.99')
        self.save('192.0.2.10')
        for field in fields:
            self.assertEqual(tuple(field.cget('values')), ('192.0.2.10',))
            if field is not main_field:
                self.assertEqual(field.get(), '192.0.2.99')
        self.app.preferences.add_address('2001:db8::2')
        for field in fields:
            self.root.tk.call(field.cget('postcommand'))
            self.assertEqual(tuple(field.cget('values')), ('192.0.2.10', '2001:db8::2'))
        self.app.forget_address_button.invoke()
        for field in fields:
            self.assertEqual(tuple(field.cget('values')), ('2001:db8::2',))

    def test_validated_transfers_remember_addresses_but_never_credentials(self):
        from connection import transfer_connection
        with self.assertRaises(ValueError):
            transfer_connection(self.app, '192.0.2.11', 'root', '')
        self.assertFalse(self.path.exists())
        connection = transfer_connection(self.app, '2001:0DB8::5', 'service', 'transfer-secret')
        self.assertEqual(connection.password, 'transfer-secret')
        self.assertEqual(self.app.preferences.addresses, ('2001:db8::5',))
        saved = self.path.read_text()
        self.assertNotIn('service', saved)
        self.assertNotIn('transfer-secret', saved)

    def test_submit_remembers_valid_address_without_network_and_invalid_submit_does_not(self):
        connection = self.fake_session()
        with patch('main.RobotSession', return_value=connection) as robot:
            self.app.ip.set('')
            self.app.submit()
            robot.assert_not_called()
            self.assertFalse(self.path.exists())
            self.app.ip.set('999.2.3.4')
            self.app.submit()
            robot.assert_not_called()
            self.assertFalse(self.path.exists())
            self.app.ip.set('192.0.2.12')
            self.app.password.set('robot-secret')
            self.app.submit()
            robot.assert_called_once()
            connection.start.assert_called_once_with()
            self.assertIs(self.app.session, connection)
        self.assertEqual(Preferences(self.path).addresses, ('192.0.2.12',))
        self.assertNotIn('robot-secret', self.path.read_text())

    def test_failed_save_retains_saved_settings_and_connection_can_continue(self):
        self.save('192.0.2.10')
        before = self.path.read_bytes()
        connection = self.fake_session()
        with patch('app_preferences.os.replace', side_effect=PermissionError('Read-only folder')), \
                patch('main.messagebox.showerror') as error, \
                patch('main.RobotSession', return_value=connection):
            self.app.ip.set('192.0.2.20')
            self.app.submit()
            error.assert_called_once()
            connection.start.assert_called_once_with()
        self.assertIs(self.app.session, connection)
        self.assertEqual(self.app.preferences.addresses, ('192.0.2.10',))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('Could not save preferences', self.app.address_notice.get())

    def test_failed_theme_save_restores_toggle_without_repainting_or_losing_saved_addresses(self):
        self.save('192.0.2.10')
        before = self.path.read_bytes()
        with patch('app_preferences.os.replace', side_effect=PermissionError('Read-only folder')), \
                patch('main.messagebox.showerror') as error:
            self.app.shell.theme_toggle.invoke()
            error.assert_called_once()
        self.assertTrue(self.app.dark_mode.get())
        self.assertEqual(self.root._studio_theme_mode, 'dark')
        self.assertEqual(self.app.preferences.theme, 'dark')
        self.assertEqual(self.path.read_bytes(), before)

    def test_return_on_address_actions_and_theme_does_not_bubble_to_connect(self):
        self.app.ip.set('192.0.2.10')
        self.assertTrue(self.root.bind('<Return>'))
        with patch('main.RobotSession') as robot:
            for control in (self.app.save_address_button, self.app.forget_address_button,
                            self.app.shell.theme_toggle):
                control.focus_force()
                self.settle()
                control.event_generate('<KeyPress-Return>')
                self.settle()
                robot.assert_not_called()
        self.assertEqual(self.app.preferences.addresses, ())
        self.assertEqual(self.app.preferences.theme, 'light')
        self.assertIsNone(self.app.session)

    def test_theme_changes_keep_loaded_pages_flight_position_draft_and_active_job(self):
        from log_data import load_log
        from tests.test_flight_ui import flight
        log = Path(self.temp.name) / 'log_history.txt'
        log.write_text('3.5 :: 1s :: 2026-04-01 12:00:00 :: 110 :: C153A0:6 :: Synthetic incident\n')
        self.app.open_logs()
        logs = self.app.log_window
        data = load_log(log)
        logs.set_data(data)
        logs.start_date.set('2026-04-01')
        self.app.open_flights()
        flights = self.app.flight_window
        report = flight()
        flights.set_report(report)
        flights.seek(.02)
        self.app.open_checklist()
        checklist = self.app.checklist_page
        checklist.metadata['project'].set('Do not lose this draft')
        checklist.notes.insert('1.0', 'Owner: Alex\nReview the bracket.')
        checklist._sync()
        draft = deepcopy(checklist.record)
        self.app.open_backups()
        backup = self.app.backup_page
        job = SimpleNamespace(events=queue.Queue(), stopped=threading.Event(), cancel=Mock())
        backup.job = job
        backup.status.set('Downloading synthetic backup')
        for mode in ('light', 'dark', 'light'):
            self.assertTrue(self.app.set_theme(mode))
            self.settle()
            self.assertEqual(self.app.active_page, 'backups')
            self.assertEqual(self.root._studio_theme_mode, mode)
            self.assertIs(self.app.log_window, logs)
            self.assertIs(logs.data, data)
            self.assertEqual(logs.start_date.get(), '2026-04-01')
            self.assertIs(self.app.flight_window, flights)
            self.assertIs(flights.report, report)
            self.assertAlmostEqual(flights.timeline.elapsed, .02)
            self.assertIs(self.app.checklist_page, checklist)
            self.assertEqual(checklist.record, draft)
            self.assertTrue(checklist.dirty)
            self.assertIs(self.app.backup_page, backup)
            self.assertIs(backup.job, job)
            job.cancel.assert_not_called()


if __name__ == '__main__':
    unittest.main()
