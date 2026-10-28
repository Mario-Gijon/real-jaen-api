"""Focused fixtures only. Run in the offline Linux image; no installed scraper cache is opened."""
import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from acquisition.sofascore_offline import FixtureTransport, OfflineRecipe, Policy, REVISION, SECTIONS, scope_manifest
from acquisition import real_bundle as contract
from sofascore_scraper.client import endpoints
from sofascore_scraper.slices import Outcome
from sofascore_scraper.store import Store

NOW = datetime(2026,10,26,12,tzinfo=timezone.utc).timestamp()
HOUR = 3600
IDENT = 900001  # Synthetic IDs are used only in a disposable fixture Store.


def dt(at):
    return datetime.fromtimestamp(at,timezone.utc)


def event(ident=IDENT,home=2841,away=135672,*,status='finished',start=None):
    return dict(id=ident,startTimestamp=int(NOW-24*HOUR if start is None else start),
                tournament=dict(id=94191,name='Fixture group',uniqueTournament=dict(id=17073,name='Fixture',
                    category=dict(id=32,sport=dict(id=1,slug='football'))),category=dict(id=32,sport=dict(id=1,slug='football'))),
                season=dict(id=97382,name='26/27'),roundInfo=dict(round=1),
                status=dict(type=status,code=100 if status=='finished' else 0),
                homeTeam=dict(id=home,name='Fixture home'),awayTeam=dict(id=away,name='Fixture away'),
                homeScore=dict(current=1),awayScore=dict(current=0))


def bodies():
    return dict(lineups={'home':{'players':[{'player':{'id':9002,'name':'Fixture'}}]}},
                statistics={'statistics':[{'period':'ALL','groups':[{'statisticsItems':[{'name':'Total shots','home':'1','away':'1'}]}]}]},
                incidents={'incidents':[{'incidentType':'period','text':'FT'}]})


def responses(e=None,at=NOW):
    e=e or event()
    values={endpoints.event(e['id']):Outcome('ok',data={'event':e},fetched_at=dt(at),http_status=200,via='direct')}
    values.update({endpoints.event_slice(k,e['id']):Outcome('ok',data=v,fetched_at=dt(at),http_status=200,via='direct') for k,v in bodies().items()})
    return values


def fake_state(*,start=NOW-24*HOUR, fetched=NOW-6*HOUR, checked=None, kind='ok', phase='completed'):
    values = {k: SimpleNamespace(state=kind,has_payload=kind=='ok',fetched_at=dt(fetched),
                                 checked_at=dt(fetched if checked is None else checked),empty_count=99,unverified_empty_count=99) for k in SECTIONS}
    row=SimpleNamespace(id=IDENT,sport='football',status_class=phase,start_ts=start)
    return SimpleNamespace(event=row,slice=lambda k:values[k])


class TemporalPolicy(unittest.TestCase):
    def test_six_hours_exactly(self):
        state=fake_state(); final=NOW-22*HOUR
        self.assertEqual(Policy().due(state,final,NOW-1),())
        self.assertEqual(set(Policy().due(state,final,NOW)),set(SECTIONS))

    def test_closure_and_no_rotating_history(self):
        state=fake_state(start=NOW-72*HOUR)
        final=NOW-70*HOUR
        self.assertEqual(set(Policy().due(state,final,NOW)),set(SECTIONS))
        settled=fake_state(start=NOW-72*HOUR,fetched=NOW)
        self.assertEqual(Policy().due(settled,final,NOW+100*HOUR),())

    def test_late_discovery_and_additional_check(self):
        before=fake_state(start=NOW-200*HOUR,fetched=NOW-HOUR)
        self.assertEqual(set(Policy().due(before,None,NOW)),set(SECTIONS))
        after=fake_state(start=NOW-200*HOUR,fetched=NOW)
        self.assertEqual(Policy().due(after,NOW,NOW+6*HOUR-1),())
        self.assertEqual(set(Policy().due(after,NOW,NOW+6*HOUR)),set(SECTIONS))
        closed=fake_state(start=NOW-200*HOUR,fetched=NOW+6*HOUR)
        self.assertEqual(Policy().due(closed,NOW,NOW+100*HOUR),())

    def test_first_post_final_overrides_prefinal_cache(self):
        self.assertEqual(set(Policy().due(fake_state(fetched=NOW-1),None,NOW)),set(SECTIONS))

    def test_interval_is_per_section_and_attempt(self):
        s=fake_state();old=s.slice('lineups')
        values={k:s.slice(k) for k in SECTIONS}
        values['statistics']=SimpleNamespace(**{**vars(old),'checked_at':dt(NOW-HOUR),'state':'error'})
        state=SimpleNamespace(event=s.event,slice=lambda k:values[k])
        self.assertEqual(set(Policy().due(state,NOW-22*HOUR,NOW)),set(SECTIONS))

    def test_transient_retry_boundary_is_30_seconds_not_six_hours(self):
        s=fake_state(kind='error',checked=NOW-10)
        values={k:s.slice(k) for k in SECTIONS}
        for v in values.values():v.error=SimpleNamespace(reason='network',count=1)
        state=SimpleNamespace(event=s.event,slice=lambda k:values[k])
        self.assertEqual(Policy().due(state,NOW-22*HOUR,NOW),())
        self.assertEqual(set(Policy().due(state,NOW-22*HOUR,NOW+20)),set(SECTIONS))

    def test_empty_counters_do_not_settle_and_clock_is_validated(self):
        self.assertEqual(set(Policy().due(fake_state(kind='empty'),NOW-22*HOUR,NOW)),set(SECTIONS))
        with self.assertRaises(ValueError):Policy().due(fake_state(fetched=NOW+1),NOW-22*HOUR,NOW)
        with self.assertRaises(ValueError):Policy(interval_s=0)
        self.assertEqual(Policy(window_s=24*HOUR,interval_s=HOUR).interval_s,HOUR)


