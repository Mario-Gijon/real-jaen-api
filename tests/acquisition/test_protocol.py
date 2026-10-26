"""Contract tests with only local HTTP and local fixtures; no sports database."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from acquisition.service import Jobs,Server,PREFIX,encoded
from acquisition_simulated import Provider
from persistencia.adquisicion import Cliente,AdquisicionRechazada,validar_bundle

class Protocol(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.token=b'a'*64
        self.file=root/'secret';self.file.write_bytes(self.token)
        self.jobs=Jobs(root/'jobs',Provider());self.addCleanup(self.jobs.close)
        self.server=Server(('127.0.0.1',0),self.jobs,self.token)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.client=Cliente(self.url,self.file,allow_simulated=True)
    def request(self,path,body,auth=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode(),headers={'Authorization':auth or 'Bearer '+self.token.decode()})
        return urllib.request.urlopen(req,timeout=5)
    def completed(self,recipe='fixture-complete-v1'):
        request=str(uuid.uuid4());job=self.client.solicitar(request,recipe)
        state=self.client.esperar(job['id']);return request,state
    def bundle(self):
        request,state=self.completed();raw,etag=self.client._http('GET','/'+state['id']+'/bundle',bundle=True)
        self.assertEqual(etag,state['bundle']['sha256'])
        request_obj=urllib.request.Request(self.url+PREFIX+'/'+state['id']+'/bundle',headers={'Authorization':'Bearer '+self.token.decode()})
        with urllib.request.urlopen(request_obj,timeout=5) as response:self.assertEqual(response.headers['ETag'],chr(34)+etag+chr(34))
        return raw,state,dict(job_id=state['id'],request_id=request,recipe_id=state['recipe_id'])
    def test_authorization_wrong_secret_and_no_echo(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.request(PREFIX,{},'Bearer wrong-secret')
        self.assertEqual(error.exception.code,401)
        self.assertNotIn(self.token,error.exception.read())
    def test_arbitrary_parameters_rejected(self):
        for field in ('command','url','path'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(PREFIX,{'request_id':str(uuid.uuid4()),'recipe_id':'fixture-complete-v1',field:'ignored'})
            self.assertEqual(error.exception.code,400)
    def test_valid_bundle_and_partial_rejected(self):
        raw,state,ids=self.bundle();data,m=validar_bundle(raw,state['bundle'],**ids)
        self.assertTrue(data and m['is_simulated'])
        request,state=self.completed('fixture-partial-v1');body,_=self.client._http('GET','/'+state['id']+'/bundle',bundle=True)
        with self.assertRaises(AdquisicionRechazada):validar_bundle(body,state['bundle'],job_id=state['id'],request_id=request,recipe_id=state['recipe_id'])
    def test_mutation_digest_rejected(self):
        raw,state,ids=self.bundle()
        with self.assertRaises(AdquisicionRechazada):validar_bundle(raw+b' ',state['bundle'],**ids)
    def test_rehashed_invalid_files_paths_and_coverage_rejected(self):
        raw,state,ids=self.bundle()
        for mutation in ('path','hash','content','extra','coverage','errors','identity'):
            envelope=json.loads(raw)
            if mutation=='path':envelope['manifest']['files'][0]['name']='../../escape'
            if mutation=='hash':envelope['manifest']['files'][0]['sha256']='0'*64
            if mutation=='content':envelope['files']['raw.jsonl']=base64.b64encode(b'changed').decode()
            if mutation=='extra':envelope['files']['extra']=envelope['files']['raw.jsonl']
            if mutation=='coverage':envelope['manifest']['coverage']='partial'
            if mutation=='errors':envelope['manifest']['errors']=[{'code':'download_failed'}]
            if mutation=='identity':envelope['manifest']['request_id']=str(uuid.uuid4())
            body=encoded(envelope);descriptor={'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
            with self.subTest(mutation=mutation),self.assertRaises(AdquisicionRechazada):validar_bundle(body,descriptor,**ids)
    def test_duplicate_json_keys_rejected(self):
        raw,state,ids=self.bundle();body=raw.replace(b'{',b'{"files":{},',1)
        desc={'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
        with self.assertRaises(AdquisicionRechazada):validar_bundle(body,desc,**ids)
    def test_sealed_file_corruption_not_served(self):
        raw,state,ids=self.bundle();path=self.jobs.root/'bundles'/(state['id']+'.json');path.chmod(0o600);path.write_bytes(raw+b'x')
        with self.assertRaises(AdquisicionRechazada):self.client._http('GET','/'+state['id']+'/bundle',bundle=True)
    def test_duplicate_terminal_requests_return_original(self):
        request,state=self.completed();again=self.client.solicitar(request,'fixture-complete-v1')
        self.assertEqual(again['id'],state['id']);self.assertEqual(again['bundle'],state['bundle'])
    def test_different_recipe_same_request_conflicts(self):
        request,state=self.completed()
        with self.assertRaises(AdquisicionRechazada):self.client.solicitar(request,'fixture-fail-v1')
    def test_failed_and_cancelled_never_provide_bundle(self):
        req,failed=self.completed('fixture-fail-v1');self.assertEqual(failed['state'],'failed')
        job=self.client.solicitar(str(uuid.uuid4()),'fixture-slow-v1');self.client.cancelar(job['id']);state=self.client.esperar(job['id'])
        self.assertEqual(state['state'],'cancelled')
        for state in (failed,state):
            with self.assertRaises(AdquisicionRechazada):self.client._http('GET','/'+state['id']+'/bundle',bundle=True)

    def test_client_requires_explicit_simulation_activation(self):
        client=Cliente(self.url,self.file)
        with self.assertRaises(AdquisicionRechazada):client.solicitar(str(uuid.uuid4()),'fixture-complete-v1')
        with self.assertRaises(AdquisicionRechazada):client._http('GET')
