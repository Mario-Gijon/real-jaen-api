"""Export only committed, pinned scraper source. No network or working-tree copy."""
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile

REVISION = '611e436a1528b0f487aa56a045f023e8ae8ae098'
PATHS = ('pyproject.toml', 'requirements.txt', 'constraints.txt', 'LICENSE', 'locales', 'sofascore_scraper')


def export(repository, target):
    repository, target = Path(repository).resolve(), Path(target).resolve()
    if target.exists():
        raise FileExistsError(f'Never overwrite an export: {target}')
    actual = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', REVISION + '^{commit}'], text=True).strip()
    if actual != REVISION:
        raise ValueError('Unexpected revision')
    archive = subprocess.check_output(['git', '-C', str(repository), 'archive', '--format=tar', REVISION, *PATHS])
    # extractall(data) requires Python >= 3.12 and rejects unsafe paths and links.
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        target.mkdir(parents=True)
        tar.extractall(target, filter='data')
    (target / 'SOURCE_REVISION').write_text(REVISION + '\n', encoding='utf-8', newline='\n')
    (target / 'SOURCE_ARCHIVE_SHA256').write_text(hashlib.sha256(archive).hexdigest() + '\n', encoding='ascii', newline='\n')
    paths = sorted(p for p in target.rglob('*') if p.is_file())
    lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(target).as_posix()}\n' for p in paths]
    (target / 'SOURCE_FILES.sha256').write_text(''.join(lines), encoding='utf-8', newline='\n')
    assert not (target / 'frontend').exists()
    print(f'{REVISION}: {len(paths)} committed files exported to {target}')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: python export_source.py SCRAPER_REPOSITORY NEW_OUTPUT_DIRECTORY')
    export(*sys.argv[1:])
