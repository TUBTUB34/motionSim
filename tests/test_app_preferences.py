"""Persistent address/theme behavior and failed-write preservation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_preferences import MAX_ADDRESSES, MAX_FILE_BYTES, Preferences, default_preferences_path


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'settings' / 'preferences.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_new_settings_do_not_write_until_explicit_change(self):
        preferences = Preferences(self.path)
        self.assertEqual(preferences.theme, 'dark')
        self.assertEqual(preferences.addresses, ())
        self.assertEqual(preferences.load_error, '')
        self.assertFalse(self.path.exists())

    def test_addresses_and_theme_survive_reopen_without_credentials(self):
        preferences = Preferences(self.path)
        preferences.add_address(' 192.168.0.10 ')
        self.assertEqual(preferences.add_address('2001:0DB8:0:0::1'), '2001:db8::1')
        preferences.add_address('2001:db8::1')
        preferences.set_theme('light')
        preferences.password = 'must-not-be-saved'
        preferences.username = 'operator'
        preferences.add_address('192.168.0.10')
        reopened = Preferences(self.path)
        self.assertEqual(reopened.addresses, ('192.168.0.10', '2001:db8::1'))
        self.assertEqual(reopened.theme, 'light')
        saved = json.loads(self.path.read_text())
        self.assertEqual(set(saved), {'version', 'theme', 'addresses'})
        self.assertNotIn('must-not-be-saved', self.path.read_text())
        self.assertFalse(hasattr(reopened, 'password'))

    def test_removing_one_address_preserves_others_and_theme(self):
        preferences = Preferences(self.path)
        preferences.add_address('192.168.0.10')
        preferences.add_address('2001:db8::1')
        preferences.set_theme('light')
        self.assertTrue(preferences.remove_address('2001:0DB8:0:0::1'))
        self.assertFalse(preferences.remove_address('192.168.0.11'))
        reopened = Preferences(self.path)
        self.assertEqual(reopened.addresses, ('192.168.0.10',))
        self.assertEqual(reopened.theme, 'light')

    def test_invalid_inputs_never_change_saved_state(self):
        preferences = Preferences(self.path)
        preferences.add_address('192.168.0.10')
        before = self.path.read_bytes()
        for value in ('', 'robot.local', 'ssh://192.168.0.1', '192.168.0.1:22', '999.2.3.4',
                      '192.168.0.01', 'fe80::1%invalid scope', 'fe80::1%a\x00', 1234, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                preferences.add_address(value)
        with self.assertRaises(ValueError):
            preferences.set_theme('automatic')
        self.assertEqual(preferences.addresses, ('192.168.0.10',))
        self.assertEqual(preferences.theme, 'dark')
        self.assertEqual(self.path.read_bytes(), before)

    def test_full_list_never_silently_forgets_a_robot(self):
        preferences = Preferences(self.path)
        for index in range(MAX_ADDRESSES):
            preferences.add_address(f'192.168.0.{index + 1}')
        before = self.path.read_bytes()
        preferences.add_address('192.168.0.1')
        with self.assertRaisesRegex(ValueError, 'Remove one'):
            preferences.add_address('192.168.1.1')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(preferences.addresses), MAX_ADDRESSES)

    def test_failed_replace_preserves_disk_memory_and_removes_temporary_file(self):
        preferences = Preferences(self.path)
        preferences.add_address('192.168.0.10')
        before = self.path.read_bytes()
        for change in (lambda: preferences.add_address('192.168.0.11'),
                       lambda: preferences.remove_address('192.168.0.10'),
                       lambda: preferences.set_theme('light')):
            with patch('app_preferences.os.replace', side_effect=PermissionError('Read-only folder')):
                with self.assertRaisesRegex(OSError, 'previous saved settings are unchanged'):
                    change()
            self.assertEqual(preferences.addresses, ('192.168.0.10',))
            self.assertEqual(preferences.theme, 'dark')
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_new_directory_does_not_claim_saved_preference(self):
        preferences = Preferences(self.path)
        with patch('app_preferences.Path.mkdir', side_effect=PermissionError('Permission denied')):
            with self.assertRaisesRegex(OSError, 'Could not save preferences'):
                preferences.set_theme('light')
        self.assertEqual(preferences.theme, 'dark')
        self.assertFalse(self.path.exists())

    def test_bad_files_use_defaults_without_overwriting_original_and_recover_explicitly(self):
        self.path.parent.mkdir()
        cases = (b'not JSON', b'\xff', b' ' * (MAX_FILE_BYTES + 1), b'[]',
                 b'[' * 2000 + b']' * 2000,
                 b'{"version":true}', b'{"version":2}',
                 b'{"version":1,"theme":"neon"}',
                 b'{"version":1,"addresses":["bad-ip"]}')
        for raw in cases:
            with self.subTest(raw=raw[:60]):
                self.path.write_bytes(raw)
                preferences = Preferences(self.path)
                self.assertEqual(preferences.addresses, ())
                self.assertEqual(preferences.theme, 'dark')
                self.assertIn('Could not read saved preferences', preferences.load_error)
                self.assertEqual(self.path.read_bytes(), raw)
                preferences.add_address('192.168.0.10')
                self.assertEqual(preferences.load_error, '')
                self.assertEqual(Preferences(self.path).addresses, ('192.168.0.10',))

    def test_load_deduplicates_and_only_rewrites_known_fields(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({'version': 1, 'theme': 'light',
            'addresses': ['2001:0DB8::1', '2001:db8::1', '192.168.0.10'], 'password': 'not-accepted'}))
        preferences = Preferences(self.path)
        self.assertEqual(preferences.addresses, ('2001:db8::1', '192.168.0.10'))
        self.assertFalse(hasattr(preferences, 'password'))
        preferences.set_theme('dark')
        self.assertNotIn('password', json.loads(self.path.read_text()))

    def test_platform_paths_are_user_local_and_ignore_relative_xdg_directory(self):
        home = Path(self.temp.name) / 'home'
        config = Path(self.temp.name) / 'custom-config'
        with patch('app_preferences.Path.home', return_value=home), patch.dict('os.environ', {'MOTIONSIM_PREFERENCES_PATH': ''}):
            with patch('app_preferences.sys.platform', 'linux'), patch.dict('os.environ', {'XDG_CONFIG_HOME': str(config)}):
                self.assertEqual(default_preferences_path(), config / 'URMotionStudio/preferences.json')
            with patch('app_preferences.sys.platform', 'linux'), patch.dict('os.environ', {'XDG_CONFIG_HOME': 'relative'}):
                self.assertEqual(default_preferences_path(), home / '.config/URMotionStudio/preferences.json')
            with patch('app_preferences.sys.platform', 'darwin'):
                self.assertEqual(default_preferences_path(), home / 'Library/Application Support/URMotionStudio/preferences.json')
            with patch('app_preferences.sys.platform', 'win32'), patch.dict('os.environ', {'LOCALAPPDATA': '/custom/local'}):
                self.assertEqual(default_preferences_path(), Path('/custom/local/URMotionStudio/preferences.json'))
            with patch('app_preferences.sys.platform', 'win32'), patch.dict('os.environ', {'LOCALAPPDATA': ''}):
                self.assertEqual(default_preferences_path(), home / 'AppData/Local/URMotionStudio/preferences.json')

    def test_explicit_path_precedes_environment_override_and_override_precedes_platform(self):
        override = Path(self.temp.name) / 'isolated' / 'preferences.json'
        with patch.dict('os.environ', {'MOTIONSIM_PREFERENCES_PATH': str(override)}):
            self.assertEqual(default_preferences_path(), override)
            self.assertEqual(Preferences().path, override)
            explicit = Preferences(self.path)
            explicit.add_address('192.168.0.10')
            self.assertEqual(explicit.path, self.path)
            self.assertFalse(override.exists())


if __name__ == '__main__':
    unittest.main()
