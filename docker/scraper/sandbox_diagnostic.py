"""Read-only sandbox diagnostics; each unshare probe runs in a disposable child."""
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as error:
        return f'unavailable: {error}'


def probe(flags):
    code = '''import json,os,sys
try:
    os.unshare(int(sys.argv[1])); print(json.dumps({'ok':True}))
except OSError as e:
    print(json.dumps({'ok':False,'errno':e.errno,'error':str(e)}))
'''
    result = subprocess.run([sys.executable, '-c', code, str(flags)], capture_output=True, text=True, timeout=10)
    return {'flags': hex(flags), 'result': result.stdout.strip(), 'stderr': result.stderr, 'returncode': result.returncode}


def main():
    keys = ('Name', 'Uid', 'Gid', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'NoNewPrivs', 'Seccomp', 'Seccomp_filters')
    lines = read('/proc/self/status').splitlines()
    helpers = []
    for path in Path('/opt/ms-playwright').rglob('*sandbox*'):
        if path.is_file():
            s = path.stat()
            helpers.append(dict(path=str(path), uid=s.st_uid, gid=s.st_gid, mode=oct(stat.S_IMODE(s.st_mode))))
    result = dict(uid=os.getuid(), gid=os.getgid(), platform=platform.platform(),
        status={line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in lines if line.split(':', 1)[0] in keys},
        lsm_profile=read('/proc/self/attr/current'), lsm_available=read('/sys/kernel/security/lsm'),
        userns_clone=read('/proc/sys/kernel/unprivileged_userns_clone'),
        apparmor_userns=read('/proc/sys/kernel/apparmor_restrict_unprivileged_userns'),
        userns_limit=read('/proc/sys/user/max_user_namespaces'),
        namespace_links={p.name: os.readlink(p) for p in Path('/proc/self/ns').iterdir()},
        uid_map=read('/proc/self/uid_map'), gid_map=read('/proc/self/gid_map'), sandbox_helpers=helpers,
        probes={name: probe(flags) for name, flags in [('no_flags', 0), ('fs_only', os.CLONE_FS), ('user', os.CLONE_NEWUSER)]})
    print(json.dumps(result, indent=2), flush=True)
    if '--hold' in sys.argv:
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        deadline = time.monotonic() + 300
        while not stopping and time.monotonic() < deadline:
            time.sleep(.1)


if __name__ == '__main__':
    main()
