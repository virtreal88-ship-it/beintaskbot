"""Preview or apply a company-owner-authorized default-deny transition.

Preview prints aggregate counts and digest only. Apply requires that exact digest.
"""
import argparse
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tenant_employee_defaults_store import transition

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tenant-id', required=True)
    parser.add_argument('--owner-id', required=True, type=int)
    parser.add_argument('--apply-digest')
    args = parser.parse_args()
    print(json.dumps(transition(tenant_id=args.tenant_id, owner_id=args.owner_id,
                               expected_digest=args.apply_digest), sort_keys=True))
