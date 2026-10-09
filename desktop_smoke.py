"""Offline packaging check, including native charts and the bundled renderer."""
from pathlib import Path
import tempfile
import time


def check(app):
    from log_data import load_log
    from rtde_client import RobotSample
    from view import RobotView

    app.open_logs()
    with tempfile.TemporaryDirectory(prefix='ur-smoke-') as folder:
        path = Path(folder) / 'log.txt'
        path.write_text('3.5 :: 1s :: 2026-04-01 12:00:00 :: 110 :: C153A0:6 :: Smoke test\n', encoding='utf-8')
        window = app.log_window
        window.set_data(load_log(path))
        app.root.update()
        assert len(window.selected) == 1, 'Bundled parser did not produce an incident'
        window.figure.savefig(Path(folder) / 'chart.png')
        viewer = RobotView(app.root, 'UR15')
        viewer.pack(fill='both', expand=True)
        app.root.update()
        viewer.update_robot(RobotSample(1, (0, -1, 1, 0, 0, 0), (.3, -.2, .7, 0, 0, 0), time.monotonic()), None)
        assert viewer.rendered_triangles > 100, 'Bundled robot assets or renderer failed'
