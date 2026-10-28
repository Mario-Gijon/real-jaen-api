"""Verify the reviewable userns diagnostic bundle; never apply policies."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent / 'policies'
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
for name, expected in manifest['files'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
base = json.loads((root / 'docker29.5.2-cap0-amd64.baseline.json').read_text())
probe = json.loads((root / 'docker29.5.2-cap0-amd64.unshare-user.probe.json').read_text())
delta = json.loads((root / 'unshare-user.delta.json').read_text())
expected_rule = {'names': ['unshare'], 'action': 'SCMP_ACT_ALLOW',
                 'args': [{'index': 0, 'value': 0x10000000, 'op': 'SCMP_CMP_EQ'}]}
assert delta == {'operation': 'append', 'path': '/syscalls/-', 'value': expected_rule}
assert probe['syscalls'] == base['syscalls'] + [expected_rule]
assert {k:v for k,v in probe.items() if k != 'syscalls'} == {k:v for k,v in base.items() if k != 'syscalls'}
result = json.loads((root / 'unshare-user.results.json').read_text())
assert result['user_namespace_probes']['user']['ok'] is True
assert result['user_namespace_probes']['no_flags']['errno'] == 1
assert result['user_namespace_probes']['fs_only']['errno'] == 1
assert result['browser']['ok'] is False and result['browser']['local_requests'] == []
assert result['stopped_before_any_permission_expansion'] is True
print('Review bundle valid: one exact unshare(CLONE_NEWUSER) allowance, no policy applied by this check')

next_manifest = root / 'clone-user-sigchld.next.manifest.json'
if next_manifest.exists():
    additional = json.loads(next_manifest.read_text())
    assert additional['status'] == 'PROPOSED_NOT_APPLIED'
    assert additional['source_profile_sha256'] == hashlib.sha256((root / 'docker29.5.2-cap0-amd64.unshare-user.probe.json').read_bytes()).hexdigest()
    for name, expected in additional['files'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    next_probe = json.loads((root / 'docker29.5.2-cap0-amd64.clone-user-sigchld.next.proposed.json').read_text())
    clone_rule = {'names': ['clone'], 'action': 'SCMP_ACT_ALLOW',
                  'args': [{'index': 0, 'value': 0x10000011, 'op': 'SCMP_CMP_EQ'}]}
    assert next_probe['syscalls'] == probe['syscalls'] + [clone_rule]
    assert {k:v for k,v in next_probe.items() if k != 'syscalls'} == {k:v for k,v in probe.items() if k != 'syscalls'}
    trace = json.loads((root / 'clone-user-sigchld.trace-results.json').read_text())
    assert trace['observed_blocking_call']['args'][0] == 0x10000011
    assert trace['observed_blocking_call']['errno'] == 1 and trace['proposal_applied'] is False
    print('Next clone delta verified, PROPOSED_NOT_APPLIED; trace matches the exact observed flags')

# The next.* manifest is historical; validate the later approved experiment separately.
approved_result = root / 'clone-user-sigchld.approved.results.json'
if approved_result.exists():
    approved = json.loads(approved_result.read_text(encoding='utf-8'))
    profile = root / approved['profile']
    assert hashlib.sha256(profile.read_bytes()).hexdigest() == approved['profile_sha256']
    actual = json.loads(profile.read_text(encoding='utf-8'))
    initial = json.loads((root / 'docker29.5.2-cap0-amd64.baseline.json').read_text(encoding='utf-8'))
    assert actual['syscalls'][:-2] == initial['syscalls']
    assert actual['syscalls'][-2:] == [
        {'names': ['unshare'], 'action': 'SCMP_ACT_ALLOW', 'args': [{'index': 0, 'value': 268435456, 'op': 'SCMP_CMP_EQ'}]},
        {'names': ['clone'], 'action': 'SCMP_ACT_ALLOW', 'args': [{'index': 0, 'value': 268435473, 'op': 'SCMP_CMP_EQ'}]},
    ]
    assert {k:v for k,v in actual.items() if k != 'syscalls'} == {k:v for k,v in initial.items() if k != 'syscalls'}
    assert approved['status'] == 'APPROVED_DIAGNOSTIC_APPLIED_FAILED_STOPPED'
    assert approved['additional_exceptions_applied'] is False
    print('Later approved clone experiment: exact two-rule profile verified; failed at next clone, stopped')

# Later five-rule experiment: semantic equality to the entire reviewed baseline.
full_result = root / 'full-candidate.results.json'
if full_result.exists():
    full = json.loads(full_result.read_text(encoding='utf-8'))
    full_path = root / full['profile']
    assert hashlib.sha256(full_path.read_bytes()).hexdigest() == full['profile_sha256']
    initial = json.loads((root / 'docker29.5.2-cap0-amd64.baseline.json').read_text())
    rules = [{'names': [name], 'action': 'SCMP_ACT_ALLOW', 'args':
              ([] if value is None else [{'index': 0, 'value': value, 'op': 'SCMP_CMP_EQ'}])}
             for name, value in [('unshare', 0x10000000), ('clone', 0x10000011),
                                 ('clone', 0x70000011), ('clone', 0x20000011), ('chroot', None)]]
    assert json.loads(full_path.read_text()) == initial | {'syscalls': initial['syscalls'] + rules}
    assert json.loads((root / full['delta']).read_text()) == [
        {'operation': 'append', 'path': '/syscalls/-', 'value': rule} for rule in rules]
    assert full['additional_permissions'] is False
    assert full['status'] == 'APPROVED_FIVE_RULE_PROFILE_SANDBOX_PASSED_RECOVERY_FAILED_STOPPED'
    assert [p['ok'] for p in full['phases']] == [True, True, True, True, False]
    print('Five-rule profile verified; sandbox passed, recovery failed and test stopped')

recovery_result = root / 'profile-recovery.results.json'
if recovery_result.exists():
    recovery = json.loads(recovery_result.read_text(encoding='utf-8'))
    assert recovery['status'] == 'DIAGNOSTIC_PROFILE_RECOVERY_PASSED_NOT_INTEGRATED'
    assert recovery['seccomp_sha256'] == hashlib.sha256((root / 'docker29.5.2-cap0-amd64.full-candidate.approved-probe.json').read_bytes()).hexdigest()
    assert recovery['additional_permissions'] is False and recovery['network'] == 'none'
    assert recovery['scraper_checkout_modified'] is False
    assert len(recovery['tests']) == 8 and all(p['ok'] for p in recovery['tests'])
    assert recovery['concurrent_contenders_rejected'] == 2
    assert recovery['injected_failure_source_and_backup_unchanged'] is True
    print('Later recovery adapter: eight scenarios passed, unchanged sandbox policy, no integration')
