"""Test-image-only provider; no network clients and no real recipes."""
import hashlib
import json
from pathlib import Path
import time

class Provider:
    revision=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    def collect(self,recipe,checkpoint):
        for i in range(100 if recipe=='fixture-slow-v1' else 4):
            time.sleep(.1 if recipe=='fixture-slow-v1' else .03)
            checkpoint(min(80,i+2))
        if recipe=='fixture-fail-v1':raise RuntimeError('Controlled provider failure')
        event={'id':777001,'tournament':{'id':94191,'uniqueTournament':{'id':17073},'name':'Primera Federacion, Group 2'},
            'season':{'id':97382,'year':'26/27'},'homeTeam':{'id':2841,'name':'Equipo 2841'},'awayTeam':{'id':9001,'name':'Equipo 9001'},
            'status':{'type':'finished'},'homeScore':{'current':4},'awayScore':{'current':1},'roundInfo':{'round':9},'startTimestamp':1790000000}
        raw=(json.dumps({'event_id':777001,'key':'event','sub':'','fetched_at':'2026-10-28T12:00:00+00:00','payload':event})+'\n').encode()
        partial=recipe=='fixture-partial-v1'
        return {'raw':raw,'coverage':'partial' if partial else 'complete','errors':[{'code':'fixture_incomplete'}] if partial else []}
