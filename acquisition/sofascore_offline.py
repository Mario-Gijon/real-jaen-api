"""Offline-only recipe adapter. No HTTP Client construction, daemon or production activation."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from .browser import managed_browser, require_offline
from .profile import atomic_json

SECTIONS = ('lineups', 'statistics', 'incidents')
REVISION = '611e436a1528b0f487aa56a045f023e8ae8ae098'


def scope_manifest():
    return json.loads(Path(__file__).with_name('sofascore-scope.json').read_text(encoding='utf8'))


def scoped(row):
    m = scope_manifest()
    ids = {t['external_id'] for t in m['teams']}
    return (row.sport == 'football' and
            (row.tournament_id, row.stage_id, row.season_id) == (17073, 94191, 97382) and
            {row.home_id, row.away_id} <= ids and row.home_id != row.away_id)


def epoch(value):
    return value.timestamp() if value is not None else None


@dataclass(frozen=True)
class Policy:
    window_s: int = 72 * 3600
    interval_s: int = 6 * 3600

    def __post_init__(self):
        if type(self.window_s) is not int or type(self.interval_s) is not int or min(self.window_s, self.interval_s) <= 0:
            raise ValueError('Invalid recipe policy')

    def due(self, state, final_seen, now):
        # Phase and expected sections belong to the scraper, not this adapter.
        from sofascore_scraper.services.planning import expected_slice_keys, phase_of
        row = state.event
        if phase_of(row.status_class) != 'post':
            return ()
        if row.start_ts is None or row.start_ts > now or (final_seen is not None and final_seen > now):
            raise ValueError('Invalid or future observation')
        keys = expected_slice_keys(row.sport, SECTIONS, phase='post', row=row)
        if final_seen is None:
            return tuple(keys)  # No proven post-final baseline. Never infer it from empty counters.
        close = max(row.start_ts + self.window_s, final_seen + self.interval_s)
        due = []
        for key in keys:
            info = state.slice(key)
            fetched, checked = epoch(info.fetched_at), epoch(info.checked_at)
            if any(v is not None and v > now for v in (fetched, checked)):
                raise ValueError('Future section timestamp')
            valid = info.state == 'ok' and not getattr(info, 'error', None) and info.has_payload and fetched is not None and fetched >= final_seen
            # Valid refresh cadence and failed-attempt backoff are independent.
            previous = max((v for v in (checked, fetched) if v is not None and v >= final_seen), default=None)
            error=getattr(info,'error',None)
            reason=getattr(error,'reason',None)
            if reason=='403':
                continue  # Administrative recovery, never automatic polling.
            delay=(min(900,30*2**min(max(0,getattr(error,'count',1)-1),5)) if reason in ('timeout','network','5xx') else 900) if not valid else self.interval_s
            if previous is not None and now < previous + delay:
                continue
            if not valid or fetched < close:
                due.append(key)
        return tuple(due)


class FixtureTransport:
    """Only literal fixture outcomes; no network library or real-client escape hatch."""
    def __init__(self, responses, *, max_calls=500, after_call=None):
        require_offline()
        self.responses = dict(responses)
        self.max_calls, self.after_call = max_calls, after_call
        self.calls = []
        self.total_calls = 0
        self.memo = {}
        self.closed = False
        self.allowed = set()
        self.cancelled = lambda: False

    async def __aenter__(self):
        self.closed = False
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def get(self, path, retries=None):
        if self.cancelled():
            from sofascore_scraper.slices import Outcome
            return Outcome('skipped', reason='cancelled')
        if path not in self.allowed:
            raise ValueError('Unplanned fixture path')
        if path in self.memo:
            return self.memo[path]
        if self.total_calls >= self.max_calls:
            raise RuntimeError('Offline request budget exhausted')
        self.calls.append(path)
        self.total_calls += 1
        if self.after_call:
            self.after_call(path)
        await asyncio.sleep(0)
        answer = self.responses[path]
        if isinstance(answer, BaseException):
            raise answer
        if path.count('/') == 2 and path.startswith('/event/') and answer.status == 'ok':
            event = (answer.data or {}).get('event', {})
            ids = {t['external_id'] for t in scope_manifest()['teams']}
            if (event.get('id') != int(path.split('/')[-1]) or
                (event.get('tournament',{}).get('id'), event.get('season',{}).get('id'),
                 event.get('tournament',{}).get('uniqueTournament',{}).get('id')) != (94191,97382,17073) or
                not {event.get('homeTeam',{}).get('id'),event.get('awayTeam',{}).get('id')} <= ids):
                raise ValueError('Response outside fixed scope')
        self.memo[path] = answer
        return answer


class OfflineRecipe:
    """One offline run with injected isolated Store and durable post-final checkpoints.

    No service endpoint accepts this adapter. The caller owns an exclusive cache lease.
    The checkpoint path and Store must both belong to the caller's isolated installation.
    """
    def __init__(self, store, transport, checkpoint_path, *, policy=Policy(), baseline=None, inventory_ids=(), historical_ids=()):
        require_offline()
        if type(transport) is not FixtureTransport:
            raise TypeError('Only explicitly simulated transport is permitted')
        self.store, self.transport, self.path = store, transport, Path(checkpoint_path)
        self.policy = policy
        self.inventory_ids=set(inventory_ids)-set(historical_ids)
        self.historical_ids=set(historical_ids)
        self.final_seen = json.loads(self.path.read_text()) if self.path.exists() else dict(baseline or {})

    def _states(self):
        from sofascore_scraper.store import Scope
        return {s.event.id: s for s in self.store.events.states(Scope(
            tournament_ids=(17073,), season_ids=(97382,))) if scoped(s.event) and s.event.id not in self.historical_ids}

    def plan(self, states, now):
        from sofascore_scraper.services.planning import WorkItem, phase_of
        from sofascore_scraper.store import Ref
        unique = {}
        for state in states:
            if state.event.id in self.historical_ids:
                continue
            if not scoped(state.event):
                raise ValueError('Event outside fixed scope')
            existing = unique.setdefault(state.event.id, state)
            if existing != state:
                raise ValueError('Conflicting duplicate event evidence')
        items = []
        for ident, state in sorted(unique.items()):
            due = self.policy.due(state, self.final_seen.get(str(ident)), now)
            post = phase_of(state.event.status_class) == 'post'
            event_due = (not post and (state.event.start_ts is None or state.event.start_ts <= now))
            if due or event_due or state.event.stale:
                keys = due if post else ()
                if post and state.event.stale:
                    keys = SECTIONS  # Explicitly changed listing: directed invalidation, no historical scan.
                items.append(WorkItem(Ref.event(ident), 'refill', tuple((k, '') for k in keys),
                                      'football', 'fixed recipe: post-final/section interval'))
        return tuple(items)

    def _checkpoint(self, result):
        from sofascore_scraper.services.planning import phase_of
        from sofascore_scraper.status import classify_status
        if result.ok and result.payload and result.put is not None and phase_of(classify_status(result.payload).value) == 'post':
            at = epoch(result.event.fetched_at)
            if at is None:
                raise ValueError('Outcome has no provenance timestamp')
            self.final_seen.setdefault(str(result.event_id), at)
            atomic_json(self.path, self.final_seen)

    def run(self, states, *, now, cancelled=lambda: False):
        from sofascore_scraper.client import endpoints
        from sofascore_scraper.services.pipeline import FetchPipeline
        from sofascore_scraper.services.planning import phase_of
        require_offline()
        self.transport.memo.clear()
        self.transport.calls.clear()
        self.transport.cancelled = cancelled
        items = self.plan(states, now)
        ids = {item.owner.id for item in items}
        self.transport.allowed = {p for ident in ids for p in
                                  [endpoints.event(ident), *(endpoints.event_slice(k, ident) for k in SECTIONS)]}
        summaries = []
        attempted = set()
        errors = []
        def record(result):
            attempted.update((result.event_id, k) for k in result.slices)
            self._checkpoint(result)
            if result.event and result.event.status != 'ok':
                errors.append((result.event_id, 'event', result.event.reason))
            for key, outcome in result.slices.items():
                if outcome.status != 'ok':
                    errors.append((result.event_id, key, outcome.reason or 'absence_rule_pending'))
            if not result.ok:
                errors.append((result.event_id, 'event', result.reason))
        pipeline = FetchPipeline(self.store, client=self.transport, concurrency=1, selection=SECTIONS)
        summaries.append(pipeline.run_sync(items, cancelled=cancelled, on_result=record))
        # A pre/live event may just have finished. Replan without a second transport GET /event.
        follow_up = []
        from sofascore_scraper.services.planning import WorkItem
        from sofascore_scraper.store import Ref
        for ident, state in self._states().items():
            if ident not in ids or phase_of(state.event.status_class) != 'post':
                continue
            due = self.policy.due(state, self.final_seen.get(str(ident)), now)
            remaining = tuple(k for k in due if (ident, k) not in attempted)
            if remaining:
                follow_up.append(WorkItem(Ref.event(ident), 'refill', tuple((k, '') for k in remaining), 'football', 'newly finished'))
        if follow_up and not cancelled():
            summaries.append(pipeline.run_sync(follow_up, cancelled=cancelled, on_result=record))
        coverage = []
        for ident, state in self._states().items():
            phase = phase_of(state.event.status_class)
            for key in SECTIONS:
                info = state.slice(key)
                fetched = epoch(info.fetched_at)
                final = self.final_seen.get(str(ident))
                failure = any(e[0:2] == (ident, key) for e in errors)
                if phase in ('pre', 'live'):
                    status = 'not_applicable'
                elif failure:
                    status = 'failed' if info.state == 'error' else 'pending'
                elif info.state == 'ok' and not getattr(info, 'error', None) and info.has_payload and final is not None and fetched is not None and fetched >= final:
                    status = 'data'
                else:
                    status = 'pending'
                coverage.append(dict(event_id=ident, resource=key, status=status, fetched_at=fetched))
        complete = (not cancelled() and not errors and all(not s.cancelled and not s.failed and not s.skipped for s in summaries)
                    and all(c['status'] in ('data', 'not_applicable') for c in coverage))
        return dict(details_complete=complete, complete=False, cancelled=cancelled() or any(s.cancelled for s in summaries),
                    coverage=coverage, errors=errors, calls=list(self.transport.calls), summaries=summaries)

    def collect(self, *, now, cancelled=lambda: False):
        """Calendar + details under the scraper's writer lease, using fixture transport only."""
        from sofascore_scraper.client import endpoints
        from sofascore_scraper.services.listing import ListingFetcher
        from sofascore_scraper.services.pipeline import FetchPipeline
        from sofascore_scraper.services.planning import schedule_item
        from sofascore_scraper.store import Scope
        require_offline()
        self.transport.cancelled = cancelled
        with self.store.lease('writer', purpose='offline-sofascore-recipe'):
            self.transport.memo.clear()
            self.transport.calls.clear()
            self.transport.allowed = {endpoints.rounds(17073,97382)}
            self.transport.allowed.update(endpoints.round_events(17073,97382,i) for i in range(1,51))
            self.transport.allowed.update(endpoints.season_events_page(17073,97382,kind,i)
                                          for kind in ('last','next') for i in range(200))
            proof = self.final_seen.get('_calendar', {})
            previous_ids = set(proof.get('event_ids', ()))
            trusted = proof.get('valid') is True and self.inventory_ids <= previous_ids
            self.final_seen['_calendar'] = {'valid':False}
            atomic_json(self.path,self.final_seen)  # A crash/failure must not turn a partial listing into a fresh success.
            handler = ListingFetcher(self.store,only_finished=False,concurrency=1,enqueue_events=False,
                                     schedule_max_age=900 if trusted else None,clock=lambda:now)
            listing = FetchPipeline(self.store,client=self.transport,concurrency=1,selection=SECTIONS,listing=handler).run_sync(
                [schedule_item(17073,97382)],cancelled=cancelled)
            calls = list(self.transport.calls)
            good = not listing.cancelled and all(r.ok or (r.reason=='fresh' and r.listing and r.listing.listed) for r in listing.results)
            if not good or cancelled():
                return dict(complete=False,details_complete=False,cancelled=cancelled() or listing.cancelled,
                            coverage=[],errors=['calendar_incomplete'],calls=calls)
            states = list(self._states().values())
            listed_ids = set().union(*(set(r.listing.events) if r.reason != 'fresh' else previous_ids for r in listing.results))
            scoped_ids = {s.event.id for s in states} & listed_ids
            if not self.inventory_ids <= scoped_ids:
                return dict(complete=False,details_complete=False,cancelled=False,coverage=[],
                            errors=['calendar_baseline_incomplete'],calls=calls)
            self.final_seen['_calendar'] = {'valid':True,'event_ids':sorted(scoped_ids),'checked_at':now}
            atomic_json(self.path,self.final_seen)
            result = self.run(states,now=now,cancelled=cancelled)
            result['calls'] = calls + result['calls']
            members = {p for state in states for p in (state.event.home_id,state.event.away_id)}
            result['complete'] = (result['details_complete'] and members == {t['external_id'] for t in scope_manifest()['teams']}
                                  and self.inventory_ids <= {s.event.id for s in states})
            if not result['complete'] and result['details_complete']:
                result['errors'].append('calendar_does_not_cover_scope_baseline')
            return result

    @staticmethod
    def browser(owner):
        # Reuse the approved sandbox/profile wrapper unchanged. Still OS-level network:none.
        return managed_browser(owner)
