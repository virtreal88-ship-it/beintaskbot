"""Strict configuration and public result contract for internal diagnostics."""
import uuid
from tenant_linear_policy import TenantLinearError
TOOLS = ('system_status', 'application_errors', 'sync_status', 'database_metrics', 'cash_register', 'previous_incidents', 'instruction_library')
DEFAULTS = {'max_calls': 2, 'timeout_seconds': 20, 'max_context_chars': 6000, 'max_tokens': 12000}
BOUNDS = {'max_calls': (1,5), 'timeout_seconds': (1,30), 'max_context_chars': (500,12000), 'max_tokens': (2048,20000)}

def ids(value):
    if not isinstance(value,list) or len(value)>8: raise TenantLinearError('Invalid internal agent list.')
    try: values=[str(uuid.UUID(str(x))) for x in value]
    except (ValueError,TypeError): raise TenantLinearError('Invalid agent identity.') from None
    if len(set(values))!=len(values): raise TenantLinearError('Duplicate internal agent.')
    return values


def policy(value):
    if not isinstance(value,dict) or set(value)-{'expert_agent_id','expert_agent_ids','internal','allowed_primary_ids','tools',*DEFAULTS}:
        raise TenantLinearError('Invalid expert policy.')
    if not value: return {}
    internal=value.get('internal') is True
    if 'internal' in value and not internal: raise TenantLinearError('Invalid internal policy.')
    if internal:
        if 'expert_agent_id' in value or 'expert_agent_ids' in value: raise TenantLinearError('Delegation cycles are forbidden.')
        result={'internal':True,'allowed_primary_ids':ids(value.get('allowed_primary_ids',[]))}
    else:
        if 'allowed_primary_ids' in value: raise TenantLinearError('Invalid primary policy.')
        if 'expert_agent_ids' in value:
            if 'expert_agent_id' in value: raise TenantLinearError('Choose one binding format.')
            selected=ids(value['expert_agent_ids'])
            if not selected: return {}
            result={'expert_agent_ids':selected}
        else:
            try: result={'expert_agent_id':str(uuid.UUID(str(value['expert_agent_id'])))}
            except (ValueError,KeyError,TypeError): raise TenantLinearError('Choose an internal agent.') from None
    tools=value.get('tools',[])
    if not isinstance(tools,list) or any(x not in TOOLS for x in tools): raise TenantLinearError('Only registered read-only tools are allowed.')
    result['tools']=sorted(set(tools))
    for key,default in DEFAULTS.items():
        number=value.get(key,default);low,high=BOUNDS[key]
        if type(number) is not int or not low<=number<=high: raise TenantLinearError('Invalid expert limit: '+key)
        result[key]=number
    return result


def helper_ids(config):
    return config.get('expert_agent_ids') or ([config['expert_agent_id']] if config.get('expert_agent_id') else [])


def effective(primary,expert,principal):
    internal=policy(expert.get('expert_policy') or {})
    if not internal: return dict(primary)  # Existing explicitly bound experts retain their old policy.
    if not internal.get('internal') or str(principal) not in internal['allowed_primary_ids']:
        raise TenantLinearError('expert_principal_not_allowed',409)
    return {**primary,**{k:min(primary[k],internal[k]) for k in DEFAULTS},
            'tools':sorted(set(primary['tools']) & set(internal['tools']))}


def validate_binding(cur,tenant,agent_id,config,previous=None):
    cfg=config.get('expert_policy') or {}
    if config['role']=='expert':
        if not cfg and (not previous or previous.get('role')!='expert' or (previous.get('expert_policy') or {}).get('internal')):
            raise TenantLinearError('Explicit internal permissions are required.',409)
        if config.get('scopes') or config.get('mode')!='manual' or (cfg and not cfg.get('internal')):
            raise TenantLinearError('An internal agent has no channels, external sending or delegation.')
        for principal in cfg.get('allowed_primary_ids',[]):
            if str(agent_id)==principal: raise TenantLinearError('Delegation cycles are forbidden.')
            cur.execute("SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND role='primary'",(tenant,principal))
            if not cur.fetchone(): raise TenantLinearError('Choose a primary from this company.',409)
    elif cfg:
        if cfg.get('internal'): raise TenantLinearError('Invalid primary policy.')
        for expert in helper_ids(cfg):
            if str(agent_id)==expert: raise TenantLinearError('Delegation cycles are forbidden.')
            cur.execute("SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND role='expert' AND enabled=TRUE",(tenant,expert))
            if not cur.fetchone(): raise TenantLinearError('Choose an enabled internal agent from this company.',409)
    if previous and previous.get('enabled') and config['role']!=previous.get('role','primary'):
        raise TenantLinearError('Disable the agent before changing its role.',409)


def result(value):
    fields={'cause','evidence','confidence','safe_actions','additional_information','requires_human','ticket_summary'}
    if not isinstance(value,dict) or set(value)!=fields: raise ValueError('invalid_expert_result')
    if value['confidence'] not in ('low','medium','high') or type(value['requires_human']) is not bool: raise ValueError('invalid_expert_result')
    for key in ('cause','additional_information','ticket_summary'):
        if not isinstance(value[key],str) or len(value[key])>1500: raise ValueError('invalid_expert_result')
    for key in ('evidence','safe_actions'):
        if not isinstance(value[key],list) or len(value[key])>8 or any(not isinstance(x,str) or len(x)>500 for x in value[key]): raise ValueError('invalid_expert_result')
    from tenant_ai_agent_safety import _SECRET, _INTERNAL
    import json
    serialized=json.dumps(value,ensure_ascii=False)
    if _SECRET.search(serialized) or _INTERNAL.search(serialized): raise ValueError('expert_unsafe_result')
    return {key:value[key] for key in fields}
