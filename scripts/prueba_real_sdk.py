"""Prueba real única del adaptador del SDK en Docker (calendario 17073/97382 ronda 1 y partido 16599363).

  construir  imagen de adquisición desde `git archive` de un commit (sin red, sin código montado) y registro de su ID
             y del contenido de /opt/app comparado con el commit.
  ejecutar   prepara el inventario en el backend con una copia inactiva (red none), lanza la etapa de adquisición y
             verifica el bundle en el backend (red none), sin candidata ni publicación.
             --modo ensayo: la adquisición con red none (no sale nada; comprueba la cadena de paradas).
             --modo real: red bridge propia de esta ejecución; exige --confirmar y un registro local de una sola vez.

El contenedor de adquisición solo monta los volúmenes de intercambio y de perfiles de esta ejecución; el backend, el
código del mismo commit (solo lectura) y la copia inactiva. Los volúmenes de una ejecución real se conservan.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = 'sofascore-16599363-una-ejecucion'
RECIPE = 'sofascore-16599363-sdk-real'
REAL_FILES = ('acquisition/pilot_bundle.py', 'acquisition/pilot_transport.py', 'acquisition/sdk_transport.py',
              'acquisition/sofascore_offline.py', 'api.py', 'importar.py', 'persistencia/bundle_v2.py',
              'docker/acquisition/real_sdk.py')
IMAGE_PATHS = {'acquisition/' + n: '/opt/app/acquisition/' + n for n in
               ('__init__.py', 'admin.py', 'browser.py', 'profile.py', 'service.py', 'sdk_adapter.py',
                'pilot_bundle.py', 'pilot_transport.py', 'sdk_transport.py', 'sofascore_offline.py')}
IMAGE_PATHS.update({'api.py': '/opt/app/api.py', 'importar.py': '/opt/app/importar.py',
                    'persistencia/bundle_v2.py': '/opt/app/persistencia/bundle_v2.py',
                    'docker/acquisition/real_sdk.py': '/opt/app/real_sdk.py'})
SDK_FILES = ('services/pipeline.py', 'services/planning.py', 'services/listing.py', 'services/export.py',
             'store/export.py', 'store/backup.py', 'slices.py', 'sports.py', 'status.py', 'client/bridge.py',
             'client/transport.py', 'client/profile_lock.py')
SCRAPER_REVISION = '611e436a1528b0f487aa56a045f023e8ae8ae098'
LEDGER = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'rj-acquisition' / 'real-runs' / (RECIPE + '.started')
REAL_TIMEOUT = 480  # el trabajo tiene 300 s y el vigilante del contenedor 420 s


def docker(*args, check=True, timeout=600):
    p = subprocess.run(['docker', *map(str, args)], capture_output=True, text=True, encoding='utf8', errors='replace',
                       timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(p.stdout + '\n' + p.stderr)
    return p


def archive(commit, target):
    """Árbol del commit, no del directorio de trabajo."""
    raw = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', commit], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        tar.extractall(target, filter='data')
    return {m: hashlib.sha256((Path(target) / m).read_bytes()).hexdigest() for m in IMAGE_PATHS}


def resolve(rev):
    return subprocess.run(['git', '-C', str(ROOT), 'rev-parse', rev + '^{commit}'], check=True, capture_output=True,
                          text=True).stdout.strip()


def image_hashes(image, paths):
    probe = ('import hashlib,json,pathlib,sys;print(json.dumps({p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() '
             'for p in json.loads(sys.argv[1])}))')
    return json.loads(docker('run', '--rm', '--pull', 'never', '--network', 'none', '--cap-drop', 'ALL', '--security-opt',
                             'no-new-privileges:true', '--entrypoint', 'python', image, '-B', '-c', probe,
                             json.dumps(list(paths))).stdout)


def build(a):
    commit = resolve(a.commit)
    if a.output.exists():
        raise FileExistsError('El directorio del registro debe ser nuevo')
    base = json.loads(docker('image', 'inspect', a.base_runtime).stdout)[0]
    with tempfile.TemporaryDirectory(prefix='rj-real-build-') as tmp:
        src, subset = Path(tmp) / 'src', Path(tmp) / 'source'
        expected = archive(commit, src)
        for name in REAL_FILES:
            (subset / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / name, subset / name)
        short = commit[:12]
        runtime, final = f'rj-acq-real-runtime:{short}', f'rj-acq-real:{short}'
        labels = ['--label', 'rj.source.commit=' + commit, '--label', 'rj.recipe=' + RECIPE]
        docker('build', '--network', 'none', '--pull=false', '--target', 'runtime', '--build-arg',
               'PROFILE_RUNTIME=' + a.base_runtime, '--build-context', f'implementation={src / "acquisition"}', *labels,
               '-f', src / 'docker/acquisition/Dockerfile', '-t', runtime, src / 'docker/acquisition')
        docker('build', '--network', 'none', '--pull=false', '--build-arg', 'RUNTIME=' + runtime, '--build-context',
               f'source={subset}', *labels, '-f', src / 'docker/acquisition/Dockerfile.real', '-t', final,
               src / 'docker/acquisition')
    image = json.loads(docker('image', 'inspect', final).stdout)[0]
    assert image['Config']['Labels'].get('rj.source.commit') == commit and image['Config']['User'] == '10001:10001'
    found = image_hashes(image['Id'], IMAGE_PATHS.values())
    mismatch = [m for m, p in IMAGE_PATHS.items() if found[p] != expected[m]]
    assert not mismatch, mismatch
    listing = docker('run', '--rm', '--pull', 'never', '--network', 'none', '--entrypoint', 'sh', image['Id'], '-c',
                     'find /opt/app -type f | sort; ls -d /opt/checks /opt/probe 2>/dev/null; true').stdout.split()
    # El destino runtime copia también el .dockerignore de su contexto (texto, no código).
    foreign = [f for f in listing if f not in IMAGE_PATHS.values() and f != '/opt/app/acquisition/.dockerignore']
    assert not foreign, foreign  # nada de pilotos de prueba, ensayos, sitios simulados ni instrumentos
    rev = subprocess.run(['git', '-C', str(a.scraper_source), 'rev-parse', 'HEAD'], check=True, capture_output=True,
                         text=True).stdout.strip()
    assert rev == SCRAPER_REVISION
    sdk = {n: hashlib.sha256((a.scraper_source / 'sofascore_scraper' / n).read_bytes()).hexdigest() for n in SDK_FILES}
    bundled = image_hashes(image['Id'], ['/opt/scraper/sofascore_scraper/' + n for n in SDK_FILES])
    assert {n: bundled['/opt/scraper/sofascore_scraper/' + n] for n in SDK_FILES} == sdk, 'SDK distinto de la revisión fijada'
    record = dict(commit=commit, image=final, image_id=image['Id'], runtime=runtime,
                  runtime_id=json.loads(docker('image', 'inspect', runtime).stdout)[0]['Id'],
                  base_runtime=a.base_runtime, base_runtime_id=base['Id'], files=found, opt_app=listing,
                  scraper_revision=rev, sdk_hashes=sdk, network_during_build='none')
    a.output.mkdir(parents=True)
    (a.output / 'build-record.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(image=final, image_id=image['Id'], commit=commit), indent=2))


def execute(a):
    real = a.modo == 'real'
    if real and a.confirmar != CONFIRMATION:
        raise SystemExit('La ejecución real exige --confirmar ' + CONFIRMATION)
    if real and LEDGER.exists():
        raise SystemExit(f'Ya hubo una ejecución real ({LEDGER}); no se repite')
    if a.snapshot.name == 'real-jaen.sqlite' or a.snapshot.parent.name != 'copias':
        raise ValueError('Solo una copia inactiva identificada de copias/, nunca la base activa')
    if a.output.exists():
        raise FileExistsError('El directorio del informe debe ser nuevo')
    image = json.loads(docker('image', 'inspect', a.imagen).stdout)[0]
    backend = json.loads(docker('image', 'inspect', a.backend_image).stdout)[0]
    commit = image['Config']['Labels'].get('rj.source.commit')
    assert image['Config']['Labels'].get('rj.recipe') == RECIPE and commit, 'Imagen no construida por este ejecutor'
    for value in (image, backend):
        assert value['Os'] == 'linux' and value['Config']['User'] == '10001:10001'
    snapshot_sha = hashlib.sha256(a.snapshot.read_bytes()).hexdigest()
    a.output.mkdir(parents=True)
    sys.path.insert(0, str(ROOT))
    from persistencia.sqlite import copia_consistente, comprobar_copia
    prefix = 'rj-real-sdk-' + uuid.uuid4().hex[:12]
    label = 'rj.real.sdk=' + prefix
    volumes = {k: prefix + '-' + k for k in ('state', 'exchange', 'profiles')}
    created, containers, network = [], [], 'none'
    common = ['--init', '--pull', 'never', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true']
    stages = {}

    def run(image_id, mounts, command, name, *, net='none', extra=(), env=(), entrypoint=('--entrypoint', 'python'),
            timeout=600, allowed=(0,)):
        cname = prefix + '-' + name
        containers.append(cname)
        cmd = ['run', '--rm', *common, '--network', net, *extra, '--name', cname, '--label', label]
        for source, target, kind, readonly in mounts:
            cmd += ['--mount', f'type={kind},source={source},target={target}' + (',readonly' if readonly else '')]
        for variable in env:
            cmd += ['--env', variable]
        cmd += [*entrypoint, image_id, *command]
        try:
            p = docker(*cmd, check=False, timeout=timeout)
            code, out = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            docker('kill', cname, check=False)
            code, out = 'timeout', f'Detenido por el ejecutor tras {timeout} s'
        (a.output / (name + '.log')).write_text(out, encoding='utf8')
        stages[name] = dict(exit=code, network=net)
        if code not in allowed:
            raise RuntimeError(f'Detenido en {name} ({code})\n{out[-3000:]}')
        print(f'[{name}] salida {code}\n{out[-1500:]}', flush=True)
        return code

    try:
        for volume in volumes.values():
            assert docker('volume', 'inspect', volume, check=False).returncode != 0
            docker('volume', 'create', '--label', label, volume)
            created.append(volume)
        with tempfile.TemporaryDirectory(prefix=prefix + '-') as tmp:
            src = Path(tmp) / 'src'
            archive(commit, src)
            copied = Path(tmp) / 'inactive-snapshot.sqlite'
            copia_consistente(a.snapshot, copied)
            description = comprobar_copia(copied)
            app = (src, '/app', 'bind', True)
            backend_mounts = [app, (volumes['state'], '/state', 'volume', False), (volumes['exchange'], '/imports', 'volume', False)]
            run(backend['Id'], backend_mounts + [(copied, '/state/input.sqlite', 'bind', True)],
                ['-B', '/app/docker/acquisition/pilot_backend_check.py', 'prepare'], 'prepare', env=('PYTHONPATH=/app',))
            # Adquisición: solo intercambio y perfiles; ni base deportiva, ni journal, ni código montado.
            acq_mounts = [(volumes['exchange'], '/exchange', 'volume', False), (volumes['profiles'], '/profiles', 'volume', False)]
            browser = ['--security-opt', 'seccomp=' + str(src / 'docker/acquisition/seccomp-linux-amd64.json'),
                       '--shm-size', '512m', '--memory', '2g', '--cpus', '2', '--pids-limit', '512']
            # Sin confirmación no envía nada, ni siquiera en el ensayo.
            run(image['Id'], acq_mounts, [], 'sin-confirmacion', extra=browser, entrypoint=(), allowed=(1,))
            if real:
                network = prefix + '-net'
                docker('network', 'create', '--driver', 'bridge', '--label', label, network)
                LEDGER.parent.mkdir(parents=True, exist_ok=True)
                with LEDGER.open('x') as f:  # una sola ejecución real por equipo
                    json.dump(dict(prefix=prefix, image=image['Id'], commit=commit), f)
            code = run(image['Id'], acq_mounts, [], 'acquisition', net=network, extra=browser,
                       env=('RJ_REAL_CONFIRM=' + CONFIRMATION,), entrypoint=(), timeout=REAL_TIMEOUT,
                       allowed=(0, 1, 3, 'timeout'))
            sys.path.insert(0, str(src))
            from acquisition.pilot_bundle import producer_rules
            rules = producer_rules()
            if code == 0:
                run(backend['Id'], backend_mounts, ['-B', '/app/docker/acquisition/pilot_backend_check.py', 'verify-real', rules],
                    'verify', env=('PYTHONPATH=/app',))
        # Solo informes; el bundle, el inventario, el perfil y las respuestas quedan en los volúmenes.
        names = ('prepare-report.json', 'sdk-report.json', 'connections.json', 'browser-requests.json', 'bundle-meta.json',
                 'verify-report.json', 'watchdog.json')
        p = docker('run', '--rm', *common, '--network', 'none', '--mount',
                   f'type=volume,source={volumes["exchange"]},target=/imports,readonly', '--entrypoint', 'python',
                   backend['Id'], '-c', 'import pathlib,json,sys;print(json.dumps({n:json.loads((pathlib.Path("/imports")/n).read_bytes()) '
                   'for n in json.loads(sys.argv[1]) if (pathlib.Path("/imports")/n).exists()}))', json.dumps(names))
        results = json.loads(p.stdout)
        for name, value in results.items():
            (a.output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf8')
        report = dict(mode=a.modo, project=prefix, commit=commit, acquisition_image=image['Id'], backend_image=backend['Id'],
                      snapshot=str(a.snapshot), snapshot_sha256=snapshot_sha, source_revision=description['revision'],
                      network=network, stages=stages, volumes=volumes, volumes_kept=real, ledger=str(LEDGER) if real else None,
                      bundle=code == 0, publication=None)
        (a.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
        print(json.dumps(report, indent=2))
    finally:
        for name in containers:
            p = docker('inspect', name, check=False)
            if p.returncode == 0 and json.loads(p.stdout)[0]['Config']['Labels'].get('rj.real.sdk') == prefix:
                docker('rm', '-f', name)
        if network != 'none':
            docker('network', 'rm', network, check=False)
        if not real:  # el ensayo no deja nada; los volúmenes de una ejecución real son evidencia
            for volume in created:
                value = json.loads(docker('volume', 'inspect', volume).stdout)[0]
                if value['Labels'].get('rj.real.sdk') != prefix:
                    raise RuntimeError('Volume ownership changed')
                docker('volume', 'rm', volume)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    b = sub.add_parser('construir')
    b.add_argument('--commit', default='HEAD')
    b.add_argument('--base-runtime', required=True)
    b.add_argument('--scraper-source', type=Path, required=True)
    b.add_argument('--output', type=Path, required=True)
    e = sub.add_parser('ejecutar')
    e.add_argument('--imagen', required=True)
    e.add_argument('--backend-image', required=True)
    e.add_argument('--snapshot', type=Path, required=True)
    e.add_argument('--output', type=Path, required=True)
    e.add_argument('--modo', choices=('ensayo', 'real'), default='ensayo')
    e.add_argument('--confirmar')
    a = ap.parse_args()
    build(a) if a.command == 'construir' else execute(a)


if __name__ == '__main__':
    main()
