"""Proposed real bundle v2: offline verifier only; not connected to Jobs or publication."""
import base64
from datetime import datetime
import hashlib
import json
import uuid
from pathlib import Path
from .sofascore_offline import Policy, REVISION, SECTIONS, scope_manifest

MIB = 1024 * 1024
MAX_FILE = 4 * MIB
MAX_DECODED = 64 * MIB
MAX_WIRE = 96 * MIB
MAX_LINE = 2 * MIB
MAX_COVERAGE = 4 * MIB
# No absence semantics for these endpoints have yet been validated.
ABSENCE_RULES = frozenset()


class BundleRejected(ValueError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(policy=Policy()):
    files = ('sofascore_offline.py','real_bundle.py','sofascore-scope.json')
    source = {name:sha(Path(__file__).with_name(name).read_bytes()) for name in files}
    return sha(json.dumps(dict(source=source,provider_revision=REVISION,
                               window_s=policy.window_s,interval_s=policy.interval_s),sort_keys=True).encode())


def object_unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise BundleRejected('Duplicate JSON key')
        obj[key] = value
    return obj


def parse(raw):
    return json.loads(raw, object_pairs_hook=object_unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(BundleRejected('Nonfinite JSON')))


def timestamp(value):
    at = datetime.fromisoformat(value)
    if not at.tzinfo:
        raise BundleRejected('Timestamp without timezone')
    return at.timestamp()


def verify(body, *, prior_data=frozenset(), required_event_ids=frozenset(), expected_context=None, policy=Policy()):
    """Reject partial, ambiguous and contradictory evidence before any importer is invoked.

    This is an offline contract checker, not an authenticated transport or a publisher.
    """
    try:
        return _verify(body, prior_data=prior_data, required_event_ids=required_event_ids, expected_context=expected_context, policy=policy)
    except BundleRejected:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, UnicodeError, OverflowError):
        raise BundleRejected('Malformed real bundle') from None