class PipelineAdapter(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rj-offline-recipe-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'cache')
        self.addCleanup(self.store.close)

    def seed(self,e=None,at=NOW-7*HOUR,sections=True):
        e=e or event()
        outcomes={'event':Outcome('ok',data=e,fetched_at=dt(at))}
        if sections:outcomes.update({k:Outcome('ok',data=v,fetched_at=dt(at)) for k,v in bodies().items()})
        self.store.events.put(e['id'],outcomes)
        return list(self.store.events.states())[0]

    def recipe(self,fixture=None,**kwargs):
        return OfflineRecipe(self.store,FixtureTransport(fixture or responses()),self.root/'checks.json',**kwargs)

    def test_dedup_refresh_cached_sections_and_no_change(self):
        state=self.seed();recipe=self.recipe(baseline={str(IDENT):NOW-22*HOUR})
        result=recipe.run([state,state],now=NOW)
        self.assertTrue(result['details_complete']);self.assertFalse(result['complete']) # No calendar proof in details-only run.
        self.assertEqual(len(result['calls']),4)
        self.assertEqual(len(set(result['calls'])),4)
        result=recipe.run(list(self.store.events.states()),now=NOW+HOUR)
        self.assertTrue(result['details_complete']);self.assertEqual(result['calls'],[])

    def test_only_due_section_is_requested(self):
        self.seed()
        self.store.events.put(IDENT,{k:Outcome('ok',data=bodies()[k],fetched_at=dt(NOW-HOUR)) for k in ('lineups','incidents')})
        state=list(self.store.events.states())[0]
        result=self.recipe(baseline={str(IDENT):NOW-22*HOUR}).run([state],now=NOW)
        self.assertTrue(result['details_complete'])
        self.assertEqual(result['calls'],[endpoints.event(IDENT),endpoints.event_slice('statistics',IDENT)])

    def test_malformed_section_is_failure_not_absence(self):
        state=self.seed(sections=False);fixture=responses()
        fixture[endpoints.event_slice('statistics',IDENT)]=Outcome('ok',data=False,fetched_at=dt(NOW))
        result=self.recipe(fixture).run([state],now=NOW)
        self.assertFalse(result['details_complete'])
        self.assertTrue(any(e[1]=='statistics' for e in result['errors']))

    def test_restart_checkpoint_and_no_automatic_run(self):
        state=self.seed(sections=False);r=self.recipe()
        self.assertTrue(r.run([state],now=NOW)['details_complete'])
        r2=self.recipe()
        self.assertEqual(r2.transport.calls,[])
        self.assertTrue(r2.run(list(self.store.events.states()),now=NOW+HOUR)['details_complete'])
        self.assertEqual(r2.transport.calls,[])

    def test_new_final_replans_without_duplicate_get(self):
        state=self.seed(event(status='inprogress'),at=NOW-1,sections=False)
        result=self.recipe().run([state],now=NOW)
        self.assertTrue(result['details_complete'])
        self.assertEqual(len(result['calls']),4)
        self.assertEqual(result['calls'].count(endpoints.event(IDENT)),1)

    def test_future_event_uses_calendar_and_no_sections(self):
        state=self.seed(event(status='notstarted',start=NOW+HOUR),at=NOW-1,sections=False)
        result=self.recipe().run([state],now=NOW)
        self.assertEqual(result['calls'],[])
        self.assertTrue(all(c['status']=='not_applicable' for c in result['coverage']))

    def test_failure_keeps_previous_data_but_rejects_completion(self):
        state=self.seed();fixture=responses()
        fixture[endpoints.event_slice('statistics',IDENT)]=Outcome('failed',reason='timeout',fetched_at=dt(NOW))
        result=self.recipe(fixture,baseline={str(IDENT):NOW-22*HOUR}).run([state],now=NOW)
        self.assertFalse(result['details_complete']);self.assertFalse(result['complete'])
        self.assertTrue(self.store.events.slice(IDENT,'statistics').has_payload)
        later=self.recipe(fixture,baseline={str(IDENT):NOW-22*HOUR}).run(list(self.store.events.states()),now=NOW+HOUR)
        self.assertFalse(later['details_complete'])

    def test_empty_and_404_never_confirm_absence(self):
        for status in (200,404):
            with self.subTest(status=status):
                (self.root/'checks.json').unlink(missing_ok=True)
                state=list(self.store.events.states())[0] if list(self.store.events.states()) else self.seed()
                fixture=responses()
                fixture[endpoints.event_slice('incidents',IDENT)]=Outcome('empty',data={} if status==200 else None,reason=str(status),http_status=status,fetched_at=dt(NOW))
                result=self.recipe(fixture).run([state],now=NOW)
                self.assertFalse(result['details_complete'])
                self.assertIn(endpoints.event_slice('incidents',IDENT), result['calls'])
                self.assertNotIn('confirmed_absence',{c['status'] for c in result['coverage']})

    def test_cancel_before_work_and_after_first_event(self):
        state=self.seed(sections=False)
        r=self.recipe();self.assertFalse(r.run([state],now=NOW,cancelled=lambda:True)['details_complete'])
        self.assertEqual(r.transport.calls,[])
        flag=[False]
        t=FixtureTransport(responses(),after_call=lambda p:flag.__setitem__(0,True))
        r=OfflineRecipe(self.store,t,self.root/'cancel.json')
        result=r.run([state],now=NOW,cancelled=lambda:flag[0])
        self.assertTrue(result['cancelled']);self.assertFalse(result['details_complete'])
        self.assertTrue(t.closed)

    def test_wrong_scope_unplanned_paths_and_real_client_rejected(self):
        state=self.seed(sections=False);fixture=responses();fixture[endpoints.event(IDENT)].data['event']['season']['id']=1
        with self.assertRaises(ValueError):self.recipe(fixture).run([state],now=NOW)
        self.assertEqual(self.store.events.get(IDENT).season_id,97382)
        with self.assertRaises(TypeError):OfflineRecipe(self.store,object(),self.root/'no.json')
        t=FixtureTransport({})
        with self.assertRaises(ValueError):asyncio.run(t.get('https://example.invalid'))

    def test_budget_fails_before_next_operation(self):
        state=self.seed(sections=False)
        r=OfflineRecipe(self.store,FixtureTransport(responses(),max_calls=1),self.root/'budget.json')
        with self.assertRaises(RuntimeError):r.run([state],now=NOW)
        self.assertEqual(len(r.transport.calls),1)

    def test_calendar_pipeline_cache_and_full_20_team_coverage(self):
        ids=sorted(t['external_id'] for t in scope_manifest()['teams'])
        events=[event(ident,ids[2*(i%10)],ids[2*(i%10)+1],status='notstarted',start=NOW+HOUR) for i,ident in enumerate(scope_manifest()['known_event_ids'])]
        fixture={endpoints.rounds(17073,97382):Outcome('ok',data={'rounds':[{'round':1}]},fetched_at=dt(NOW)),
                 endpoints.round_events(17073,97382,1):Outcome('ok',data={'events':events},fetched_at=dt(NOW))}
        r=self.recipe(fixture)
        result=r.collect(now=NOW)
        self.assertTrue(result['complete'],result['errors'])
        self.assertEqual(len(result['calls']),2)
        self.assertTrue(r.collect(now=NOW+60)['complete'])
        self.assertEqual(r.transport.calls,[])
        partial={**fixture,endpoints.round_events(17073,97382,1):Outcome('failed',reason='timeout',fetched_at=dt(NOW))}
        partial.update({endpoints.season_events_page(17073,97382,k,0):Outcome('failed',reason='timeout',fetched_at=dt(NOW)) for k in ('last','next')})
        # Separate empty fixture Store, never reuse stale successful listing to hide failure.
        other=Store(self.root/'other');self.addCleanup(other.close)
        r2=OfflineRecipe(other,FixtureTransport(partial),self.root/'other.json')
        self.assertFalse(r2.collect(now=NOW)['complete'])

    def test_interrupted_calendar_cannot_reuse_old_fresh_success(self):
        ids=sorted(t['external_id'] for t in scope_manifest()['teams'])
        events=[event(ident,ids[2*(i%10)],ids[2*(i%10)+1],status='notstarted',start=NOW+HOUR) for i,ident in enumerate(scope_manifest()['known_event_ids'])]
        fixture={endpoints.rounds(17073,97382):Outcome('ok',data={'rounds':[{'round':1}]},fetched_at=dt(NOW)),
                 endpoints.round_events(17073,97382,1):Outcome('ok',data={'events':events},fetched_at=dt(NOW))}
        r=self.recipe(fixture);self.assertTrue(r.collect(now=NOW)['complete'])
        # Simulate the durable marker left before a failed/interrupted calendar refresh.
        saved=json.loads(r.path.read_text());saved['_calendar']={'valid':False};r.path.write_text(json.dumps(saved))
        failed={p:Outcome('failed',reason='timeout',fetched_at=dt(NOW)) for p in
                (endpoints.rounds(17073,97382),endpoints.season_events_page(17073,97382,'last',0),endpoints.season_events_page(17073,97382,'next',0))}
        retry=self.recipe(failed).collect(now=NOW+60)
        self.assertFalse(retry['complete']);self.assertEqual(len(retry['calls']),3)

    def test_validated_browser_wrapper_reused_offline(self):
        from acquisition.profile import Profile
        async def check(owner):
            async with OfflineRecipe.browser(owner) as browser:
                self.assertTrue(browser.sandbox['proc_verified'])
                page=await browser.context.new_page()
                await page.goto('data:text/html,<title>Recipe offline</title><p>local</p>')
                self.assertEqual(await page.title(),'Recipe offline')
        (self.root/'profile').mkdir()
        with Profile(self.root/'profile') as owner:
            owner.initialize(confirm_new=True)
            asyncio.run(check(owner))
            self.assertIsNotNone(owner.verify())


