"""Transaction-local list capabilities; never authorizes detail reads or writes.

Batch the same group route barriers used by allowed_channels. A list must not
make twenty network round trips per group while holding every previous lock.
"""
import json
from tenant_linear_policy import TenantLinearError


class ListAccess:
    def __init__(self, cur, tenant, policy, rows, rollout):
        from tenant_dialog_access import require_source, sql_filter
        from tenant_dialog_lifecycle import visibility_sql
        self.update = {}
        self.groups = {}
        if policy.has_scoped_access('dialogs'):
            for provider, channel in sorted({(r['provider'], str(r['channel_id'])) for r in rows}):
                try:
                    require_source(cur, tenant, policy, provider, channel, 'update')
                    self.update[(provider, channel)] = True
                except TenantLinearError:
                    self.update[(provider, channel)] = False
        groups = sorted({r['group_coordination']['group_jid'] for r in rows if r.get('group_coordination')})
        if not groups:
            return
        if not rollout.enforced and policy.action_scope('dialogs', 'view') is None:
            return
        sql = '''SELECT c.connect_channel_id::text AS channel_id,c.conversation_id
          FROM saas_connect_conversations c JOIN saas_connect_channels ch
          ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
          WHERE c.tenant_id=%s::uuid AND c.conversation_id=ANY(%s::text[]) AND '''
        sql += sql_filter(policy, "'connect'", 'c.connect_channel_id::text')
        if not rollout.enforced:
            # Legacy scoped lists historically expose source-approved group
            # configuration even before a physical group route exists there.
            sql = '''SELECT ch.connect_channel_id::text AS channel_id,g.conversation_id
              FROM saas_connect_channels ch CROSS JOIN unnest(%s::text[]) AS g(conversation_id)
              WHERE ch.tenant_id=%s::uuid AND ''' + sql_filter(policy, "'connect'", 'ch.connect_channel_id::text')
        if rollout.enforced:
            sql += ' AND ' + visibility_sql(cur, policy, "'connect'", 'c.connect_channel_id::text', 'c.conversation_id')
        cur.execute(sql, (tenant, groups) if rollout.enforced else (groups, tenant))
        routes = sorted((str(r['channel_id']), r['conversation_id']) for r in cur.fetchall())
        for channel in sorted({channel for channel, _ in routes}):
            require_source(cur, tenant, policy, 'connect', channel, 'view')
        if rollout.enforced and routes:
            # Resolve exactly as require_access, then retain the group/dialogue
            # barriers through all subsequent projection reads and serialization.
            from tenant_connect_dialog_equivalence import available
            links_available = available(cur)
            def resolved():
                if not links_available:
                    return {route: route[1] for route in routes}
                cur.execute('''SELECT channel_id::text AS channel_id,source_id,canonical_id
                  FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid
                  AND source_id=ANY(%s::text[]) AND active''', (tenant, groups))
                links = {(r['channel_id'], r['source_id']): r['canonical_id'] for r in cur.fetchall()}
                return {route: str(links.get(route, route[1])) for route in routes}
            canonical = resolved()
            from tenant_ai_group_coordination import group_jid
            keys = []
            for channel, conversation in sorted(set((ch, canonical[(ch, jid)]) for ch, jid in routes), key=lambda r: (r[1], r[0])):
                if group_jid(conversation):
                    keys.append('ai-group:' + json.dumps([str(tenant), 'connect', conversation]))
                keys.append('ai-dialog:' + json.dumps([str(tenant), 'connect', channel, conversation]))
            # WITH ORDINALITY preserves the established group-before-route order.
            cur.execute('''SELECT pg_advisory_xact_lock(hashtextextended(key,0))
              FROM unnest(%s::text[]) WITH ORDINALITY AS locks(key,n) ORDER BY n''', (keys,))
            cur.fetchall()
            targets = sorted(set(canonical.values()))
            cur.execute('''SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid
              AND provider='connect' AND channel_id='' AND conversation_id=ANY(%s::text[]) FOR SHARE''', (tenant, targets))
            cur.fetchall()
            if resolved() != canonical:
                raise TenantLinearError('Адрес диалога изменился. Обновите диалог.', 409)
            cur.execute('''SELECT c.connect_channel_id::text AS channel_id,c.conversation_id,w.state,w.operator_id,
                (w.state='accepted' AND ep.id IS NOT NULL) AS shared_route
              FROM saas_connect_conversations c LEFT JOIN saas_dialog_workflows w
              ON w.tenant_id=c.tenant_id AND w.channel_id=c.connect_channel_id AND w.conversation_id=c.conversation_id
              LEFT JOIN saas_dialog_episodes ep ON ep.tenant_id=w.tenant_id AND ep.id=w.id
                AND ep.conversation_id=w.conversation_id AND ep.closed_at IS NULL
                AND to_jsonb(ep)->>'group_leader_channel_id' IS NOT NULL
              WHERE c.tenant_id=%s::uuid AND c.conversation_id=ANY(%s::text[])''', (tenant, targets))
            states = {(r['channel_id'], r['conversation_id']): r for r in cur.fetchall()}
            for channel, jid in routes:
                row = states.get((channel, canonical[(channel, jid)]))
                if row is None:
                    raise TenantLinearError('Адрес диалога изменился. Обновите диалог.', 409)
                if (row['state'] and row['state']!='waiting' and not row['shared_route']
                        and int(row['operator_id'])!=int(policy.profile['telegram_id'])):
                    raise TenantLinearError('Диалог недоступен: его принял другой сотрудник.', 404)
        self.groups = {group: set() for group in groups}
        for channel, group in routes:
            self.groups[group].add(channel)
