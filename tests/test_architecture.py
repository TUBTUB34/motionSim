"""Package dependency rules, checked on every import in the source (including imports inside functions).

    core          pure robot domain: kinematics, telemetry, installation, playback, paths, rendering lock
    ui            shared Tk toolkit: theme, dialogs, widgets, chart styling and canvas
    remote        SSH infrastructure: connection settings, host keys, streams, AsyncSSH, the SSH job base
    reporting     the shared A4 PDF frame
    viewer        the 3D robot view
    live, flight, logs, programs, diagnostics, backup, commissioning
                  features
    app           composition root: the main window, navigation, help, smoke check

Shared packages never import features or the app. A feature uses another feature only
where the domain really links them (FEATURE_DEPENDENCIES), and then never its pages
(``view`` modules); only the app wires pages together.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'motionstudio'
SHARED = {
    'core': {'core'},
    'ui': {'core', 'ui'},
    'remote': {'core', 'remote'},
    'reporting': {'core', 'reporting'},
    'viewer': {'core', 'ui', 'viewer'},
}
FEATURES = {'live', 'flight', 'logs', 'programs', 'diagnostics', 'backup', 'commissioning'}
FEATURE_DEPENDENCIES = {
    # Robot Status analyses flight recordings and robot logs and reuses the program report's line ranges.
    'diagnostics': {'flight', 'logs', 'programs'},
    # Live recordings are written in the flight-report format.
    'live': {'flight'},
    # The Logs page saves the robot's log pair with the backup job.
    'logs': {'backup'},
    # Program reports list the deployment checks of the commissioning checklist.
    'programs': {'commissioning'},
}


def is_page(module):
    return module.split('.')[-1] == 'view' or module.endswith('_view')


def imports(path):
    """(line, imported motionstudio module) for every import in a source file."""
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # "from motionstudio.ui import theme" imports the module motionstudio.ui.theme;
            # "from motionstudio.ui.theme import flow" imports a name from motionstudio.ui.theme.
            names = [f'{node.module}.{alias.name}' if ROOT.parent.joinpath(*node.module.split('.'), f'{alias.name}.py').is_file()
                     else node.module for alias in node.names]
        else:
            continue
        for name in names:
            if name.startswith('motionstudio.'):
                yield node.lineno, name.removeprefix('motionstudio.')


def violations():
    found = []
    for path in sorted(ROOT.rglob('*.py')):
        parts = path.relative_to(ROOT).with_suffix('').parts
        if len(parts) < 2:
            continue
        package, module = parts[0], '.'.join(parts)
        for line, target in imports(path):
            target_package = target.split('.')[0]
            if target_package == package:
                continue
            if package in SHARED:
                allowed = target_package in SHARED[package]
            elif package in FEATURES:
                allowed = target_package in SHARED or (
                    target_package in FEATURE_DEPENDENCIES.get(package, ()) and not is_page(target))
            else:  # app
                allowed = True
            if target_package == 'app' and package != 'app':
                allowed = False
            if not allowed:
                found.append(f'{module}:{line} imports {target}')
    return list(dict.fromkeys(found))


class ArchitectureTests(unittest.TestCase):
    def test_package_dependency_rules(self):
        self.assertEqual(violations(), [])

    def test_every_package_is_classified(self):
        packages = {path.name for path in ROOT.iterdir() if (path / '__init__.py').is_file()}
        self.assertEqual(packages, set(SHARED) | FEATURES | {'app'})


if __name__ == '__main__':
    unittest.main()