def bundle_fixture():
    teams=sorted(t['external_id'] for t in scope_manifest()['teams'])
    files={f'team_{i}.jsonl':[] for i in teams};files['calendar.jsonl']=[]
    coverage=[]
    for i,ident in enumerate(scope_manifest()['known_event_ids']):
        e=event(ident,teams[2*(i%10)],teams[2*(i%10)+1])
        calendar=dict(id=e['id'],tournament_id=17073,stage={'id':94191},season_id=97382,status=e['status'],
                      participants={s:{'id':e[s+'Team']['id']} for s in ('home','away')},quality={'observed_at_utc':dt(NOW).isoformat()})
        files['calendar.jsonl'].append(calendar)
        evidence={'calendar':{'file':'calendar.jsonl','line':i+1}}
        for team in (e['homeTeam']['id'],e['awayTeam']['id']):
            name=f'team_{team}.jsonl'
            for key,payload in [('event',e),*bodies().items()]:
                files[name].append(dict(event_id=e['id'],key=key,sub='',fetched_at=dt(NOW).isoformat(),payload=payload))
                evidence.setdefault(key,{'file':name,'line':len(files[name])})
        coverage += [dict(event_id=e['id'],resource=k,status='data',checked_at=dt(NOW).isoformat(),evidence=ref,rule=None) for k,ref in evidence.items()]
    raw={name:(''.join(json.dumps(row,separators=(',',':'))+'\n' for row in rows)).encode() for name,rows in files.items()}
    descriptors=[dict(name=name,kind='calendar' if name=='calendar.jsonl' else 'own_export',
                     team_external_id=None if name=='calendar.jsonl' else int(name[5:-6]),bytes=len(value),sha256=contract.sha(value)) for name,value in raw.items()]
    return dict(manifest=dict(schema=2,job_id='11111111-1111-4111-8111-111111111111',request_id='22222222-2222-4222-8222-222222222222',rules_sha256=contract.fingerprint(),provider='sofascore',provider_revision=REVISION,is_simulated=False,
                             scope_sha256=contract.sha(Path(__import__('acquisition.sofascore_offline',fromlist=['x']).__file__).with_name('sofascore-scope.json').read_bytes()),
                             recipe_id='sofascore-ambito-v1',started_at=dt(NOW).isoformat(),finished_at=dt(NOW).isoformat(),
                             policy={'window_s':72*HOUR,'interval_s':6*HOUR},files=descriptors,coverage=coverage,errors=[]),
                files={name:base64.b64encode(raw).decode() for name,raw in raw.items()})


