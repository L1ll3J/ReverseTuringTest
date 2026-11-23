"""Offline bundle consistency checks. No application startup or credentials required."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


RESOURCE_TYPES = {
    'prompts': {'.xml'}, 'House_images': {'.png'}, 'fonts': {'.ttf'},
    'icons': {'.png'}, 'sounds': {'.mp3', '.wav'},
}
REQUIRED_RESOURCES = {
    'System Prompt.txt', 'prompts/speech.xml', 'prompts/vote.xml',
    'prompts/director.xml', 'prompts/muffled.xml',
    'prompts/current_infatuation.xml', 'prompts/current_bitterness.xml',
    'fonts/PressStart2P-Regular.ttf', 'House_images/house-cutaway-v1.png',
}
REQUIRED_RUNTIME = {
    'PyQt5/Qt5/bin/Qt5Core.dll', 'PyQt5/Qt5/bin/Qt5Gui.dll',
    'PyQt5/Qt5/bin/Qt5Widgets.dll', 'PyQt5/Qt5/bin/Qt5Network.dll',
    'PyQt5/Qt5/bin/Qt5Multimedia.dll',
    'PyQt5/Qt5/plugins/platforms/qwindows.dll',
    'PyQt5/Qt5/plugins/audio/qtaudio_wasapi.dll',
    'PyQt5/Qt5/plugins/audio/qtaudio_windows.dll',
    'PyQt5/Qt5/plugins/mediaservice/qtmedia_audioengine.dll',
    'PyQt5/Qt5/plugins/mediaservice/wmfengine.dll',
    'PyQt5/Qt5/plugins/mediaservice/dsengine.dll',
    'PyQt5/Qt5/bin/MSVCP140.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll',
    '_ssl.pyd', 'libssl-3.dll', 'libcrypto-3.dll', 'certifi/cacert.pem',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def forbidden(name):
    normalized = name.replace('\\', '/').lower()
    parts = PurePosixPath(normalized).parts
    if '..' in parts or normalized.startswith('/') or ':' in normalized:
        return True
    for part in parts:
        if (part == '.env' or part.startswith('.env.') or
                part in {'maincomp', 'maincomp.py', 'tests', 'userprofiles',
                         'dev-runs', 'traces', 'profile.json', 'settings.ini'}):
            return True
    # PYZ entries are module names rather than filesystem paths.
    return normalized.split('.')[0] in {'tests', 'maincomp', 'userprofiles'}


def resource_files(root):
    """Only approved resource types, with no links or private/generated files."""
    root = Path(root).resolve()
    files = [root / 'System Prompt.txt']
    for folder, extensions in RESOURCE_TYPES.items():
        directory = root / folder
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f'Missing or linked resource directory: {folder}')
        for path in sorted(directory.rglob('*')):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError(f'Linked resource is not permitted: {relative}')
            if forbidden(relative):
                raise ValueError(f'Private/generated resource is not permitted: {relative}')
            if path.is_file() and path.suffix.lower() in extensions:
                files.append(path)
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or linked required resource')
    return files


def verify_archive(source, executable, reader_factory=None):
    if reader_factory is None:
        from PyInstaller.archive.readers import CArchiveReader
        reader_factory = CArchiveReader
    archive = reader_factory(str(executable))
    names = {name.replace('\\', '/'): name for name in archive.toc}
    if len(names) != len(archive.toc):
        raise ValueError('Duplicate normalized archive paths')
    modules = set(archive.open_embedded_archive('PYZ.pyz').toc)
    if any(forbidden(name) for name in (*names, *modules)):
        raise ValueError('Private/generated data or unsafe paths found in archive')
    if not {'unlock_ui', 'house_view'} <= modules:
        raise ValueError('Required presentation modules missing from archive')
    missing = REQUIRED_RUNTIME - names.keys()
    if missing or not any(re.fullmatch(r'python3\d+\.dll', name) for name in names):
        raise ValueError('Required Qt, multimedia, SSL or Python runtime missing')
    resources = {path.relative_to(source).as_posix(): path for path in resource_files(source)}
    if not REQUIRED_RESOURCES <= resources.keys():
        raise ValueError('Required final demo resources missing from source')
    hashes = {}
    for name, path in resources.items():
        if name not in names:
            raise ValueError(f'Resource missing from archive: {name}')
        expected = digest(path.read_bytes())
        if digest(archive.extract(names[name])) != expected:
            raise ValueError(f'Bundled resource differs from source: {name}')
        hashes[name] = expected
    # Resource directories may not conceal extra files outside the source manifest.
    for name in names:
        if name.split('/')[0] in RESOURCE_TYPES and name not in resources:
            raise ValueError(f'Unexpected bundled resource: {name}')
    return hashes


def check_revision(source, revision):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('A full lowercase source revision is required')
    def git(*args):
        # Trust only this explicitly selected worktree, without global config edits.
        return subprocess.run(['git', '-c', f'safe.directory={source.as_posix()}', '-C', str(source), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    if git('rev-parse', 'HEAD') != revision:
        raise ValueError('Source HEAD differs from requested revision')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Source tree has changes; refusing a pristine revision manifest')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--launcher', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args(argv)
    source = args.source.resolve()
    try:
        check_revision(source, args.revision)
        executable_hash = digest(args.exe.read_bytes())
        resources = verify_archive(source, args.exe)
        launcher_hash = digest(args.launcher.read_bytes())
        if launcher_hash != digest((source / 'packaging/Launch-Playtest.ps1').read_bytes()):
            raise ValueError('Packaged launcher differs from the source launcher')
        check_revision(source, args.revision)
        if digest(args.exe.read_bytes()) != executable_hash:
            raise ValueError('Executable changed during verification')
        if digest(args.launcher.read_bytes()) != launcher_hash:
            raise ValueError('Launcher changed during verification')
        if args.manifest:
            package = args.manifest.resolve().parent
            if (args.manifest.name != 'PLAYTEST.json' or
                    args.launcher.resolve() != package / 'Launch-Playtest.ps1' or
                    args.exe.resolve() != package / 'dist' / 'AlibAi-Playtest.exe'):
                raise ValueError('Manifest, launcher and executable must use the portable package layout')
            manifest = {
                'schema_version': 1, 'source_commit': args.revision,
                'exe_sha256': executable_hash, 'launcher_sha256': launcher_hash,
                'resource_sha256': resources,
                'checks': ['clean_source_revision', 'resource_byte_equality',
                           'required_modules_and_runtime', 'forbidden_archive_names'],
                'limits': [
                    'Consistency checks are not code signing or build attestation.',
                    'Python bytecode is not compared against the source revision.',
                    'No executable startup, live API, microphone, playback, visual or gameplay acceptance performed.',
                    'No independent code review or external developer approval asserted.',
                ],
            }
            args.manifest.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Playtest verification failed ({type(error).__name__}): {error}\n')
    print('Offline bundle consistency checks passed; live and startup acceptance remain separate.')


if __name__ == '__main__':
    main()
