"""Diagnostic-only, Linux profile owner. Never installed into the scraper/service.

A trusted host launcher must fence an unclean previous container before supplying
--fence. Metadata is not proof by itself; without that operator boundary refuse.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import uuid

ROOT = Path('/acquisition').resolve()
CONTROL = ROOT / 'profile-control'
SINGLETON = ('SingletonLock', 'SingletonSocket', 'SingletonCookie')

class RecoveryRequired(RuntimeError):
    pass

def durable(path, value):
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temp.open('x', encoding='utf-8') as out:
        json.dump(value, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def tree(path):
    found = {}
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            p = Path(directory) / name
            rel = str(p.relative_to(path))
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode):
                found[rel] = {'link': os.readlink(p)}
            elif stat.S_ISREG(mode):
                with p.open('rb') as content:
                    found[rel] = {'sha256': hashlib.file_digest(content, 'sha256').hexdigest(), 'size': p.stat().st_size}
            elif stat.S_ISDIR(mode):
                found[rel] = {'directory': True}
            else:
                raise RecoveryRequired('Unsupported special file in profile: ' + rel)
    return found

def sync_tree(path):
    for directory, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            p = Path(directory) / name
            if p.is_symlink():
                continue
            with p.open('rb') as src:
                os.fsync(src.fileno())
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

def contained(value):
    p = Path(value)
    if not p.is_absolute() or p.is_symlink() or not p.resolve().is_relative_to(ROOT):
        raise RecoveryRequired('Unsafe profile location')
    return p

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container-id', required=True)
    parser.add_argument('--fence', type=Path)
    parser.add_argument('--inject-failure', choices=['after-cleanup'])
    parser.add_argument('probe_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    assert sys.platform == 'linux' and os.getuid() == 10001 and os.uname().machine == 'x86_64'
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    if CONTROL.is_symlink():
        raise RecoveryRequired('Coordination directory must not be a symlink')
    fd = os.open(CONTROL / 'owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    assert stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'ok': False, 'status': 'profile_busy'}), flush=True)
            return 73
        state_path = CONTROL / 'session.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {'active': str(ROOT / 'profile'), 'dirty': None}
        prior = state.get('dirty')
        if prior:
            proof = json.loads(args.fence.read_text()) if args.fence else {}
            if proof.get('nonce') != prior['nonce'] or proof.get('container_id') != prior['container_id'] or proof.get('fenced') is not True:
                raise RecoveryRequired('Previous session incomplete: trusted launcher must fence its entire container; hostname/PID are insufficient')
        source = contained(prior['working'] if prior else state['active'])
        if not source.is_dir():
            raise RecoveryRequired('Missing source profile; automatic empty replacement prohibited')
        original = tree(source)
        if not state_path.exists() and original:
            raise RecoveryRequired('Unmanaged nonempty profile: explicit offline adoption required')
        if not prior and any(name in original for name in SINGLETON):
            raise RecoveryRequired('Unexpected singleton in a clean profile; explicit recovery required')
        required = 2 * sum(v.get('size', 0) for v in original.values()) + 32 * 1024 * 1024
        if shutil.disk_usage(ROOT).free < required:
            raise RecoveryRequired('Insufficient space for preserved backup and candidate')
        nonce = uuid.uuid4().hex
        backup = ROOT / 'profile-backups' / nonce
        candidate = ROOT / 'profile-generations' / nonce
        backup.parent.mkdir(mode=0o700, exist_ok=True)
        candidate.parent.mkdir(mode=0o700, exist_ok=True)
        shutil.copytree(source, backup, symlinks=True)
        if tree(backup) != original or tree(source) != original:
            raise RecoveryRequired('Backup verification failed; original untouched')
        sync_tree(backup)
        durable(backup.parent / (nonce + '.manifest.json'), {'source': str(source), 'tree': original})
        shutil.copytree(backup, candidate, symlinks=True)
        if tree(candidate) != original:
            raise RecoveryRequired('Candidate verification failed')
        state['dirty'] = {'nonce': nonce, 'container_id': args.container_id, 'working': str(candidate), 'backup': str(backup), 'source': str(source)}
        durable(state_path, state)
        for name in SINGLETON:
            p = candidate / name
            if p.is_symlink():
                p.unlink()  # Never dereference or delete the /tmp socket target.
            elif p.exists():
                raise RecoveryRequired('Unexpected Chromium singleton type: ' + name)
        if args.inject_failure:
            raise RecoveryRequired('Injected failure after candidate cleanup; backup and source retained')
        sync_tree(candidate)
        durable(CONTROL / 'prelaunch.json', {'backup': str(backup), 'source': str(source), 'candidate': str(candidate), 'nonce': nonce})
        from sofascore_scraper.client import bridge
        def reject_secondary(self, owner):
            raise RecoveryRequired('Profile cannot be opened exclusively; temporary empty profile prohibited')
        bridge.BrowserBridge._use_secondary_profile = reject_secondary
        spec = importlib.util.spec_from_file_location('offline_probe', '/opt/probe/offline_probe.py')
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        probe.PROFILE = candidate
        original_dependencies = probe.dependencies
        def session_dependencies(expect_storage):
            report = original_dependencies(expect_storage)
            report['profile_session_nonce'] = nonce
            report['profile_working_path'] = str(candidate)
            return report
        probe.dependencies = session_dependencies
        probe_args = args.probe_args
        if probe_args and probe_args[0] == '--':
            probe_args = probe_args[1:]
        sys.argv = ['/opt/probe/offline_probe.py'] + probe_args
        code = 1
        try:
            probe.main()
        except SystemExit as error:
            code = error.code
        if code != 0:
            raise RecoveryRequired('Probe/session failed; original and backup retained; recovery required')
        # Probe asserts child cleanup; retain the lock through fsync and durable commit.
        sync_tree(candidate)
        durable(state_path, {'active': str(candidate), 'dirty': None})
        durable(CONTROL / 'last-success.json', {'candidate': str(candidate), 'backup': str(backup), 'source_unchanged': tree(source) == original, 'backup_unchanged': tree(backup) == original})
        return 0
    except Exception as error:
        failure = {'ok': False, 'status': 'recovery_required', 'error': str(error)}
        if 'backup' in locals() and backup.is_dir() and 'original' in locals():
            failure.update(backup=str(backup), source=str(source), candidate=str(candidate),
                           backup_unchanged=tree(backup) == original, source_unchanged=tree(source) == original)
        durable(CONTROL / 'last-failure.json', failure)
        print(json.dumps(failure), flush=True)
        return 74
    finally:
        os.close(fd)  # Never unlink the lock inode.

if __name__ == '__main__':
    raise SystemExit(main())
