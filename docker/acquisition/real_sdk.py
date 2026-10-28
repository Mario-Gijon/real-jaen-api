"""Etapa real única del adaptador en Docker: calendario 17073/97382 ronda 1 y partido 16599363 de SofaScore.

Se ejecuta dentro de la imagen de adquisición construida desde un commit (sin código montado). Solo monta el volumen
de intercambio (inventario extraído por el backend; nunca la base deportiva ni su journal) y el de perfiles (perfil
gestionado exclusivo, caché del SDK y presupuesto durable del trabajo).

Límites: las 5 rutas fijas del protocolo v2, un intento por ruta (cualquier fallo detiene el trabajo), 8 envíos
físicos (curl y fetch() del navegador, incluida la comprobación propia del bridge), 300 s de plazo del trabajo y un
vigilante que termina el proceso a los 420 s. Un único arranque del navegador, con sandbox acreditado, HTTPS
verificado y tráfico reducido. Sin RJ_REAL_CONFIRM exacto no se envía nada; el marcador del volumen de perfiles
impide una segunda ejecución con el mismo volumen.

Además del registro del adaptador (envíos y peticiones de las páginas), muestrea /proc/net de su espacio de red: todas
las conexiones TCP y UDP del contenedor, también las de fondo de Chromium que no pasan por las páginas.
"""
from contextlib import closing
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid

CONFIRMATION = 'sofascore-16599363-una-ejecucion'
SECONDS = 300
WATCHDOG = 420
MAX_SENDS = 8
TCP_STATES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV', '04': 'FIN_WAIT1', '05': 'FIN_WAIT2',
              '06': 'TIME_WAIT', '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK', '0A': 'LISTEN', '0B': 'CLOSING'}


def now():
    return datetime.now(timezone.utc).isoformat()


class StrictBreaker:
    """Un solo intento por ruta: cualquier resultado distinto de ok abre el interruptor y detiene todo el trabajo."""
    tripped = False

    def record(self, reason):
        if reason != 'ok':
            self.tripped = True


def address(text, family):
    host, port = text.split(':')
    raw = bytes.fromhex(host)
    # /proc/net guarda cada palabra de 32 bits en el orden del procesador (little-endian en amd64).
    raw = b''.join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    return socket.inet_ntop(family, raw), int(port, 16)


class Connections(threading.Thread):
    """Conexiones del espacio de red del contenedor, muestreadas cada 0,2 s. TCP queda en TIME_WAIT 60 s tras cerrarse,
    así que también aparecen las breves; un socket UDP solo muestra destino si está conectado (DNS, QUIC)."""
    FILES = (('tcp', socket.AF_INET), ('tcp6', socket.AF_INET6), ('udp', socket.AF_INET), ('udp6', socket.AF_INET6))

    def __init__(self, interval=.2):
        super().__init__(daemon=True)
        self.interval, self.halt, self.seen, self.samples = interval, threading.Event(), {}, 0

    def run(self):
        while not self.halt.is_set():
            self.sample()
            self.halt.wait(self.interval)

    def sample(self):
        at = now()
        for name, family in self.FILES:
            try:
                lines = Path('/proc/net', name).read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                ip, port = address(fields[2], family)
                if port == 0:
                    continue
                local = address(fields[1], family)[1]
                value = ipaddress.ip_address(ip)
                if getattr(value, 'ipv4_mapped', None):
                    ip = str(value.ipv4_mapped)
                entry = self.seen.setdefault((name.rstrip('6'), ip, port, local),
                                             dict(proto=name.rstrip('6'), remote=ip, port=port, local_port=local,
                                                  first_seen=at, states=[]))
                entry['last_seen'] = at
                state = TCP_STATES.get(fields[3], fields[3]) if name.startswith('tcp') else 'UDP'
                if state not in entry['states']:
                    entry['states'].append(state)
        self.samples += 1

    def stop(self):
        self.halt.set()
        self.join(5)
        self.sample()
        rows = sorted(self.seen.values(), key=lambda e: e['first_seen'])
        flows = {}
        for row in rows:
            key = f"{row['proto']} {row['remote']}:{row['port']}"
            flows[key] = flows.get(key, 0) + 1
        return dict(samples=self.samples, interval_s=self.interval, sockets=rows, by_destination=flows)


