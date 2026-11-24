"""Presentation of proved history aliases over already authorized list rows.

Never resolve a missing route, change assignments, or copy private company,
notes or counters. The selected card remains a physical row.
"""
from collections import Counter


def _key(row):
    if row.get('provider') != 'connect':
        return None
    return (row.get('channel_id'), row.get('conversation_id'), row.get('lead_id'))


def _mode(row):
    return tuple(bool(row.get(bit)) for bit in (
        'archive_read_only', 'owner_overview_read_only', 'search_overview_read_only'))


def _facets(routes, rows, *, actor):
    """Return separately navigable All routes, never inferred action rights."""
    facets, order = [], []
    for i,(key,row) in enumerate(zip(routes,rows)):
        status = row.get('lifecycle') or {}
        state = status.get('state')
        archive, passive, search = _mode(row)
        if state not in {'accepted','waiting','closed','dismissed'} or search:
            return None
        if state == 'accepted' and (archive or (not passive and str(status.get('operator_id')) != str(actor))):
            return None
        if state == 'waiting' and (archive or passive or status.get('operator_id') or not status.get('can_accept')):
            return None
        terminal=state in {'closed','dismissed'}
        if terminal and (passive or (not archive and str(status.get('operator_id')) != str(actor))):
            return None
        facets.append({'provider':'connect','channel_id':key[0],'conversation_id':key[1],
                       'lead_id':key[2],'state':state,'read_only':bool(terminal or passive)})
        rank=3 if terminal else 0 if state=='accepted' and not passive else 1 if state=='waiting' else 2
        order.append((rank,i))
    _,selected=min(order)
    return rows[selected], facets


def fold_authorized(rows, pairs, native_companions, *, actor, allow_mixed=False):
    native = set(native_companions) | {_key(row) for row in native_companions.values()}
    route_counts = Counter(_key(r) for r in rows)
    exact = {key: (index, row) for index, row in enumerate(rows)
             if (key := _key(row)) and route_counts[key] == 1 and key not in native}
    memberships = Counter((p['channel_id'], p[k+'_id']) for p in pairs for k in ('primary', 'alternate'))
    changes, removed = {}, set()
    for pair in pairs:
        routes = [(pair['channel_id'], pair[k+'_id'], pair[k+'_lead']) for k in ('primary','alternate')]
        if not all(key in exact and memberships[key[:2]] == 1 for key in routes):
            continue
        (pi, primary), (ai, alternate) = (exact[key] for key in routes)
        p_state, a_state = (r.get('lifecycle') or {} for r in (primary, alternate))
        display={'history_merge_display_'+field:pair['display_'+field] for field in ('name','phone')
                 if pair.get('display_'+field)}
        if allow_mixed and (p_state.get('state') != a_state.get('state') or _mode(primary)!=_mode(alternate)):
            mixed = _facets(routes,(primary,alternate),actor=actor)
            if mixed is not None:
                selected, facets = mixed
                changes[min(pi, ai)] = {**selected, **display, 'history_merge_id':pair['id'],
                                        'history_merge_certificate':pair['id'],
                                        'history_merge_read_routes':facets}
                removed.add(max(pi, ai))
            continue
        mode = _mode(primary)
        if mode != _mode(alternate):
            continue  # never switch a writable route to a passive route
        p_owner, a_owner = (state.get('operator_id') for state in (p_state, a_state))
        if not any(mode) and p_owner and a_owner and str(p_owner) != str(a_owner):
            continue  # keep independent operators' simultaneously active work separate
        p_accepted, a_accepted = (state.get('state') == 'accepted' for state in (p_state, a_state))
        if not any(mode):
            if (p_accepted and str(p_owner) != str(actor)) or (a_accepted and str(a_owner) != str(actor)):
                continue
        # Do not conceal a distinct actionable state or role-specific right.
        # Keep it a separate route until an explicit action-selector can
        # represent differing workflow states without changing their meaning.
        if p_state.get('state') != a_state.get('state'):
            continue
        if not p_accepted and any(bool(p_state.get(bit)) != bool(a_state.get(bit))
                                  for bit in ('can_accept','can_start','can_close','can_transfer')):
            continue
        selected = primary
        card = {**selected, **display, 'history_merge_id': pair['id']}
        if (p_accepted and a_accepted and not any(mode)
                and str(p_owner) == str(a_owner) == str(actor)
                and p_state.get('can_close') and a_state.get('can_close')):
            card['history_merge_action_routes'] = [
                {'provider':'connect','channel_id':key[0],'conversation_id':key[1],'lead_id':key[2]}
                for key in routes]
        changes[min(pi, ai)] = card
        removed.add(max(pi, ai))
    return [changes.get(i, r) for i, r in enumerate(rows) if i not in removed]


def fold_completed(cur, tenant, policy, rows, native_companions, *, channel=None, allow_mixed=False):
    from tenant_dialog_history_merge_projection import _pairs, current_display, enabled
    if not enabled() or sum(_key(row) is not None for row in rows) < 2:
        return rows
    # Only inspect editable display metadata if both exact list rows are present.
    visible={key for row in rows if (key:=_key(row))}
    pairs=[current_display(cur,tenant,pair) for pair in _pairs(cur,tenant,policy,channel=channel,eligible_lifecycle=True)
           if all((pair['channel_id'],pair[k+'_id'],pair[k+'_lead']) in visible for k in ('primary','alternate'))]
    return fold_authorized(rows, pairs, native_companions, actor=policy.profile.get('telegram_id'), allow_mixed=allow_mixed)
