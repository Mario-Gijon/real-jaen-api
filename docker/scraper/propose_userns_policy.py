"""Generate (never apply) one narrow userns diagnostic allowance for review.

Use the resolved seccomp JSON from the exact disposable container being audited,
not an arbitrary download or another host's baseline. No Docker calls are made.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys

RULE = {'names': ['unshare'], 'action': 'SCMP_ACT_ALLOW',
        'args': [{'index': 0, 'value': 268435456, 'op': 'SCMP_CMP_EQ'}]}


def propose(source, target):
    source, target = Path(source), Path(target)
    if target.exists():
        raise FileExistsError('Never overwrite an existing policy')
    raw = source.read_bytes()
    baseline = json.loads(raw.decode('utf-8-sig'))
    if baseline.get('defaultAction') != 'SCMP_ACT_ERRNO' or baseline.get('defaultErrnoRet', 1) != 1:
        raise ValueError('Expected the inspected errno=EPERM deny-by-default baseline')
    if any('unshare' in rule['names'] for rule in baseline['syscalls']):
        raise ValueError('Baseline already has unshare rules; requires a separate review')
    proposed = copy.deepcopy(baseline)
    proposed['syscalls'].append(RULE)
    assert proposed['syscalls'][:-1] == baseline['syscalls']
    assert {k: v for k, v in proposed.items() if k != 'syscalls'} == {k: v for k, v in baseline.items() if k != 'syscalls'}
    target.write_text(json.dumps(proposed, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'applied': False, 'baseline_sha256': hashlib.sha256(raw).hexdigest(),
                      'proposal_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                      'only_added_rule': RULE, 'proposal': str(target)}))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: python propose_userns_policy.py BASELINE_JSON NEW_PROPOSAL_JSON')
    propose(*sys.argv[1:])
