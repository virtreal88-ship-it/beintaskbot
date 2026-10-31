"""Employee-selected, bounded case submission through the existing extractor."""
import uuid
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_group_coordination import group_jid
import tenant_knowledge_candidates_store as store

_SUBMISSION_CAPABILITY=object()

def scoped(profile):
    return {**profile, '_case_submission_capability':_SUBMISSION_CAPABILITY}


def assignment(cur, tenant, channel, conversation, expected=None):
    if group_jid(conversation):
        from tenant_ai_group_coordination import lock_group, authority
        lock_group(cur,tenant,conversation)
        selected=authority(cur,tenant,None,conversation,allow_stopped=True)
        if not selected:raise TenantLinearError('Сначала назначьте агента этому диалогу.',409)
        cur.execute('SELECT id AS agent_id,version,name FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',
                    (tenant,selected['primary_agent_id']))
        row=cur.fetchone()
        if row:row={**row,'revision':selected['revision']}
    else:
        cur.execute('''SELECT cs.agent_id,cs.revision,a.version,a.name FROM saas_ai_conversation_state cs
          JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=cs.agent_id
          WHERE cs.tenant_id=%s::uuid AND cs.provider='connect' AND cs.channel_id=%s AND cs.conversation_id=%s
          FOR SHARE OF cs,a''',(tenant,channel,conversation))
        row=cur.fetchone()
    if not row: raise TenantLinearError('Сначала назначьте агента этому диалогу.',409)
    value={'agent_id':str(row['agent_id']),'agent_version':int(row['version']),
           'assignment_revision':int(row['revision']),'agent_name':row['name']}
    if expected and any(value[k]!=expected.get(k) for k in ('agent_id','agent_version','assignment_revision')):
        raise TenantLinearError('Назначение агента изменилось. Обновите выбор.',409)
    return value


def preview(profile,data):
    if not isinstance(data,dict) or set(data)!={'channel_id','conversation_id'}:
        raise TenantLinearError('Некорректные параметры.')
    profile=scoped(profile)
    channel=identifier(data['channel_id']);conversation=data['conversation_id']
    with _connect() as conn:
        store.schema(conn)
        with conn.cursor() as cur:
            item=store.snapshot(cur,profile,{'channel_id':channel,'conversation_id':conversation})
            agent=assignment(cur,item['tenant'],channel,conversation)
            return {'agent':agent,'messages':[{'id':ref['id'],'role':ref['role'],'text':msg['text'],
                       'media_kind':ref.get('media_kind'),'message_type':meta['message_type'],
                       'happened_at':meta['happened_at']}
                    for ref,msg,meta in zip(item['refs'],item['messages'],item['preview_metadata'])],
                    'has_earlier':bool(item['next_before'])}


def validate(cur,profile,data,training):
    tenant,_,_,_,_,_=store.route(cur,profile,data)
    agent=assignment(cur,tenant,data['channel_id'],data['conversation_id'],training)
    full=store.snapshot(cur,profile,{k:v for k,v in data.items() if k!='message_ids'})
    ids=[r['id'] for r in full['refs']];selected=data['message_ids']
    if not selected or len(set(selected))!=len(selected):raise TenantLinearError('Выберите границы обращения.',400)
    try:start=ids.index(selected[0]);end=ids.index(selected[-1])
    except ValueError:raise TenantLinearError('Границы изменились. Обновите выбор.',409) from None
    if start>end or ids[start:end+1]!=selected:raise TenantLinearError('Выберите непрерывный фрагмент обращения.',409)
    key=store.digest(['employee_case',tenant,data['channel_id'],data['conversation_id'],selected[0],selected[-1],agent['agent_id']])
    return {**agent,'submission_key':key,'first_message_id':selected[0],'last_message_id':selected[-1]}


def submit(profile,data):
    required={'channel_id','conversation_id','message_ids','agent_id','agent_version','assignment_revision'}
    if not isinstance(data,dict) or set(data)!=required or not isinstance(data['message_ids'],list):
        raise TenantLinearError('Некорректные параметры.')
    if type(data['agent_version']) is not int or type(data['assignment_revision']) is not int:
        raise TenantLinearError('Некорректная версия агента.')
    selected=[identifier(v) for v in data['message_ids']]
    if not 1<=len(selected)<=80:raise TenantLinearError('Выберите от 1 до 80 сообщений.')
    training={k:data[k] for k in ('agent_id','agent_version','assignment_revision')}
    training['agent_id']=identifier(training['agent_id'])
    from tenant_knowledge_candidates_service import analyze
    payload={'request_id':str(uuid.uuid4()),'channel_id':identifier(data['channel_id']),
       'conversation_id':data['conversation_id'],'message_ids':selected,'analysis_agent_id':training['agent_id'],
       'allow_external_media':False,'allow_paid_asr':False,'allow_vision':False}
    return analyze(scoped(profile),payload,training=training)
