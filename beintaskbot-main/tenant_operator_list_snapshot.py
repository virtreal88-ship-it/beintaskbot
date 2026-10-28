"""Request-local read enrichment for already-authorized operator list routes.

No cross-request cache and no mutation/authority shortcut: individual cards and
assignment writes continue to reauthorize through their existing paths.
"""
from tenant_ai_group_coordination import choose, group_jid


class ListSnapshot:
    def __init__(self, cur, tenant, rows):
        self.cur, self.tenant = cur, tenant
        self.agents, self.groups, self.channels, self.direct = {}, {}, {}, {}
        connect = [row for row in rows if row['provider'] == 'connect']
        if not connect:
            return
        cur.execute('''SELECT id,name,mode,enabled,scopes,schedule,role FROM saas_ai_agents
          WHERE tenant_id=%s::uuid ORDER BY name,id''', (tenant,))
        self.agents = {str(row['id']): dict(row) for row in cur.fetchall()}
        groups = [row['conversation_id'] for row in connect if group_jid(row['conversation_id'])]
        if groups:
            cur.execute('''SELECT g.*,m.display_name AS assignee_name FROM saas_ai_group_coordination g
              LEFT JOIN saas_tenant_members m ON m.tenant_id=g.tenant_id AND m.telegram_id=g.assigned_to
              WHERE g.tenant_id=%s::uuid AND g.provider='connect' AND g.group_jid=ANY(%s::text[])''', (tenant, groups))
            self.groups = {row['group_jid']: dict(row) for row in cur.fetchall()}
            cur.execute('''SELECT c.conversation_id,c.connect_channel_id::text AS channel_id,c.lead_id,
                ch.name AS channel_name,ch.active,cs.state,
                EXISTS(SELECT 1 FROM saas_ai_conversation_state legacy WHERE legacy.tenant_id=c.tenant_id
                  AND legacy.provider='connect' AND legacy.channel_id='' AND legacy.conversation_id=c.conversation_id) AS legacy
              FROM saas_connect_conversations c JOIN saas_connect_channels ch
                ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=c.tenant_id AND cs.provider='connect'
                AND cs.channel_id=c.connect_channel_id::text AND cs.conversation_id=c.conversation_id
              WHERE c.tenant_id=%s::uuid AND c.conversation_id=ANY(%s::text[]) ORDER BY ch.name,c.connect_channel_id''', (tenant, groups))
            for row in cur.fetchall():
                self.channels.setdefault(row['conversation_id'], []).append(dict(row))
        direct = [row for row in connect if not group_jid(row['conversation_id']) and not row.get('agent_id')
                  and row.get('unambiguous') and row.get('conversation_id') and row.get('channel_active')]
        if direct:
            cur.execute('''WITH routes(channel_id,conversation_id) AS (SELECT * FROM unnest(%s::text[],%s::text[]))
              SELECT r.channel_id,r.conversation_id,cs.state,cs.reason,cs.revision,cs.agent_id,
                EXISTS(SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=%s::uuid AND j.provider='connect'
                  AND j.channel_id=r.channel_id AND j.conversation_id=r.conversation_id
                  AND j.state IN ('queued','processing','unknown')) AS has_job,
                (EXISTS(SELECT 1 FROM saas_connect_identity_conflicts f WHERE f.tenant_id=%s::uuid
                  AND f.connect_channel_id=r.channel_id::uuid
                  AND (f.conversation_id=r.conversation_id OR EXISTS(SELECT 1 FROM saas_connect_direct_identities a
                    WHERE a.tenant_id=f.tenant_id AND a.connect_channel_id=f.connect_channel_id
                      AND a.identity_key=f.conversation_id AND a.canonical_conversation_id=r.conversation_id)))
                OR EXISTS(SELECT 1 FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid AND m.channel='connect:'||r.channel_id
                  AND m.raw->>'conversationIdentityConflict'='true'
                  AND (m.raw->>'conversationId'=r.conversation_id OR m.raw->'directIdentity'->>'lid'=r.conversation_id
                    OR m.raw->'directIdentity'->>'jid'=r.conversation_id))
                OR EXISTS(SELECT 1 FROM saas_connect_dialog_links l WHERE l.tenant_id=%s::uuid
                  AND l.channel_id=r.channel_id::uuid AND l.active
                  AND l.snapshot->>'identity_source'='owner_assertion'
                  AND (l.canonical_id=r.conversation_id OR l.source_id=r.conversation_id))) AS identity_conflict
              FROM routes r LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=%s::uuid AND cs.provider='connect'
                AND cs.channel_id=r.channel_id AND cs.conversation_id=r.conversation_id''',
              ([row['channel_id'] for row in direct], [row['conversation_id'] for row in direct], tenant, tenant, tenant, tenant, tenant))
            self.direct = {(row['channel_id'], row['conversation_id']): dict(row) for row in cur.fetchall()}
            from tenant_connect_identity import conflicted
            for (channel,conversation),state in self.direct.items():
                if state.get('identity_conflict'):
                    state['identity_conflict']=conflicted(cur,tenant,channel,conversation)

    @property
    def primary_agents(self):
        return [agent for agent in self.agents.values() if agent['role'] == 'primary']

    def group_view(self, conversation):
        state = self.groups.get(conversation, {})
        channels = self.channels.get(conversation, [])
        winner = choose(state.get('assignments'), self.agents) if state and not any(row['legacy'] for row in channels) else None
        selected = next((row for row in channels if winner and row['channel_id'] == winner['channel_id'] and row['active']), None)
        if not selected:
            winner = None
        if winner and state.get('state') == 'active' and selected['state'] in ('human', 'paused'):
            state = {**state, 'state': selected['state'], 'reason': 'group_channel_requires_resume'}
        return {'group_jid': conversation, 'state': state.get('state', 'paused'),
                'reason': state.get('reason') or ('' if winner else 'group_assignment_required_or_ambiguous'),
                'revision': int(state.get('revision', 0)), 'assignments': state.get('assignments', []),
                'assigned_to': state.get('assigned_to'),
                'channels': [{key: row[key] for key in ('channel_id', 'lead_id', 'channel_name')} for row in channels],
                'primary_agent_id': winner['agent_id'] if winner else None,
                'sending_channel_id': winner['channel_id'] if winner else None}

    def group_options(self, row):
        from tenant_operator_assignment import group_options_in_transaction
        gate = {'status': row.get('tenant_status'), 'enabled': row.get('company_enabled'),
                'emergency_stop': row.get('company_emergency_stop'), 'active': row.get('channel_active'),
                'connected': row.get('connected'), 'unambiguous': row.get('unambiguous')}
        return group_options_in_transaction(self.cur, self.tenant, row['channel_id'], row['conversation_id'],
            snapshot={'state': self.groups.get(row['conversation_id'], {}), 'gate': gate, 'agents': self.primary_agents})

    def direct_options(self, row):
        from tenant_operator_assignment import candidates
        state = self.direct.get((row['channel_id'], row['conversation_id']), {})
        config = {**row, 'identity_conflict': state.get('identity_conflict', False)}
        agents, reason = candidates(self.cur, self.tenant, row['channel_id'], row['conversation_id'], row,
            state, state.get('has_job', False), config, agent_rows=self.primary_agents)
        return {'allowed': bool(agents), 'reason': reason, 'agents': agents,
                'current_agent_id': str(state['agent_id']) if state.get('agent_id') else None,
                'can_manage': not state.get('has_job', False), 'revision': int(state.get('revision') or 0),
                'state': state.get('state') or 'unmanaged'}