def watchdog(exchange):
    def fire():
        time.sleep(WATCHDOG)
        with (exchange / 'watchdog.json').open('w') as f:
            json.dump(dict(at=now(), limit_s=WATCHDOG), f)
        os._exit(3)
    threading.Thread(target=fire, daemon=True).start()


def main(exchange=Path('/exchange'), root=Path('/profiles')):
    if os.environ.get('RJ_REAL_CONFIRM') != CONFIRMATION:
        raise SystemExit('Sin la confirmación explícita de una ejecución no se contacta con SofaScore')
    with (root / 'real-run.started').open('x') as marker:  # una sola ejecución por volumen de perfiles
        marker.write(now() + '\n')
    watchdog(exchange)
    monitor = Connections()
    monitor.start()
    resolv = Path('/etc/resolv.conf').read_text(errors='replace') if Path('/etc/resolv.conf').exists() else None

    from acquisition.sdk_adapter import SdkAdapter, configure_environment
    configure_environment(root / 'sdk')  # antes de importar el SDK
    from acquisition.profile import Profile
    from acquisition.sdk_transport import SdkTransport
    from acquisition.pilot_bundle import seed_cache, collect_pilot, producer_rules
    from persistencia.bundle_v2 import PATHS, encode, parse
    from sofascore_scraper.store import Store

    baseline = parse((exchange / 'inventory.json').read_bytes())
    job, request = str(uuid.uuid4()), str(uuid.uuid4())
    managed = root / 'managed-profile'
    managed.mkdir(mode=0o700)
    descriptor, error, attempts, report = None, None, [], None
    started = now()
    with Profile(managed) as owner:
        owner.initialize(confirm_new=True)
        with closing(Store(root / 'cache')) as store:
            seed_cache(store, baseline)
            with owner.session() as profile:
                owner.allow_browser(profile)
                adapter = SdkAdapter(profile, PATHS, max_sends=MAX_SENDS, require_sandbox=True).install()
                try:
                    with SdkTransport(root / 'jobs', adapter=adapter, job_id=job, request_id=request,
                                      inventory_sha256=baseline['sha256'], enable_real=True, max_attempts=len(PATHS),
                                      seconds=SECONDS, breaker=StrictBreaker()) as transport:
                        try:
                            descriptor = collect_pilot(store, transport, baseline, exchange / 'bundle.json')
                            sends = len(adapter.sends)
                            assert collect_pilot(store, transport, baseline, exchange / 'bundle.json') == descriptor
                            assert len(adapter.sends) == sends  # el bundle sellado se verifica sin red
                        except BaseException as e:
                            error = f'{type(e).__name__}: {e}'
                        attempts = transport.state['attempts']
                finally:
                    adapter.close()
                    report = adapter.report()
        clean = owner.verify()
    connections = monitor.stop()
    summary = dict(stage='acquisition-sdk-real', started_at=started, finished_at=now(), job=[job, request],
                   error=error, stopped=report['stopped'], attempts=attempts,
                   limits=dict(paths=list(PATHS), attempts=len(PATHS), sends=MAX_SENDS, job_s=SECONDS, watchdog_s=WATCHDOG),
                   sends=report['sends'], requests=report['requests'], sandbox=report['sandbox'],
                   sessions=report['sessions'], launches=report['launches'], browser=report['browser'],
                   events=report['events'], managed_profile=clean, bytes=descriptor and descriptor['bytes'])
    (exchange / 'sdk-report.json').write_bytes(encode(summary))
    (exchange / 'connections.json').write_bytes(encode(dict(resolv_conf=resolv, **connections)))
    (exchange / 'browser-requests.json').write_bytes(encode(adapter.browser))
    if descriptor is not None and error is None:
        meta = dict(descriptor=descriptor, context=[job, request], rules_sha256=producer_rules(), attempts=len(attempts))
        (exchange / 'bundle-meta.json').write_bytes(encode(meta))
    print(encode(dict(error=error, stopped=report['stopped'], sends=len([s for s in report['sends'] if s['sent']]),
                      attempts=[(a['path'], a['status']) for a in attempts], sandbox=report['sandbox'],
                      browser_requests=report['browser']['total'], destinations=connections['by_destination'])).decode())
    if error is not None or report['stopped'] is not None:
        sys.exit(1)


if __name__ == '__main__':
    main()
