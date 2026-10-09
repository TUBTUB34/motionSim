import os
import unittest

from kinematics import MODELS
from model_catalog import ROBOT_SIZES, model_summary, show_catalog


class CatalogTests(unittest.TestCase):
    def test_all_simulation_models_have_published_dimensions(self):
        self.assertEqual(len(MODELS), 14)
        self.assertEqual(len(ROBOT_SIZES), 22)
        self.assertTrue(set(MODELS) <= ROBOT_SIZES.keys())
        for model in MODELS:
            self.assertGreater(ROBOT_SIZES[model].reach_mm, 0)
            self.assertIn('mm reach', model_summary(model))
        self.assertEqual(ROBOT_SIZES['UR8 Long'].reach_mm, 1750)
        self.assertEqual(ROBOT_SIZES['UR18'].reach_mm, 950)
        self.assertEqual(ROBOT_SIZES['UR7e'].reach_mm, 850)
        self.assertEqual(ROBOT_SIZES['UR12e'].reach_mm, 1300)

    def test_unverified_variants_do_not_silently_use_legacy_geometry(self):
        for name in set(ROBOT_SIZES) - set(MODELS):
            self.assertIn('reference only', ROBOT_SIZES[name].note)
        self.assertEqual(ROBOT_SIZES['UR3e'].reach_mm, 500)
        self.assertEqual(ROBOT_SIZES['UR3e-540'].reach_mm, 540)
        self.assertNotIn('UR3e-540', MODELS)

    @unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires a graphical Tk session')
    def test_catalog_selection_and_compact_controls(self):
        import tkinter as tk
        from tkinter import ttk
        from main import SimulatorApp
        root = tk.Tk()
        app = SimulatorApp(root)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        try:
            catalog = show_catalog(root, app.model)
            root.update()
            tree = next(w for w in descendants(catalog) if isinstance(w, ttk.Treeview))
            choose = next(w for w in descendants(catalog) if isinstance(w, ttk.Button) and w.cget('text') == 'Use selected model')
            tree.selection_set('UR3e-540')
            root.update()
            self.assertTrue(choose.instate(['disabled']))
            tree.selection_set('UR18')
            root.update()
            self.assertFalse(choose.instate(['disabled']))
            choose.invoke()
            self.assertEqual(app.model.get(), 'UR18')
            self.assertIn('950 mm', app.model_details.get())
            app.open_logs()
            window = app.log_window
            window.geometry('1060x740')
            root.update()
            for control in (window.fetch_button, window.open_button, window.csv_button, window.cancel_button):
                self.assertTrue(control.winfo_ismapped())
                self.assertLessEqual(control.winfo_rooty() + control.winfo_height(), window.winfo_rooty() + window.winfo_height())
                self.assertGreaterEqual(control.winfo_height(), 32)
        finally:
            app.close()