class RealContract(unittest.TestCase):
    def setUp(self):self.b=bundle_fixture()
    def check(self,**kwargs):
        kwargs.setdefault('required_event_ids',scope_manifest()['known_event_ids']) # This fixture's independent inventory, not a production total.
        return contract.verify(json.dumps(self.b).encode(),**kwargs)
    def test_job_context_and_rules(self):
        with self.assertRaises(contract.BundleRejected):self.check(expected_context=('wrong','wrong'))
        self.b['manifest']['rules_sha256']='0'*64
        with self.assertRaises(contract.BundleRejected):self.check()
    def test_complete(self):self.assertEqual(self.check()['schema'],2)
    def test_pending_failed_missing_duplicate_and_false_not_applicable(self):
        for status in ('failed','pending','confirmed_absence','not_applicable'):
            with self.subTest(status=status):
                self.b=bundle_fixture();self.b['manifest']['coverage'][2]['status']=status
                with self.assertRaises(contract.BundleRejected):self.check()
        self.b=bundle_fixture();self.b['manifest']['coverage'].pop()
        with self.assertRaises(contract.BundleRejected):self.check()
        self.b=bundle_fixture();self.b['manifest']['coverage'].append(self.b['manifest']['coverage'][0])
        with self.assertRaises(contract.BundleRejected):self.check()
    def test_absence_contradiction_is_explicit(self):
        c=self.b['manifest']['coverage'][2];c['status']='confirmed_absence'
        with self.assertRaisesRegex(contract.BundleRejected,'contradicts'):
            self.check(prior_data={(c['event_id'],c['resource'])})
    def test_hash_tamper_and_arbitrary_path(self):
        key=next(iter(self.b['files']));self.b['files'][key]=base64.b64encode(b'{}\n').decode()
        with self.assertRaises(contract.BundleRejected):self.check()
        self.b=bundle_fixture();self.b['files']['../secret']=''
        with self.assertRaises(contract.BundleRejected):self.check()
    def test_size_policy_unknown_fields_and_backend_inventory(self):
        from unittest.mock import patch
        with patch.object(contract,'MAX_FILE',10):
            with self.assertRaises(contract.BundleRejected):self.check()
        with self.assertRaises(contract.BundleRejected):self.check(required_event_ids={999999999})
        self.b['manifest']['extra']='forbidden'
        with self.assertRaises(contract.BundleRejected):self.check()

    def rewrite(self,name,rows):
        raw=(''.join(json.dumps(row)+'\n' for row in rows)).encode()
        self.b['files'][name]=base64.b64encode(raw).decode()
        f=next(f for f in self.b['manifest']['files'] if f['name']==name)
        f.update(bytes=len(raw),sha256=contract.sha(raw))

    def test_truncated_calendar_with_valid_hash_rejected(self):
        rows=[json.loads(l) for l in base64.b64decode(self.b['files']['calendar.jsonl']).splitlines()]
        self.rewrite('calendar.jsonl',rows[:-1])
        with self.assertRaisesRegex(contract.BundleRejected,'omits'):self.check()

    def test_shared_sections_must_match(self):
        name=list(self.b['files'])[1]
        rows=[json.loads(l) for l in base64.b64decode(self.b['files'][name]).splitlines()]
        rows[1]['payload']={'home':{'players':[{'player':{'id':999999}}]}}
        self.rewrite(name,rows)
        with self.assertRaisesRegex(contract.BundleRejected,'Shared resource'):self.check()

    def test_coverage_reference_mismatch(self):
        self.b['manifest']['coverage'][2]['evidence']['line']=1
        with self.assertRaises(contract.BundleRejected):self.check()

    def test_missing_calendar_provenance(self):
        raw=base64.b64decode(self.b['files']['calendar.jsonl'])
        rows=[json.loads(l) for l in raw.splitlines()];rows[0]['quality']['observed_at_utc']=None
        raw=(''.join(json.dumps(row)+'\n' for row in rows)).encode()
        self.b['files']['calendar.jsonl']=base64.b64encode(raw).decode()
        f=next(f for f in self.b['manifest']['files'] if f['name']=='calendar.jsonl');f.update(bytes=len(raw),sha256=contract.sha(raw))
        with self.assertRaises(contract.BundleRejected):self.check()

if __name__=='__main__':unittest.main(verbosity=2)
