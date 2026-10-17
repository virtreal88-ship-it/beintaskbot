"""Strict configuration and public result contract for internal diagnostics."""
import uuid
from tenant_linear_policy import TenantLinearError
TOOLS = ('system_status', 'application_errors', 'sync_status', 'database_metrics', 'cash_register', 'previous_incidents')
DEFAULTS = {'max_calls': 2, 'timeout_seconds': 20, 'max_context_chars': 6000, 'max_tokens': 12000}
BOUNDS = {'max_calls': (1,5), 'timeout_seconds': (1,30), 'max_context_chars': (500,12000), 'max_tokens': (2048,20000)}

def policy(value):
    if not isinstance(value,dict) or set(value)-{'expert_agent_id','tools',*DEFAULTS}:
        raise TenantLinearError('Invalid expert policy.')
    if not value: return {}
    try: expert=str(uuid.UUID(str(value['expert_agent_id'])))
    except (ValueError,KeyError,TypeError): raise TenantLinearError('Choose an AI expert.') from None
    tools=value.get('tools',[])
    if not isinstance(tools,list) or any(x not in TOOLS for x in tools):
        raise TenantLinearError('Only registered read-only diagnostic tools are allowed.')
    result={'expert_agent_id':expert,'tools':sorted(set(tools))}
    for key,default in DEFAULTS.items():
        number=value.get(key,default);low,high=BOUNDS[key]
        if type(number) is not int or not low<=number<=high: raise TenantLinearError('Invalid expert limit: '+key)
        result[key]=number
    return result

def validate_binding(cur,tenant,agent_id,config,previous=None):
    if config['role']=='expert':
        if config.get('expert_policy') or config.get('scopes') or config.get('mode')!='manual':
            raise TenantLinearError('An expert is internal only: Manual, no channels and no expert delegation.')
    elif config.get('expert_policy'):
        expert=config['expert_policy']['expert_agent_id']
        if str(agent_id)==expert: raise TenantLinearError('Expert delegation cycles are forbidden.')
        cur.execute("SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND role='expert' AND enabled=TRUE FOR SHARE",(tenant,expert))
        if not cur.fetchone(): raise TenantLinearError('Choose an enabled expert from this company.',409)
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
