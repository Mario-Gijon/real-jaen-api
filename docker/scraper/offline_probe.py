"""Offline packaging probe. Run only with Docker --network none, no ports or host data.

The hardened session factory is TEST ONLY: upstream source stays unchanged and is
not wired into the API/coordinator. It never falls back to an unsandboxed launch.
"""
import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path('/acquisition')
PROFILE = ROOT / 'profile'
REVISION = '611e436a1528b0f487aa56a045f023e8ae8ae098'
PACKAGES = ('SQLAlchemy', 'alembic', 'greenlet', 'typing_extensions', 'curl_cffi',
            'scrapling', 'patchright', 'playwright', 'fastapi', 'pydantic')


def children():
    found = {}
    for path in Path('/proc').glob('[0-9]*'):
        try:
            if int(path.name) == os.getpid():
                continue
            values = dict(line.split(':', 1) for line in (path / 'status').read_text().splitlines() if ':' in line)
            if int(values['Uid'].split()[0]) == os.getuid():
                found[int(path.name)] = dict(name=values['Name'].strip(), state=values['State'].strip(),
                    command=(path / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'))
        except (OSError, ValueError, KeyError):
            continue
    return found


def dependencies(expect_storage):
    assert sys.platform == 'linux' and os.geteuid() == 10001, 'Must run as the image non-root user'
    assert Path('/opt/scraper/SOURCE_REVISION').read_text().strip() == REVISION
    assert not Path('/opt/scraper/frontend/package-lock.json').exists()
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
    import sqlalchemy, alembic, curl_cffi, scrapling, patchright.async_api
    from sofascore_scraper.version import __version__
    from sofascore_scraper.store import open_store
    from sofascore_scraper.cli.commands.sync import data_dir_of_settings
    assert data_dir_of_settings() == '/acquisition/data'
    store = open_store(data_dir_of_settings(), create=True)
    store.close()
    assert __version__ == '3.1.0'
    subprocess.run([sys.executable, '-m', 'sofascore_scraper.cli.main', '--version'], check=True)
    subprocess.run(['sha256sum', '-c', '/opt/scraper/SOURCE_FILES.sha256'],
                   cwd='/opt/scraper', stdout=subprocess.DEVNULL, check=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    marker = PROFILE / '.offline-storage-marker'
    previous = marker.exists()
    if expect_storage:
        assert previous, 'Profile filesystem marker did not survive container recreation'
    if not previous:
        marker.write_text('local-fixture-only\n')
    # Exercise SQL and scraper dependencies in the same interpreter, without any real data.
    engine = sqlalchemy.create_engine('sqlite:///:memory:')
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql('select 1').scalar() == 1
    finally:
        engine.dispose()
    return dict(ok=True, uid=os.getuid(), platform=sys.platform, architecture=platform.machine(),
                python=platform.python_version(), sqlite=sqlite3.sqlite_version, source=REVISION,
                versions={name: importlib.metadata.version(name) for name in PACKAGES},
                profile_storage_previous=previous, profile_storage_writable=True)


class LocalSite(BaseHTTPRequestHandler):
    hits = []
    def log_message(self, *_):
        pass
    def do_GET(self):
        self.hits.append(self.path)
        if self.path.startswith('/api/'):
            body = json.dumps({'local': True, 'path': self.path}).encode()
            kind = 'application/json'
        else:
            body = b'<!doctype html><title>Local packaging fixture</title><p id="fixture">Offline fixture</p>'
            kind = 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Set-Cookie', 'sofa_captcha=local-fixture-token; Path=/; Max-Age=3600')
        self.end_headers()
        self.wfile.write(body)


async def browser(report, expect_cookie, hold):
    from scrapling.fetchers import AsyncStealthySession
    from sofascore_scraper.client import bridge
    baseline = set(children())
    denied = []
    server = ThreadingHTTPServer(('127.0.0.1', 0), LocalSite)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f'http://127.0.0.1:{server.server_address[1]}'
    # Do not run any original SofaScore URL, captcha or challenge.
    bridge.HOME_URL = origin + '/home'
    bridge.CAPTCHA_URL = origin + '/local-cookie'
    bridge._PROBE_URL = origin + '/api/probe'
    protected = {'chromium_sandbox': True, 'ignore_https_errors': False, 'permissions': [],
        'args': ['--disable-background-networking'],
        'ignore_default_args': ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
                               '--disable-ipc-flooding-protection', '--disable-client-side-phishing-detection']}

    class ProtectedSession(AsyncStealthySession):
        def __init__(self, **options):
            options['additional_args'] = protected
            super().__init__(**options)
        async def start(self):
            await super().start()
            async def local_only(route):
                if route.request.url.startswith(origin + '/'):
                    await route.continue_()
                else:
                    denied.append(route.request.url)
                    await route.abort()
            await self.context.route('**/*', local_only)

    class LocalBridge(bridge.BrowserBridge):
        async def _token_from_context(self):
            for cookie in await self.context.cookies(origin):
                if cookie['name'] == 'sofa_captcha':
                    return cookie['value']
            return None

    # Upstream bridge does not supply additional_args or turn on chromium_sandbox.
    report['upstream_requires_explicit_hardening'] = True
    report['probe_launch_policy'] = protected
    bridge._session_class = lambda: ProtectedSession
    instance = LocalBridge(profile_dir=str(PROFILE), home_url=origin + '/home', report_health=False)
    report['browser_ok'] = False
    try:
        await instance.ensure_ready()
        cookies = await instance.context.cookies(origin)
        persisted = any(c['name'] == 'offline_profile_check' and c['value'] == 'local-only' for c in cookies)
        report['browser_cookie_previous'] = persisted
        if expect_cookie:
            assert persisted, 'Browser profile cookie did not persist'
        await instance.context.add_cookies([dict(name='offline_profile_check', value='local-only',
                                                url=origin, expires=int(time.time()) + 86400)])
        assert await instance.page.locator('#fixture').inner_text() == 'Offline fixture'
        assert (await instance.fetch_json(origin + '/api/echo'))['local'] is True
        running = {pid: p for pid, p in children().items() if pid not in baseline}
        assert any('chrome' in p['command'].lower() for p in running.values())
        for process in running.values():
            flags = process['command'].split()
            assert '--no-sandbox' not in flags and '--disable-setuid-sandbox' not in flags, process
        report['child_processes_during_browser'] = running
        sandbox_page = await instance.context.new_page()
        await sandbox_page.goto('chrome://sandbox')
        report['chromium_sandbox_status'] = await sandbox_page.locator('body').inner_text()
        await sandbox_page.close()
        sandbox_status = report['chromium_sandbox_status']
        for expected in ('Layer 1 Sandbox\tNamespace', 'PID namespaces\tYes',
                         'Network namespaces\tYes', 'Seccomp-BPF sandbox\tYes'):
            assert expected in sandbox_status, sandbox_status
        proc_snapshot = {}
        for pid, process in running.items():
            try:
                proc = Path('/proc') / str(pid)
                values = dict(line.split(':', 1) for line in (proc / 'status').read_text().splitlines() if ':' in line)
                entry = {key: values[key].strip() for key in
                         ('Name', 'Pid', 'PPid', 'Uid', 'NSpid', 'CapEff', 'NoNewPrivs', 'Seccomp', 'Seccomp_filters')}
                entry['command'] = process['command']
                entry['namespaces'] = {}
                for namespace in ('user', 'pid', 'net'):
                    try:
                        entry['namespaces'][namespace] = os.readlink(proc / 'ns' / namespace)
                    except OSError as error:
                        entry['namespaces'][namespace] = {'unreadable': str(error)}
                proc_snapshot[str(pid)] = entry
            except FileNotFoundError:
                continue
        report['sandbox_proc'] = proc_snapshot
        browsers = [p for p in proc_snapshot.values() if '/chrome ' in p['command'] and '--type=' not in p['command']]
        renderers = [p for p in proc_snapshot.values() if '--type=renderer' in p['command']]
        assert len(browsers) == 1 and renderers, 'Missing browser/renderer process evidence'
        parent = browsers[0]
        assert parent['NoNewPrivs'] == '1' and parent['Seccomp'] == '2' and int(parent['CapEff'], 16) == 0
        for renderer in renderers:
            assert renderer['NoNewPrivs'] == '1' and renderer['Seccomp'] == '2' and int(renderer['CapEff'], 16) == 0
            assert len(renderer['NSpid'].split()) > len(parent['NSpid'].split()), 'Renderer not in a nested PID namespace'
            assert int(renderer['Seccomp_filters']) > int(parent['Seccomp_filters']), 'No additional renderer seccomp filter'
        report['sandbox_proc_verified'] = True
        report['browser_ok'] = True
        if hold:
            stopped = asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
            (ROOT / 'ready.json').write_text(json.dumps(report))
            print('OFFLINE_BROWSER_READY', flush=True)
            await stopped.wait()
    finally:
        await instance.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            remaining = {pid: p for pid, p in children().items() if pid not in baseline}
            if not remaining:
                break
            await asyncio.sleep(0.1)
        report['children_after_close'] = remaining
        report['children_closed'] = not remaining
        report['local_requests'] = LocalSite.hits
        report['page_requests_denied'] = denied
        report['ok'] = report['browser_ok'] and report['children_closed'] and not denied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('dependencies', 'browser'), default='dependencies')
    parser.add_argument('--expect-storage', action='store_true')
    parser.add_argument('--expect-cookie', action='store_true')
    parser.add_argument('--hold', action='store_true')
    parser.add_argument('--report', default='report.json')
    args = parser.parse_args()
    report = {}
    code = 0
    try:
        report.update(dependencies(args.expect_storage))
        if args.phase == 'browser':
            asyncio.run(browser(report, args.expect_cookie, args.hold))
        if not report['ok']:
            code = 1
    except Exception as error:
        report.update(ok=False, error=f'{type(error).__name__}: {error}')
        code = 1
    finally:
        target = ROOT / Path(args.report).name
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
    raise SystemExit(code)


if __name__ == '__main__':
    main()
