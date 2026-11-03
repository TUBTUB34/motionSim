"""Offline packaging check, including native charts and the bundled renderer."""
from pathlib import Path
import tempfile
import time


def check(app):
    from log_data import load_log
    from rtde_client import RobotSample
    from view import RobotView
    from kinematics import MODELS
    from robot_assets import load_robot_mesh

    assert app.active_page == 'home', 'Home must expose the offline workflows at launch'
    assert app.session is None, 'Startup must not initiate a robot connection'
    app.ip.set('192.0.2.42')
    app.save_address()
    assert app.preferences.addresses == ('192.0.2.42',), 'Saved IP list failed'
    assert app.set_theme('light'), 'Light mode failed'
    from app_preferences import Preferences
    saved = Preferences(app.preferences.path)
    assert saved.theme == 'light' and saved.addresses == ('192.0.2.42',), 'Preferences did not survive reload'
    app.open_logs()
    with tempfile.TemporaryDirectory(prefix='ur-smoke-') as folder:
        app.open_checklist()
        checklist = app.checklist_page
        checklist.metadata['project'].set('Packaging smoke test')
        checklist.path = Path(folder) / 'checklist.json'
        assert checklist.save(), 'Bundled checklist save failed'
        from commissioning import load_record, export_record_csv
        record = load_record(checklist.path)
        assert record['metadata']['project'] == 'Packaging smoke test'
        export_record_csv(Path(folder) / 'checklist.csv', record)
        assert len(record['items']) == 14, 'Bundled checklist source catalog is missing'
        app.open_home()
        app.home_page.task_buttons['Logs & incidents'].invoke()
        assert app.active_page == 'logs', 'Home task navigation failed'
        path = Path(folder) / 'log.txt'
        path.write_text('3.5 :: 1s :: 2026-04-01 12:00:00 :: 110 :: C153A0:6 :: Smoke test\n', encoding='utf-8')
        window = app.log_window
        window.set_data(load_log(path))
        app.root.update()
        assert len(window.selected) == 1, 'Bundled parser did not produce an incident'
        window.aggregate.set(True)
        window.refresh()
        assert sum(bar.get_height() for bar in window.axes.patches) == 1, 'Aggregate chart lost incidents'
        window.figure.savefig(Path(folder) / 'chart.png')
        # Exercise the bundled flight parser and vector PDF backend without
        # packaging private user recordings or contacting a robot.
        from flight_data import load_flight
        from flight_pdf import export_flight_pdf
        flight_path = Path(folder) / 'flight.csv'
        flight_path.write_text('Timestamp [s],Actual position j0 [rad]\n1,0.1\n1.002,0.2\n', encoding='utf-8')
        report = load_flight(flight_path)
        report_path = export_flight_pdf(report, Path(folder) / 'flight.pdf')
        assert report_path.read_bytes().startswith(b'%PDF-'), 'Bundled flight PDF export failed'
        app.open_flights()
        flights = app.flight_window
        flights.set_report(report)
        flights.seek(.001)
        flights.view_mode.set('Compare joints')
        flights.change_mode()
        flights.focus.set('Torque')
        flights.redraw_charts()
        app.root.update()
        assert flights.report is report, 'Bundled flight workspace failed'
        flights.compare_layout.set('Separate joints')
        flights.redraw_charts()
        assert len(flights.charts.axes) == 6, 'Bundled separate joint graphs failed'
        flights.show_simulator()
        app.root.update()
        assert app.recorded_panel.mounting.get() == 'TCP mounting'
        assert app.recorded_source is flights, 'Bundled main-window recorded playback failed'
        assert 'model unavailable' in app.recorded_panel.note.get().lower()
        from program_check import CATEGORIES, discover_programs, review_program
        from program_check_pdf import export_program_pdf
        from program_transfer import ProgramSourceJob
        assert ProgramSourceJob, 'Bundled program retrieval module is missing'
        program = Path(folder) / 'packaging.script'
        program.write_text(
            'set_payload(1, [0,0,.1])\n'
            'base=1.5\nacceleration=base*2\nspeed=sqrt(4)/2\n'
            'movel(p[0,0,0,0,0,0], a=acceleration, v=speed)\n'
            'movel(p[.03,.04,0,0,0,0], a=acceleration, v=speed)\n',
            encoding='utf-8')
        program.with_suffix('.urp').write_text('<URProgram name="packaging" lastSavedIn="5.24"/>', encoding='utf-8')
        pairs = discover_programs(program)
        review = review_program(pairs[0], limits={'tcp_velocity': .8})
        assert any(f.category == CATEGORIES[4] and f.status == 'Review recommended'
                   and 'a=3 m/s²' in f.detail for f in review.findings), 'Bundled acceleration calculation failed'
        assert any(f.category == CATEGORIES[7] and f.status == 'Review recommended'
                   for f in review.findings), 'Bundled velocity comparison failed'
        assert any(f.category == CATEGORIES[8] and 'move distance=0.05 m' in f.detail
                   for f in review.findings), 'Bundled move-distance calculation failed'
        program_pdf = export_program_pdf(review, Path(folder) / 'program.pdf')
        assert program_pdf.read_bytes().startswith(b'%PDF-'), 'Bundled program PDF export failed'
        app.open_program_check()
        app.program_check_window.executor.submit(lambda: None).result(timeout=10)
        app.root.update()
        app.program_check_window.set_pairs(pairs)
        assert app.program_check_window.review is None, 'Source selection must wait for a URP choice'
        app.program_check_window.combo.current(0)
        app.program_check_window.show_review(review)
        assert 'without installation' in app.program_check_window.warning.get()
        app.program_check_window.withdraw()
        from diagnostic_transfer import DiagnosticSourceJob
        from robot_diagnostics import analyze_directory
        from diagnostic_pdf import export_diagnostic_pdf
        assert DiagnosticSourceJob, 'Bundled diagnostic retrieval module is missing'
        (Path(folder) / 'log_history.txt').write_bytes(path.read_bytes())
        diagnostic = analyze_directory(folder)
        app.open_diagnostics()
        app.diagnostic_window.set_report(diagnostic)
        assert len(app.diagnostic_window.robot_status.joints) == 6, 'Bundled Robot Status must assess all six joints'
        app.open_backups()
        assert app.backup_page.job is None, 'Opening Backups must not start a robot operation'
        app.open_diagnostics()
        assert app.diagnostic_window.report is diagnostic, 'Page navigation lost the generated status report'
        app.root.update()
        diagnostic_pdf = export_diagnostic_pdf(diagnostic, Path(folder) / 'diagnostic.pdf')
        assert diagnostic_pdf.read_bytes().startswith(b'%PDF-'), 'Bundled diagnostic PDF export failed'
        app.diagnostic_window.withdraw()
        flights.open_remote_reports()
        retrieval = app.flight_retrieval_window
        app.root.update()
        assert retrieval.refresh_button.instate(['disabled']), 'Offline retrieval must not connect'
        from transfer_stream import copy_remote
        from io import BytesIO
        saved = BytesIO()
        assert copy_remote(BytesIO(b'packaged'), saved, 8) == 8
        assert saved.getvalue() == b'packaged', 'Bundled transfer helper failed'
        app.open_help('Compare joints')
        assert 'Target' in app.help_window.text.get('1.0', 'end'), 'Bundled guide is missing'
        app.help_window.withdraw()
        retrieval.hide()
        flights.hide()
        for model in MODELS:
            assert len(load_robot_mesh(model)) == 7, f'Bundled mesh missing: {model}'
        viewer = RobotView(app.root, 'UR15')
        viewer.pack(fill='both', expand=True)
        app.root.update()
        viewer.update_robot(RobotSample(1, (0, -1, 1, 0, 0, 0), (.3, -.2, .7, 0, 0, 0), time.monotonic()), None)
        assert viewer.rendered_triangles > 100, 'Bundled robot assets or renderer failed'
        assert app.set_theme('dark'), 'Dark mode failed with loaded workspaces'
        assert app.flight_window.report is report, 'Theme change lost recorded evidence'