def _verify(body, *, prior_data, required_event_ids, expected_context, policy):
    from sofascore_scraper.services.pipeline import body_state
    from sofascore_scraper.services.planning import phase_of
    from sofascore_scraper.status import classify_status
    if len(body) > MAX_WIRE:
        raise BundleRejected('Wire size limit')
    bundle = parse(body)
    if set(bundle) != {'manifest', 'files'}:
        raise BundleRejected('Unknown envelope fields')
    m = bundle['manifest']
    if set(m) != {'schema','provider','provider_revision','is_simulated','scope_sha256','recipe_id',
                  'started_at','finished_at','policy','files','coverage','errors','job_id','request_id','rules_sha256'}:
        raise BundleRejected('Unknown manifest fields')
    if any(str(uuid.UUID(m[k])) != m[k] for k in ('job_id','request_id')):
        raise BundleRejected('Invalid job context')
    if expected_context is not None and (m['job_id'],m['request_id']) != tuple(expected_context):
        raise BundleRejected('Job context mismatch')
    if m['rules_sha256'] != fingerprint(policy):
        raise BundleRejected('Recipe rules mismatch')
    scope_path = Path(__file__).with_name('sofascore-scope.json')
    if (type(m['schema']) is not int or m['schema'] != 2 or m['provider'] != 'sofascore' or
        m['provider_revision'] != REVISION or m['is_simulated'] is not False or
        m['scope_sha256'] != sha(scope_path.read_bytes()) or m['recipe_id'] != 'sofascore-ambito-v1' or
        m['policy'] != {'window_s':policy.window_s,'interval_s':policy.interval_s} or m['errors'] != []):
        raise BundleRejected('Incompatible or incomplete manifest')
    begin, end = timestamp(m['started_at']), timestamp(m['finished_at'])
    if end < begin:
        raise BundleRejected('Invalid job clock')
    teams = {t['external_id'] for t in scope_manifest()['teams']}
    expected_files = {f'team_{i}.jsonl':i for i in teams}
    expected_files['calendar.jsonl'] = None
    if set(bundle['files']) != set(expected_files) or len(m['files']) != 21:
        raise BundleRejected('Exactly 20 own exports and calendar required')
    descriptors = {f['name']:f for f in m['files']}
    if len(descriptors) != 21 or set(descriptors) != set(expected_files):
        raise BundleRejected('Duplicate or unexpected file descriptor')
    rows, decoded_size = {}, 0
    for name, owner in expected_files.items():
        f = descriptors[name]
        if set(f) != {'name','kind','team_external_id','bytes','sha256'} or f['team_external_id'] != owner or f['kind'] != ('calendar' if owner is None else 'own_export'):
            raise BundleRejected('Invalid own export identity')
        raw = base64.b64decode(bundle['files'][name], validate=True)
        decoded_size += len(raw)
        if type(f['bytes']) is not int or f['bytes'] != len(raw) or len(raw) > MAX_FILE or sha(raw) != f['sha256'] or decoded_size > MAX_DECODED:
            raise BundleRejected('File hash or size mismatch')
        lines = raw.splitlines()
        if any(not line or len(line) > MAX_LINE for line in lines):
            raise BundleRejected('Invalid JSONL line')
        rows[name] = [parse(line) for line in lines]
    calendar = {}
    participants = set()
    for index, row in enumerate(rows['calendar.jsonl'], 1):
        ident = row['id']
        if type(ident) is not int or ident in calendar or (row['tournament_id'],row['stage']['id'],row['season_id']) != (17073,94191,97382):
            raise BundleRejected('Ambiguous calendar or scope')
        pair = {row['participants'][s]['id'] for s in ('home','away')}
        if len(pair) != 2 or not pair <= teams:
            raise BundleRejected('Calendar membership mismatch')
        participants.update(pair)
        calendar[ident] = row
    if not set(required_event_ids) <= set(calendar):
        raise BundleRejected('Calendar omits previously known scoped events')
    if participants != teams:
        raise BundleRejected('Calendar does not cover 20 teams')
    events = {}
    shared = {}
    owned_keys = {}
    for name, owner in expected_files.items():
        if owner is None:
            continue
        seen = set()
        for row in rows[name]:
            ident, key = row['event_id'], row['key']
            if key not in ('event',*SECTIONS) or row.get('sub','') != '' or (ident,key) in seen or ident not in calendar:
                raise BundleRejected('Unexpected or duplicate raw resource')
            seen.add((ident,key))
            record = (row['payload'],row['fetched_at'])
            if (ident,key) in shared and shared[ident,key] != record:
                raise BundleRejected('Shared resource differs between own exports')
            shared[ident,key] = record
            owned_keys.setdefault((owner,ident),set()).add(key)
            if owner not in {calendar[ident]['participants'][s]['id'] for s in ('home','away')}:
                raise BundleRejected('Export does not belong to its owner')
            if timestamp(row['fetched_at']) > end:
                raise BundleRejected('Future raw provenance')
            if key == 'event':
                event = row['payload']
                if (event['id'],event['tournament']['id'],event['tournament']['uniqueTournament']['id'],event['season']['id']) != (ident,94191,17073,97382):
                    raise BundleRejected('Raw event scope mismatch')
                if any(event[side+'Team']['id'] != calendar[ident]['participants'][side]['id'] for side in ('home','away')):
                    raise BundleRejected('Calendar/event correspondence mismatch')
                if ident in events and events[ident] != event:
                    raise BundleRejected('Shared event has conflicting exports')
                events[ident] = event
    for ident,event in events.items():
        for side in ('home','away'):
            owner = event[side+'Team']['id']
            if owned_keys.get((owner,ident),set()) != {k for e,k in shared if e==ident}:
                raise BundleRejected('Own exports omit shared resources')
    coverage = m['coverage']
    if len(json.dumps(coverage,separators=(',',':')).encode()) > MAX_COVERAGE:
        raise BundleRejected('Coverage size limit')
    expected = {(i,k) for i in calendar for k in ('calendar','event',*SECTIONS)}
    actual = set()
    for c in coverage:
        if set(c) != {'event_id','resource','status','checked_at','evidence','rule'}:
            raise BundleRejected('Invalid coverage fields')
        pair = c['event_id'], c['resource']
        if pair in actual or pair not in expected:
            raise BundleRejected('Duplicate or unexpected coverage')
        actual.add(pair)
        checked = timestamp(c['checked_at'])
        if checked > end:
            raise BundleRejected('Future coverage')
        status = c['status']
        if status == 'confirmed_absence':
            if pair in prior_data:
                raise BundleRejected('New absence contradicts previous data: importer cannot resolve it')
            if c['rule'] not in ABSENCE_RULES:
                raise BundleRejected('Endpoint absence rule not validated')
        elif status == 'not_applicable':
            original = events.get(pair[0],calendar[pair[0]])
            phase = phase_of(classify_status(original).value)
            allowed = pair[1] in SECTIONS and c['rule'] == 'pre-or-live-v1' and phase in ('pre','live')
            allowed |= pair[1] == 'event' and pair[0] not in events and c['rule'] == 'calendar-only-pre-v1' and phase == 'pre'
            if not allowed or c['evidence'] is not None:
                raise BundleRejected('Not-applicable assertion not proven')
        elif status == 'data':
            if c['rule'] is not None or set(c['evidence']) != {'file','line'}:
                raise BundleRejected('Data has no exact evidence reference')
            name,line = c['evidence']['file'],c['evidence']['line']
            if name not in rows or type(line) is not int or not 1 <= line <= len(rows[name]):
                raise BundleRejected('Invalid evidence reference')
            row = rows[name][line-1]
            if pair[1] == 'calendar':
                if name != 'calendar.jsonl' or row['id'] != pair[0] or checked < timestamp(row['quality']['observed_at_utc']):
                    raise BundleRejected('Calendar lacks observed provenance')
            else:
                if name == 'calendar.jsonl' or (row['event_id'],row['key']) != pair or checked < timestamp(row['fetched_at']):
                    raise BundleRejected('Raw evidence mismatch')
                if pair[1] in SECTIONS and body_state(pair[1],row['payload']) != 'data':
                    raise BundleRejected('Empty payload cannot be labelled data')
        else:
            raise BundleRejected('Pending, failed or unknown coverage cannot publish')
    if actual != expected:
        raise BundleRejected('Incomplete resource coverage')
    return m
