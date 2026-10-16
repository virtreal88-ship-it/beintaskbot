"""Optional agent boundary on existing exact Test Mode bindings."""
from tenant_linear_policy import identifier,TenantLinearError


def selected_agent(cur,tenant,data):
    if 'agent_id' not in data:return None
    agent=identifier(data['agent_id'])
    cur.execute('SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND enabled=TRUE FOR SHARE',(tenant,agent))
    if not cur.fetchone():raise TenantLinearError('Выберите включённого агента этой компании.',403)
    return agent


def clause(alias,provider):
    # Callers supply fixed source aliases/provider literals, never browser values.
    table={'connect':'saas_ai_agent_connect_test_mode','kommo':'saas_ai_agent_test_mode'}[provider]
    return " AND NOT EXISTS (SELECT 1 FROM "+table+" tb JOIN saas_ai_agent_settings ts ON ts.tenant_id=tb.tenant_id WHERE tb.tenant_id="+alias+".tenant_id AND ts.operating_mode='test' AND tb.agent_id IS NOT NULL AND tb.agent_id<>"+alias+".id) "
