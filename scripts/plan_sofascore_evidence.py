"""Read-only offline audit of existing exports; never opens SQLite or the scraper Store."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def audit(import_manifest, exports_dir, frontend_source, teams_json):
    manifest_bytes = Path(import_manifest).read_bytes()
    imported = json.loads(manifest_bytes)
    front_bytes=Path(frontend_source).read_bytes()
    block=front_bytes.decode('utf8').split('const SOURCE_TEAM_IDS:',1)[1].split('};',1)[0]
    frontend_ids={int(external): public for public,external in re.findall(r'"([^"]+)":\s*(\d+)',block)}
    team_bytes=Path(teams_json).read_bytes()
    imported_names={team['team_id']:team['name'] for team in json.loads(team_bytes)}
    latest = {}
    for entry in imported['exports']:
        if entry.get('followed_team_id') is not None:
            latest[entry['followed_team_id']] = entry
    if len(latest) != 20:
        raise ValueError('Expected exactly 20 own exports')
    if set(frontend_ids) != set(latest):
        raise ValueError('Frontend correspondences differ from own exports')
    teams, all_events, sizes, filtered_sizes = [], {}, [], []
    own_members = set(latest)
    for team_id, entry in sorted(latest.items()):
        path = Path(exports_dir) / entry['file']
        raw = path.read_bytes()
        if digest(raw) != entry['sha256']:
            raise ValueError('Export hash mismatch: ' + path.name)
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        scoped = []
        for row in rows:
            if row['key'] != 'event':
                continue
            event = row['payload']
            if (event.get('tournament', {}).get('id'), event.get('season', {}).get('id'),
                event.get('tournament', {}).get('uniqueTournament', {}).get('id')) != (94191, 97382, 17073):
                continue
            participants = [event['homeTeam'], event['awayTeam']]
            if team_id not in {p['id'] for p in participants}:
                raise ValueError('Own export contains scoped event unrelated to its owner')
            if not {p['id'] for p in participants} <= own_members:
                raise ValueError('Scoped opponent outside the 20 own teams')
            scoped.append(row)
            all_events[row['event_id']] = event
        names = {p['name'] for row in scoped for p in (row['payload']['homeTeam'], row['payload']['awayTeam']) if p['id'] == team_id}
        if len(names) != 1 or not scoped:
            raise ValueError('Ambiguous team correspondence or no scoped witness')
        if imported_names.get(team_id) != next(iter(names)):
            raise ValueError('Imported team identity/name mismatch')
        event_ids = {r['event_id'] for r in scoped}
        selected = [r for r in rows if r['event_id'] in event_ids and r['key'] in ('event', 'lineups', 'statistics', 'incidents')]
        selected_bytes = sum(len(json.dumps(r, ensure_ascii=False, separators=(',', ':')).encode()) + 1 for r in selected)
        sizes.append(len(raw)); filtered_sizes.append(selected_bytes)
        witness = min(scoped, key=lambda r: r['event_id'])
        teams.append(dict(provider='sofascore', external_id=team_id, public_id=frontend_ids[team_id], name=next(iter(names)),
                          own_export=dict(file=path.name, sha256=digest(raw), bytes=len(raw)),
                          scope_witness=dict(event_id=witness['event_id'], fetched_at=witness['fetched_at']),
                          scoped_events=len(scoped), recipe_export_bytes=selected_bytes))
    listings = [e for e in imported['exports'] if e.get('kind') == 'listing']
    calendars = []
    for e in listings:
        raw = (Path(exports_dir) / e['file']).read_bytes()
        if digest(raw) != e['sha256']:
            raise ValueError('Calendar hash mismatch')
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        for r in rows:
            if (r['tournament_id'], r['stage']['id'], r['season_id']) != (17073, 94191, 97382):
                raise ValueError('Calendar scope mismatch')
            if {r['participants'][side]['id'] for side in ('home', 'away')} - own_members:
                raise ValueError('Calendar participant outside scope')
        calendars.append(dict(file=e['file'], sha256=digest(raw), bytes=len(raw), rows=len(rows),
                              missing_observed_at=sum(not r.get('quality', {}).get('observed_at_utc') for r in rows)))
    # Fixed historical pilot with both owners and all three original sections verified.
    pilot_id = 16599363
    event = all_events[pilot_id]
    pilot_files = []
    for participant in (event['homeTeam'], event['awayTeam']):
        entry = latest[participant['id']]
        rows = [json.loads(l) for l in (Path(exports_dir)/entry['file']).read_bytes().splitlines() if l.strip()]
        keys = {r['key'] for r in rows if r['event_id'] == pilot_id}
        if not {'event','lineups','statistics','incidents'} <= keys or event['status']['type'] != 'finished':
            raise ValueError('Pilot not verified')
        pilot_files.append(dict(team_external_id=participant['id'], file=entry['file'], sha256=entry['sha256']))
    return dict(schema=1, scope=dict(unique_tournament_id=17073, tournament_id=94191, season_id=97382),
                scraper_revision='611e436a1528b0f487aa56a045f023e8ae8ae098',
                evidence_manifest_sha256=digest(manifest_bytes), frontend_correspondence_sha256=digest(front_bytes),
                imported_teams_sha256=digest(team_bytes), teams=teams, calendars=calendars,
                known_event_ids=sorted(all_events),
                measurements=dict(own_exports=20, original_total_bytes=sum(sizes), original_max_file_bytes=max(sizes),
                                  recipe_total_bytes=sum(filtered_sizes), recipe_max_file_bytes=max(filtered_sizes),
                                  unique_scoped_events=len(all_events)),
                pilot=dict(event_id=pilot_id, kickoff=event['startTimestamp'], round=event['roundInfo']['round'],
                           status=event['status']['type'], home=event['homeTeam']['id'], away=event['awayTeam']['id'],
                           witnesses=pilot_files))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--import-manifest',type=Path,required=True)
    p.add_argument('--exports-dir',type=Path,required=True)
    p.add_argument('--frontend-source',type=Path,required=True)
    p.add_argument('--teams-json',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    value=audit(args.import_manifest,args.exports_dir,args.frontend_source,args.teams_json)
    with args.output.open('x',encoding='utf8',newline='\n') as out:
        json.dump(value,out,ensure_ascii=False,indent=2);out.write('\n')
    print(json.dumps(value['measurements']))

if __name__=='__main__': main()
