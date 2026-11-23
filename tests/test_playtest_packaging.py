"""Offline packaging checks using fake archives and disposable package directories."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path, PureWindowsPath
import runpy
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE = importlib.util.spec_from_file_location('playtest_verifier', ROOT / 'packaging/verify_playtest.py')
verifier = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(verifier)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        for name in verifier.REQUIRED_RESOURCES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'resource fixture')
        for folder in verifier.RESOURCE_TYPES:
            (self.source / folder).mkdir(exist_ok=True)
        for name in ('icons/items/test.png', 'sounds/test.mp3'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        self.resources = {path.relative_to(self.source).as_posix(): path.read_bytes()
                          for path in verifier.resource_files(self.source)}
        self.toc = dict.fromkeys(set(self.resources) | verifier.REQUIRED_RUNTIME |
                                 {'python312.dll', 'PYZ.pyz'})
        self.modules = {'unlock_ui', 'house_view'}

    def reader(self, _path):
        return SimpleNamespace(toc=self.toc,
                               extract=lambda name: self.resources[name.replace('\\', '/')],
                               open_embedded_archive=lambda name: SimpleNamespace(toc=self.modules))

    def verify(self):
        return verifier.verify_archive(self.source, self.root / 'fake.exe', self.reader)

    def test_hashes_all_resources_and_accepts_windows_archive_paths(self):
        self.toc = {name.replace('/', '\\'): value for name, value in self.toc.items()}
        self.assertEqual(set(self.verify()), set(self.resources))

    def test_spec_uses_its_source_directory_and_historical_build_options(self):
        packaging = self.source / 'packaging'
        packaging.mkdir()
        for name in ('AlibAi-Playtest.spec', 'verify_playtest.py'):
            shutil.copyfile(ROOT / 'packaging' / name, packaging / name)
        captured = {}
        def analysis(scripts, **kwargs):
            captured['scripts'] = scripts
            captured['analysis'] = kwargs
            return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
        def executable(*args, **kwargs):
            captured['exe'] = kwargs
        runpy.run_path(str(packaging / 'AlibAi-Playtest.spec'), init_globals={
            'SPECPATH': str(packaging), 'Analysis': analysis,
            'PYZ': lambda _pure: None, 'EXE': executable,
        })
        self.assertEqual(captured['scripts'], [str(self.source / 'main.py')])
        self.assertEqual({Path(path).relative_to(self.source).as_posix()
                          for path, _destination in captured['analysis']['datas']},
                         set(self.resources))
        self.assertFalse(captured['exe']['console'])
        self.assertFalse(captured['exe']['upx'])
        self.assertEqual(captured['analysis']['hiddenimports'], [])

    def test_missing_or_changed_resource_fails(self):
        name = 'House_images/house-cutaway-v1.png'
        self.toc.pop(name)
        with self.assertRaisesRegex(ValueError, 'missing from archive'):
            self.verify()
        self.toc[name] = None
        self.resources[name] = b'wrong build'
        with self.assertRaisesRegex(ValueError, 'differs from source'):
            self.verify()

    def test_missing_house_source_fails(self):
        (self.source / 'House_images/house-cutaway-v1.png').unlink()
        with self.assertRaisesRegex(ValueError, 'missing from source'):
            self.verify()

    def test_missing_runtime_and_module_fail(self):
        dll = 'PyQt5/Qt5/plugins/audio/qtaudio_wasapi.dll'
        self.toc.pop(dll)
        with self.assertRaisesRegex(ValueError, 'runtime missing'):
            self.verify()
        self.toc[dll] = None
        self.modules.remove('house_view')
        with self.assertRaisesRegex(ValueError, 'modules missing'):
            self.verify()

    def test_forbidden_outer_and_python_names_fail(self):
        for name in ('.env', 'mainCOMP.py', 'tests/test_a.py', 'userprofiles/a.json',
                     'dev-runs/one.json', 'profile.json', '../escape', 'C:/escape'):
            with self.subTest(name=name):
                self.toc[name] = None
                with self.assertRaisesRegex(ValueError, 'unsafe paths'):
                    self.verify()
                del self.toc[name]
        self.modules.add('tests.test_secret')
        with self.assertRaisesRegex(ValueError, 'unsafe paths'):
            self.verify()

    def test_extra_resource_and_private_source_fail(self):
        self.toc['icons/extra.png'] = None
        with self.assertRaisesRegex(ValueError, 'Unexpected bundled resource'):
            self.verify()
        self.toc.pop('icons/extra.png')
        # Names are inspected, never contents of credential-like files.
        (self.source / 'icons/.env').touch()
        with self.assertRaisesRegex(ValueError, 'Private/generated resource'):
            self.verify()

    def test_duplicate_normalized_archive_paths_fail(self):
        self.toc['icons\\items\\test.png'] = None
        with self.assertRaisesRegex(ValueError, 'Duplicate normalized'):
            self.verify()

    def test_revision_checks_dirty_tree_and_mismatched_head(self):
        revision = 'a' * 40
        for outputs in ((revision + '\n', ' M main.py\n'), ('b' * 40 + '\n',)):
            with self.subTest(outputs=outputs), patch.object(verifier.subprocess, 'run') as run:
                run.side_effect = [SimpleNamespace(stdout=value) for value in outputs]
                with self.assertRaises(ValueError):
                    verifier.check_revision(self.source, revision)
        with self.assertRaisesRegex(ValueError, 'full lowercase'):
            verifier.check_revision(self.source, 'short')

    def test_revision_uses_git_canonical_windows_trust_path(self):
        source = PureWindowsPath('C:/demo sources/AlibAi')
        revision = 'a' * 40
        with patch.object(verifier.subprocess, 'run') as run:
            run.side_effect = [SimpleNamespace(stdout=revision + '\n'),
                               SimpleNamespace(stdout='')]
            verifier.check_revision(source, revision)
        prefix = ['git', '-c', 'safe.directory=C:/demo sources/AlibAi',
                  '-C', str(source)]
        self.assertEqual(run.call_args_list[0].args[0], prefix + ['rev-parse', 'HEAD'])
        self.assertEqual(run.call_args_list[1].args[0],
                         prefix + ['status', '--porcelain', '--untracked-files=all'])

    def test_manifest_records_only_performed_checks_and_requires_layout(self):
        package = self.root / 'package'
        (package / 'dist').mkdir(parents=True)
        executable = package / 'dist/AlibAi-Playtest.exe'
        executable.write_bytes(b'fake executable')
        launcher = package / 'Launch-Playtest.ps1'
        launcher.write_bytes(b'fake launcher')
        (self.source / 'packaging').mkdir()
        (self.source / 'packaging/Launch-Playtest.ps1').write_bytes(launcher.read_bytes())
        manifest = package / 'PLAYTEST.json'
        args = ['--source', str(self.source), '--exe', str(executable),
                '--launcher', str(launcher), '--revision', 'a' * 40,
                '--manifest', str(manifest)]
        with patch.object(verifier, 'check_revision'), patch.object(verifier, 'verify_archive', return_value={}), contextlib.redirect_stdout(io.StringIO()):
            verifier.main(args)
        document = json.loads(manifest.read_text())
        self.assertEqual(document['exe_sha256'], verifier.digest(executable.read_bytes()))
        self.assertNotIn('offline_tests', document)
        self.assertNotIn('fresh_independent_review', document)
        manifest.unlink()
        launcher.write_bytes(b'drifted launcher')
        with patch.object(verifier, 'check_revision'), patch.object(verifier, 'verify_archive', return_value={}), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                verifier.main(args)
        self.assertFalse(manifest.exists())
        launcher.write_bytes(b'fake launcher')
        with patch.object(verifier, 'check_revision', side_effect=ValueError('dirty')), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                verifier.main(args)
        self.assertFalse(manifest.exists())
        args[-1] = str(package / 'wrong.json')
        with patch.object(verifier, 'check_revision'), patch.object(verifier, 'verify_archive', return_value={}), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                verifier.main(args)


@unittest.skipUnless(shutil.which('powershell'), 'Windows PowerShell is required')
class LauncherTests(unittest.TestCase):
    def test_verify_only_needs_no_key_and_rejects_bad_metadata_or_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / 'dist').mkdir()
            executable = package / 'dist/AlibAi-Playtest.exe'
            executable.write_bytes(b'not an executable; verify-only must never start it')
            launcher = package / 'Launch-Playtest.ps1'
            shutil.copyfile(ROOT / 'packaging/Launch-Playtest.ps1', launcher)
            metadata = {'schema_version': 1, 'source_commit': 'a' * 40,
                        'exe_sha256': verifier.digest(executable.read_bytes()),
                        'launcher_sha256': verifier.digest(launcher.read_bytes())}
            environment = dict(os.environ)
            environment.pop('OPENAI_API_KEY', None)
            def run():
                (package / 'PLAYTEST.json').write_text(json.dumps(metadata))
                return subprocess.run(['powershell', '-NoProfile', '-NonInteractive',
                                       '-ExecutionPolicy', 'Bypass', '-File', str(launcher), '-VerifyOnly'],
                                      env=environment, capture_output=True, text=True)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata['source_commit'] = 'short'
            self.assertNotEqual(run().returncode, 0)
            metadata['source_commit'] = 'a' * 40
            metadata['exe_sha256'] = '0' * 64
            self.assertNotEqual(run().returncode, 0)


if __name__ == '__main__':
    unittest.main()
