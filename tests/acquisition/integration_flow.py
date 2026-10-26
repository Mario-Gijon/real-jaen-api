"""Docker-only backend fixture driver. Never an API endpoint or habitual startup."""
from contextlib import closing
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
import uuid
from unittest import mock
from persistencia.actualizacion import Coordinador
from persistencia.adquisicion import Cliente,AdquisicionRechazada,validar_bundle
from persistencia import carga,sqlite
from persistencia.decisiones import cargar_sustituciones
from test_carga import Base,AMBITO,respuestas

ROOT=Path('/state');BASE=ROOT/'fixture.sqlite';SECRET='/run/secrets/acquisition-token'

def client(**kw):return Cliente('http://acquisition:8002',SECRET,allow_simulated=True,**kw)
def assert_rejected(call):
    try:call()
    except AdquisicionRechazada:return
    raise AssertionError('Expected rejection')

def main():
    mode=sys.argv[1]
    if mode=='init':
        assert not BASE.exists()
        fixture=Base();fixture.setUp()
        try:
            carga.cargar(fixture.importacion(),BASE)
            cargar_sustituciones(BASE,fixture.sustituciones((300,301)),AMBITO)
            BASE.with_name(BASE.name+'.simulated-installation').write_text('isolated-fixtures-v1')
        finally:fixture.doCleanups()
        print('SYNTHETIC_DATABASE_INITIALIZED',flush=True);return
    if mode=='units':
        import unittest
        suite=unittest.defaultTestLoader.discover('/opt/checks',pattern='test_protocol.py')
        raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
    c=client()
    if mode=='auth-probe':
        import urllib.error
        for header in (None,'Bearer deliberately-wrong'):
            request=urllib.request.Request('http://acquisition:8002/internal/v1/acquisitions',data=b'{}')
            if header:request.add_header('Authorization',header)
            try:urllib.request.urlopen(request,timeout=3)
            except urllib.error.HTTPError as error:
                assert error.code==401 and Path(SECRET).read_bytes().strip() not in error.read()
            else:raise AssertionError('Unauthorized request accepted')
        print('MISSING_AND_WRONG_AUTH_REJECTED',flush=True);return
    if mode=='restart-start':
        req=str(uuid.uuid4());job=c.solicitar(req,'fixture-slow-v1')
        (ROOT/'restart.json').write_text(json.dumps({'request':req,'job':job['id']}))
        while c.estado(job['id'])['state']=='queued':time.sleep(.02)
        print('RESTART_JOB_RUNNING',flush=True);return
    if mode=='restart-check':
        old=json.loads((ROOT/'restart.json').read_text())
        assert c.estado(old['job'])['state']=='interrupted'
        assert c.solicitar(old['request'],'fixture-slow-v1')['id']==old['job']
        assert c.estado(old['job'])['state']=='interrupted'
        print('RESTART_INTERRUPTED_NO_AUTO_RESUME',flush=True);return
    if mode=='tamper-start':
        job=c.solicitar(str(uuid.uuid4()),'fixture-complete-v1');c.esperar(job['id'])
        (ROOT/'tamper.json').write_text(json.dumps(job));print(job['id'],flush=True);return
    if mode=='tamper-check':
        job=json.loads((ROOT/'tamper.json').read_text())
        with Coordinador(BASE,AMBITO,reserva=0) as coord:
            generation=coord.capturar().id
            assert_rejected(lambda:c.publicar(coord,job['request_id'],job['recipe_id']))
            assert coord.capturar().id==generation
        print('TAMPER_REJECTED_NO_PUBLICATION',flush=True);return
    assert mode=='flow'
    assert_rejected(lambda:Cliente('http://acquisition:8002',SECRET).solicitar(str(uuid.uuid4()),'arbitrary'))
    with Coordinador(BASE,AMBITO,reserva=0) as coord:
        old=coord.capturar();before=respuestas(old._datos)
        assert old.consultar('/api/v1/matches/777001')[0]==404
        engine=sqlite.crear_motor(BASE,solo_lectura=True)
        try:
            with engine.connect() as db:
                kept={t:db.exec_driver_sql('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ['team','player','match','observation','content','local_replacement']}
        finally:engine.dispose()
        # Duplicate acquisition and active-job conflict; cancellation is idempotent.
        req=str(uuid.uuid4());job=c.solicitar(req,'fixture-slow-v1')
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            duplicate=list(pool.map(lambda _:c.solicitar(req,'fixture-slow-v1')['id'],range(4)))
        assert set(duplicate)=={job['id']}
        assert_rejected(lambda:c.solicitar(req,'fixture-complete-v1'))
        assert_rejected(lambda:c.solicitar(str(uuid.uuid4()),'fixture-complete-v1'))
        c.cancelar(job['id']);assert c.esperar(job['id'])['state']=='cancelled'
        assert c.cancelar(job['id'])['state']=='cancelled'
        assert coord.capturar().id==old.id
        for recipe in ('fixture-fail-v1','fixture-partial-v1'):
            assert_rejected(lambda:c.publicar(coord,str(uuid.uuid4()),recipe))
            assert coord.capturar().id==old.id
        assert_rejected(lambda:Cliente('http://acquisition:8002',SECRET).publicar(coord,str(uuid.uuid4()),'fixture-complete-v1'))
        marker=BASE.with_name(BASE.name+'.simulated-installation')
        disabled=marker.with_suffix(marker.suffix+'.disabled');marker.rename(disabled)
        try:assert_rejected(lambda:c.publicar(coord,str(uuid.uuid4()),'fixture-complete-v1'))
        finally:disabled.rename(marker)
        req=str(uuid.uuid4());result=c.publicar(coord,req,'fixture-complete-v1')
        assert result['state']=='published' and result['generation_id']==coord.capturar().id
        assert coord.capturar().consultar('/api/v1/matches/777001')[0]==200
        assert old.consultar('/api/v1/matches/777001')[0]==404 and respuestas(old._datos)==before
        retry=c.publicar(coord,req,'fixture-complete-v1');assert retry==result
        # Backend restriction: CLI maintenance remains rejected for the whole owner lifetime.
        import subprocess
        maintained=subprocess.run([sys.executable,'-B','-m','persistencia','migrar','--base',str(BASE)],capture_output=True,text=True)
        assert maintained.returncode!=0 and 'Base en uso' in maintained.stderr
        engine=sqlite.crear_motor(BASE,solo_lectura=True)
        try:
            with engine.connect() as db:
                for table,rows in kept.items():assert set(rows)<=set(db.exec_driver_sql('SELECT * FROM '+table+' ORDER BY 1').fetchall()),table
        finally:engine.dispose()
        assert sqlite.comprobar_copia(BASE)
        expected_responses=respuestas(coord.capturar()._datos)
        (ROOT/'published.json').write_text(json.dumps({'request':req,'result':result}))
    assert expected_responses==respuestas(__import__('persistencia.lectura',fromlist=['cargar_desde_base']).cargar_desde_base(BASE,AMBITO))
    # Restart of backend coordinator: old request must not re-import or re-publish.
    with Coordinador(BASE,AMBITO,reserva=0) as coord:
        stable=coord.capturar().id
        assert c.publicar(coord,req,'fixture-complete-v1')==result
        assert coord.capturar().id==stable
    print('FULL_FLOW_PUBLISHED_DUPLICATES_CANCEL_FAILURE_PARTIAL_IDS_DECISIONS_EQUIVALENCE',flush=True)

if __name__=='__main__':main()
