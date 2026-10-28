"""Pilot v2 wire protocol. Standard library + existing API rules; no scraper/browser imports."""
import base64
from datetime import datetime
import hashlib
import json
import uuid
import api
import importar

SECTIONS=('lineups','statistics','incidents')
PILOT=16599363
PATHS=('/unique-tournament/17073/season/97382/events/round/1',
       '/event/16599363','/event/16599363/lineups','/event/16599363/statistics','/event/16599363/incidents')
MAX_WIRE=96*1024*1024
MAX_FILE=4*1024*1024
MAX_TOTAL=64*1024*1024
MAX_LINE=2*1024*1024

class Rejected(ValueError): pass

def encode(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def fingerprint(value):return sha(encode(value))
def unique(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:raise Rejected('Duplicate JSON key')
        obj[k]=v
    return obj
def parse(raw):return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(Rejected('Nonfinite JSON')))
def instant(value):
    at=datetime.fromisoformat(value)
    if not at.tzinfo:raise Rejected('Missing timezone')
    return at.timestamp()
def record(row):return {k:row.get(k) for k in ('event_id','key','sub','fetched_at','payload')}

def inventory(generation):
    """Copy immutable published inventory, not a second SQLite connection or a provider decision."""
    data=generation._datos
    matches={m['match_id']:m for m in data['partidos']}
    teams=sorted({e['team_id'] for e in api.equipos_con_exportacion(data)})
    if len(teams)!=20:raise Rejected('Pilot requires the verified scope of 20 own teams')
    rows=[]
    for (ident,key),value in sorted(data['originales'].items()):
        if ident in matches and key in ('event','listing',*SECTIONS):
            source=value['registro']
            rows.append(dict(record=record(source),source={k:source.get(k) for k in ('export','line','content_hash','fetched_at_valid')}))
    result=dict(schema=1,generation_id=generation.id,teams=teams,
                events=[dict(id=i,current=api.vigente(m),replaced_by=(m.get('replaced_by') or {}).get('match_id'),
                             home=m['home_team_id'],away=m['away_team_id'],status=m['status'],
                             tournament_id=m['tournament_id'],season_id=m['season_id']) for i,m in sorted(matches.items())],records=rows)
    return dict(result,sha256=fingerprint(result))

def check_inventory(value):
    if fingerprint({k:v for k,v in value.items() if k!='sha256'})!=value['sha256']:raise Rejected('Inventory hash mismatch')
    events={e['id']:e for e in value['events']}
    if len(events)!=len(value['events']) or len(set(value['teams']))!=20:raise Rejected('Invalid inventory')
    for e in events.values():
        if e['current'] is not (e['replaced_by'] is None):raise Rejected('Invalid lifecycle')
        if not e['current'] and (e['replaced_by'] not in events or not events[e['replaced_by']]['current']):raise Rejected('Invalid replacement')
        if (e['tournament_id'],e['season_id'])!=(94191,97382):raise Rejected('Inventory outside pilot scope')
    return events

def verify(body,descriptor,baseline,*,context,expected_rules,test_transport=False):
    try:return _verify(body,descriptor,baseline,context=context,expected_rules=expected_rules,test_transport=test_transport)
    except Rejected:raise
    except (ValueError,TypeError,KeyError,AttributeError,IndexError,UnicodeError,OverflowError):raise Rejected('Malformed v2 bundle') from None

def _verify(body,descriptor,baseline,*,context,expected_rules,test_transport):
    expected=check_inventory(baseline)
    if set(descriptor)!={'bytes','sha256'} or type(descriptor['bytes']) is not int or len(body)!=descriptor['bytes'] or len(body)>MAX_WIRE or sha(body)!=descriptor['sha256']:raise Rejected('Bundle descriptor mismatch')
    obj=parse(body)
    if set(obj)!={'manifest','files'}:raise Rejected('Unknown envelope')
    m=obj['manifest']
    fields={'schema','contract','job_id','request_id','recipe_id','provider_revision','transport','inventory_sha256','generation_id','rules_sha256','policy','started_at','finished_at','files','coverage','retained','attempts','errors'}
    if set(m)!=fields or type(m['schema']) is not int or m['schema']!=2 or m['contract']!='pilot-v2':raise Rejected('Incompatible contract')
    if any(str(uuid.UUID(m[k]))!=m[k] for k in ('job_id','request_id')) or tuple(m[k] for k in ('job_id','request_id'))!=tuple(context):raise Rejected('Wrong job context')
    if m['recipe_id']!='sofascore-pilot-16599363-v1' or m['provider_revision']!='611e436a1528b0f487aa56a045f023e8ae8ae098':raise Rejected('Wrong recipe/revision')
    if m['transport']!=('loopback-fixture' if test_transport else 'sofascore-https'):raise Rejected('Wrong transport provenance')
    if not expected_rules or m['rules_sha256']!=expected_rules:raise Rejected('Unapproved producer rules')
    if m['inventory_sha256']!=baseline['sha256'] or m['generation_id']!=baseline['generation_id']:raise Rejected('Inventory generation mismatch')
    if m['errors'] or m['policy']!={'window_s':259200,'interval_s':21600}:raise Rejected('Incomplete acquisition or unexpected policy')
    begin,end=instant(m['started_at']),instant(m['finished_at'])
    if end<begin:raise Rejected('Invalid clock')
    retained=sorted(e['id'] for e in expected.values() if not e['current'])
    if m['retained']!=retained:raise Rejected('Local history changed')
    attempts=m['attempts'];latest={};counts={}
    if not 5<=len(attempts)<=16:raise Rejected('Attempt budget mismatch')
    for n,a in enumerate(attempts,1):
        if set(a)!={'number','path','sent_at','finished_at','status','payload_sha256'} or a['number']!=n or a['path'] not in PATHS:raise Rejected('Unplanned attempt')
        counts[a['path']]=counts.get(a['path'],0)+1
        if counts[a['path']]>2:raise Rejected('Per-resource budget exceeded')
        if not begin<=instant(a['sent_at'])<=instant(a['finished_at'])<=end:raise Rejected('Invalid attempt clock')
        latest[a['path']]=a
    if set(latest)!=set(PATHS) or any(a['status']!=200 for a in latest.values()):raise Rejected('Pilot did not finish every planned resource')
    wanted={f'team_{i}.jsonl':i for i in baseline['teams']};wanted['calendar.jsonl']=None;wanted['provenance.jsonl']=None
    if set(obj['files'])!=set(wanted) or len(m['files'])!=len(wanted):raise Rejected('Own exports/calendar missing')
    entries={e['name']:e for e in m['files']}
    if len(entries)!=len(wanted) or set(entries)!=set(wanted):raise Rejected('Duplicate file descriptor')
    decoded={};rows={};total=0
    for name,owner in wanted.items():
        d=entries[name]
        if set(d)!={'name','kind','team_external_id','bytes','sha256'} or d['team_external_id']!=owner or d['kind']!=(('calendar' if name=='calendar.jsonl' else 'provenance') if owner is None else 'own_export'):raise Rejected('Invalid file owner')
        raw=base64.b64decode(obj['files'][name],validate=True);total+=len(raw)
        if len(raw)>MAX_FILE or total>MAX_TOTAL or type(d['bytes']) is not int or d['bytes']!=len(raw) or sha(raw)!=d['sha256']:raise Rejected('Invalid file size/hash')
        lines=raw.splitlines()
        if any(not l or len(l)>MAX_LINE for l in lines):raise Rejected('Invalid JSONL')
        decoded[name]=raw;rows[name]=[parse(l) for l in lines]
    provenance={}
    for e in rows['provenance.jsonl']:
        if set(e)!={'number','path','received_at','body_b64'} or type(e['number']) is not int or e['number'] in provenance or not 1<=e['number']<=len(attempts):raise Rejected('Invalid original response')
        a=attempts[e['number']-1];original=base64.b64decode(e['body_b64'],validate=True);payload=parse(original)
        selected=importar.evento_de(payload) if a['path']==PATHS[1] else payload
        if a['status']!=200 or e['path']!=a['path'] or e['received_at']!=a['finished_at'] or fingerprint(selected)!=a['payload_sha256']:raise Rejected('Original response/attempt mismatch')
        provenance[e['number']]=payload
    if not {a['number'] for a in latest.values()}<=set(provenance):raise Rejected('Original successful responses missing')
    calendar={}
    for row in rows['calendar.jsonl']:
        ident=row['id']
        if type(ident) is not int or ident in calendar:raise Rejected('Duplicate calendar ID')
        parsed=importar.partido_de_listado(row)
        if (row['tournament_id'],parsed['tournament_id'],parsed['season_id'])!=(17073,94191,97382):raise Rejected('Calendar scope mismatch')
        if not {parsed['home_team_id'],parsed['away_team_id']}<=set(baseline['teams']):raise Rejected('Calendar participants outside scope')
        if ident in expected and (parsed['home_team_id'],parsed['away_team_id'])!=(expected[ident]['home'],expected[ident]['away']):raise Rejected('Calendar changed identity')
        if ident in retained and parsed['status']!=expected[ident]['status']:raise Rejected('Historical event modified')
        calendar[ident]=row
    if not set(expected)<=set(calendar):raise Rejected('Inventory events missing from calendar')
    # The pilot never silently expands its sporting selection; new IDs need another preflight.
    if set(calendar)!=set(expected):raise Rejected('New event requires preflight revision')
    raw_records={};owned={};prior={(v['record']['event_id'],v['record']['key']):v['record'] for v in baseline['records']}
    for name,owner in wanted.items():
        if owner is None:continue
        seen=set()
        for row in rows[name]:
            ident,key=row['event_id'],row['key'];pair=(ident,key)
            if ident not in expected or not expected[ident]['current'] or key not in ('event',*SECTIONS) or row.get('sub')!='' or pair in seen:raise Rejected('Unexpected raw resource')
            seen.add(pair)
            if owner not in (expected[ident]['home'],expected[ident]['away']):raise Rejected('Wrong own export')
            if pair in raw_records and record(row)!=raw_records[pair]:raise Rejected('Shared resource conflict')
            raw_records[pair]=record(row);owned.setdefault(pair,set()).add(owner)
            if key=='event':
                event=importar.evento_de(row['payload'])
                if (event['id'],event['tournament']['id'],event['tournament']['uniqueTournament']['id'],event['season']['id'])!=(ident,94191,17073,97382):raise Rejected('Raw scope mismatch')
                if (event['homeTeam']['id'],event['awayTeam']['id'])!=(expected[ident]['home'],expected[ident]['away']):raise Rejected('Raw identity mismatch')
    for pair,owners in owned.items():
        e=expected[pair[0]]
        if owners!={e['home'],e['away']}:raise Rejected('Shared event missing own export')
    coverage=m['coverage'];seen=set();required={(i,k) for i,e in expected.items() if e['current'] for k in ('calendar','event',*SECTIONS)}
    if len(encode(coverage))>4*1024*1024:raise Rejected('Coverage too large')
    for c in coverage:
        if set(c)!={'event_id','resource','status','origin','evidence','checked_at'}:raise Rejected('Invalid coverage')
        pair=c['event_id'],c['resource']
        if pair not in required or pair in seen:raise Rejected('Unexpected coverage')
        seen.add(pair);ident,key=pair
        if c['status']=='confirmed_absence':raise Rejected('Absence rule absent; contradictions cannot publish')
        if c['status']=='not_applicable':
            ev=raw_records.get((ident,'event'));status=(importar.evento_de(ev['payload']).get('status') or {}).get('type') if ev else calendar[ident]['status']['type']
            if key not in SECTIONS and not (key=='event' and ev is None):raise Rejected('Not applicable invalid resource')
            if status not in api.PENDIENTES and status!='inprogress':raise Rejected('Not applicable invalid phase')
            if c['evidence'] is not None or c['origin']!='phase':raise Rejected('Invalid phase proof')
            continue
        if c['status']!='data':raise Rejected('Incomplete sporting coverage')
        ref=c['evidence']
        if set(ref)!={'file','line'} or type(ref['line']) is not int or ref['file'] not in rows or not 1<=ref['line']<=len(rows[ref['file']]):raise Rejected('Invalid evidence reference')
        row=rows[ref['file']][ref['line']-1]
        if key=='calendar':
            if ref['file']!='calendar.jsonl' or row['id']!=ident:raise Rejected('Calendar evidence mismatch')
            at=(row.get('quality') or {}).get('observed_at_utc')
            if at is None:
                old=prior.get((ident,'listing'))
                if c['origin']!='inventory' or old is None or old['payload']!=row:raise Rejected('Unknown calendar provenance not preserved')
        else:
            if ref['file']=='calendar.jsonl' or (row['event_id'],row['key'])!=pair:raise Rejected('Raw evidence mismatch')
            at=row['fetched_at']
            if key in SECTIONS and (not isinstance(row['payload'],(dict,list)) or api.vacia(key,row['payload'])):raise Rejected('Empty resource cannot mean data')
        if ident!=PILOT and c['checked_at']!=at:raise Rejected('Invented observation timestamp')
        if at is not None and instant(at)>end:raise Rejected('Future evidence')
        if key!='calendar' and ident!=PILOT:
            if c['origin']!='inventory' or record(row)!=prior.get(pair):raise Rejected('Pilot altered an unqueried resource')
        if ident==PILOT:
            path=(PATHS[0] if row.get('quality',{}).get('source')=='listing' else f'/event/{PILOT}') if key=='calendar' else f'/event/{PILOT}'+('' if key=='event' else '/'+key)
            a=latest[path]
            if c['origin']!='attempt' or c['checked_at']!=a['finished_at'] or (at is not None and instant(at)>instant(c['checked_at'])):raise Rejected('Pilot resource not newly checked')
            if key!='calendar' and fingerprint(row['payload'])!=a['payload_sha256']:raise Rejected('Payload/attempt mismatch')
    if seen!=required:raise Rejected('Resource coverage missing')
    return m,decoded
