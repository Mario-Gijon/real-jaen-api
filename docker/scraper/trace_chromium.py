#!/usr/local/bin/python
"""Trace only a fixed Chromium child using permissions already present in the probe.

No attach to arbitrary processes, no policy changes, no external requests. Linux x86_64
instrumentation only; not a browser launcher for production. PTRACE can affect sandbox
behaviour, so correlate denials with the untraced control and effective seccomp rules.
"""
import ctypes
import errno
import json
import os
from pathlib import Path
import signal
import struct
import sys
import time

CHROME = '/opt/ms-playwright/chromium-1243/chrome-linux64/chrome'
LOG = Path('/acquisition/chromium-syscalls.jsonl')
PTRACE_TRACEME, PTRACE_SYSCALL, PTRACE_SETOPTIONS = 0, 24, 0x4200
PTRACE_GET_SYSCALL_INFO = 0x420e
OPTIONS = 1 | 2 | 4 | 8 | 16 | 0x100000  # syscall stops, forks/clones/exec, EXITKILL
WALL = 0x40000000
NAMES = {56: 'clone', 57: 'fork', 58: 'vfork', 59: 'execve', 101: 'ptrace',
         105: 'setuid', 106: 'setgid', 117: 'setresuid', 119: 'setresgid',
         125: 'capget', 126: 'capset', 157: 'prctl', 161: 'chroot', 272: 'unshare',
         308: 'setns', 317: 'seccomp', 435: 'clone3'}
libc = ctypes.CDLL(None, use_errno=True)
libc.ptrace.restype = ctypes.c_long
libc.ptrace.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]


def request(operation, pid, address=0, data=0):
    ctypes.set_errno(0)
    address = ctypes.c_void_p(address) if isinstance(address, int) else address
    data = ctypes.c_void_p(data) if isinstance(data, int) else data
    value = libc.ptrace(operation, pid, address, data)
    if value == -1 and ctypes.get_errno():
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return value


def main():
    assert os.uname().machine == 'x86_64', 'Register layout must be reviewed for another architecture'
    assert os.getuid() == 10001
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open('w', encoding='utf-8') as log:
        def record(**fields):
            log.write(json.dumps(fields) + '\n')
            log.flush()
        record(event='tracer', uid=os.getuid(), target=CHROME, argv=sys.argv[1:])
        child = os.fork()
        if child == 0:
            try:
                request(PTRACE_TRACEME, 0)
                os.kill(os.getpid(), signal.SIGSTOP)
                os.execv(CHROME, [CHROME, *sys.argv[1:]])
            except BaseException as error:
                os.write(2, f'Trace child error: {error}\n'.encode())
                os._exit(125)
        _, stopped = os.waitpid(child, 0)
        if not os.WIFSTOPPED(stopped):
            raise RuntimeError('Child did not enter tracing stop')
        request(PTRACE_SETOPTIONS, child, data=OPTIONS)
        pending, owned = {}, {child}
        request(PTRACE_SYSCALL, child)
        main_code = None
        started = time.monotonic()
        def timeout(*_):
            raise TimeoutError('Chromium diagnostic exceeded 45 seconds')
        signal.signal(signal.SIGALRM, timeout)
        signal.alarm(45)
        try:
            while True:
                try:
                    pid, status = os.waitpid(-1, WALL)
                except ChildProcessError:
                    break
                owned.add(pid)
                if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                    code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
                    record(event='exit', pid=pid, code=code)
                    owned.discard(pid)
                    pending.pop(pid, None)
                    if pid == child:
                        main_code = code
                    continue
                if not os.WIFSTOPPED(status):
                    continue
                sig, event = os.WSTOPSIG(status), status >> 16
                deliver = 0
                if sig == signal.SIGTRAP | 0x80:
                    info = ctypes.create_string_buffer(128)
                    request(PTRACE_GET_SYSCALL_INFO, pid, 128, ctypes.byref(info))
                    op, arch = info.raw[0], struct.unpack_from('<I', info.raw, 4)[0]
                    if arch != 0xc000003e:
                        raise RuntimeError(f'Unexpected audit architecture: {arch:x}')
                    if op == 1:
                        nr, *args = struct.unpack_from('<7Q', info.raw, 24)
                        pending[pid] = (nr, args)
                    elif op == 2:
                        result = struct.unpack_from('<q', info.raw, 24)[0]
                        nr, args = pending.pop(pid, (-1, []))
                        if nr in NAMES or result in (-errno.EPERM, -errno.EACCES):
                            record(event='syscall', pid=pid, syscall=NAMES.get(nr, f'nr_{nr}'), nr=nr,
                                   args=args, args_hex=[hex(a) for a in args], result=result,
                                   errno=-result if -4095 <= result < 0 else None)
                elif event:
                    record(event='ptrace_event', pid=pid, kind=event)
                elif sig == signal.SIGSTOP:
                    pass  # newly traced child; options inherited automatically
                else:
                    deliver = sig  # preserve actual browser signals, including SIGABRT
                try:
                    request(PTRACE_SYSCALL, pid, data=deliver)
                except OSError as error:
                    if error.errno != errno.ESRCH:
                        raise
            record(event='complete', main_code=main_code, elapsed=time.monotonic() - started)
        finally:
            signal.alarm(0)
            for pid in owned:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            # Reap only our traced descendants; no unrelated PID or attach operation.
            while True:
                try:
                    os.waitpid(-1, WALL)
                except ChildProcessError:
                    break
        raise SystemExit(main_code if main_code is not None else 125)


if __name__ == '__main__':
    main()
