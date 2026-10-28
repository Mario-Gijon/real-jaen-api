"""Diagnostic-only injection of a fixed traced executable; all sandbox options preserved."""
from pathlib import Path
import runpy
import sys

from scrapling import fetchers

original = fetchers.AsyncStealthySession
wrapper = Path('/tmp/chromium-syscall-trace')
wrapper.write_bytes(Path('/tmp/trace_chromium.py').read_bytes())
wrapper.chmod(0o700)


class TracedSession(original):
    def __init__(self, **kwargs):
        options = dict(kwargs['additional_args'])
        assert options['chromium_sandbox'] is True and options['ignore_https_errors'] is False
        options['executable_path'] = str(wrapper)
        kwargs['additional_args'] = options
        super().__init__(**kwargs)


fetchers.AsyncStealthySession = TracedSession
sys.argv = ['/opt/probe/offline_probe.py', '--phase', 'browser', '--report', 'traced-browser.json']
runpy.run_path('/opt/probe/offline_probe.py', run_name='__main__')
