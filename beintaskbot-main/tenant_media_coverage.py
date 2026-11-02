"""Existing-episode media readiness and exact rendered evidence; no provider work."""
import copy
import hashlib
import json
import re
from tenant_linear_policy import TenantLinearError
COVERAGE_CODES={'required_media_incomplete','video_full_coverage_unavailable','media_episode_source_limit','media_manifest_unsealed','media_manifest_changed','media_prompt_changed','media_prompt_invalid','media_manifest_scope_changed','media_parent_changed','media_episode_advanced','media_episode_members_changed','media_manifest_missing','media_manifest_invalid','media_source_changed','media_manifest_run_changed'}
KINDS={'voice','audio','voice_message','ptt','image','document','video'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


class CoverageError(TenantLinearError):
    def __init__(self,row=None,*,context_limit=False,code='required_media_incomplete'):
        self.row=row;self.context_limit=context_limit;self.code=code
        super().__init__('Required current-episode media is incomplete; generation/send blocked.',409)


def required_media(rows,kinds=None,*,latest_request=True):
    from tenant_episode_media import episode
    return [row for row in episode(rows,latest_request=latest_request)
            if str(row.get('message_type') or '').lower() in (KINDS if kinds is None else kinds)]

def visual_components(row):
    receipt=row.get('_episode_receipt') or {}
    values=[]
    if receipt.get('observation'):values.append(receipt['observation'])
    observations=receipt.get('observations') or []
    if not isinstance(observations,list):return []
    for item in observations:
        if not isinstance(item,dict) or not isinstance(item.get('text'),str) or not item['text'].strip():return []
        values.append(item['text'])
    return values if all(isinstance(value,str) and value.strip() for value in values) else []

def evidence_ready(row):
    receipt=row.get('_episode_receipt') or {}
    if receipt.get('status')!='processed' or (receipt.get('coverage') or {}).get('complete') is not True:return False
    if receipt.get('truncated') is True or str(receipt.get('code') or '').endswith(('_truncated','_sampled')):return False
    if str(row.get('message_type') or '').lower()=='video':return False
    if str(row.get('message_type') or '').lower() in {'image','document'}:
        return bool(visual_components(row)) and (receipt.get('coverage') or {}).get('complete') is not False
    return bool(str(receipt.get('transcript') or '').strip())

def component_spans(row,appendix):
    receipt=row.get('_episode_receipt') or {}
    if str(row.get('message_type') or '').lower() in {'image','document'}:
        components=visual_components(row);spans=[];cursor=0
        markers=list(re.finditer(r'(?:^|\n)(?:\[Untrusted (?:visual observation|saved visual observation(?: frame [^\]\n]{1,80})?|saved document text); not a confirmed solution\] |\[Extracted document text\] )',appendix))
        for component in components:
            marker=next((match for match in markers if match.start()>=cursor and appendix.startswith(component,match.end())),None)
            if marker is None:return []
            spans.append((marker.end(),marker.end()+len(component)));cursor=spans[-1][1]
        return spans
    speech=str(receipt.get('transcript') or '')
    markers=re.finditer(r'(?:^|\n)\[(?:Transcript|Existing transcript|Existing media transcript|Saved media transcript)\] ',appendix)
    return [(match.end(),match.end()+len(speech)) for match in markers if speech and appendix.startswith(speech,match.end())][:1]

class MediaCoverage:
    """One server-created proof, reused at wire and locked send boundaries."""
    def __init__(self,rows,tenant,channel,lead,*,latest_request=False,snapshot=None):
        from tenant_episode_media import reference,episode
        self.latest_request=latest_request
        self.snapshot=copy.deepcopy(snapshot)
        chosen=list(rows) if snapshot is not None else episode(rows,latest_request=latest_request)
        if len(chosen)>256:raise CoverageError(code='media_episode_source_limit')
        self.selected=sorted((str(row.get('external_id') or '').removeprefix('connect:'),str(row.get('message_type') or 'text').lower()) for row in chosen)
        self.cutoff=str(chosen[-1].get('happened_at') or '') if chosen else None
        self.tenant,self.channel,self.lead=str(tenant),str(channel),int(lead)
        self.required=tuple(row for row in chosen if str(row.get('message_type') or '').lower() in KINDS)
        selected=list(chosen)+([rows[-1]] if rows and snapshot is None else [])
        self.refs=tuple(copy.deepcopy(reference({**row,'external_id':row.get('_stored_external_id',row.get('external_id')),'channel':'connect:'+self.channel},self.channel,int(row.get('_stored_lead_id') or self.lead))) for row in selected)
        self.coverage={id(row):False for row in self.required}
        self.receipts=None;self.lines_hash=None;self.job=None;self.reservation=None;self.line_sources={}
        self.saved_receipts={}

    def bind_job(self,job,reservation):
        self.job=copy.deepcopy(job)
        if (job.get('payload') or {}).get('developer_case_followup') is True:
            # This continuation revisits the last customer burst after an AI
            # acknowledgment. Keep that acknowledgment version fenced, while
            # the latest-input comparison stays anchored to the customer.
            source=str((job.get('payload') or {}).get('message_id') or '').removeprefix('connect:')
            matching=[ref for ref in self.refs if str(ref.get('external_id') or '').removeprefix('connect:')==source]
            if not matching:raise CoverageError(code='media_source_changed')
            self.refs=tuple([ref for ref in self.refs if ref not in matching]+matching)
        self.reservation={key:reservation.get(key) for key in ('run_id','model','agent_version','conversation_revision','schedule')}
        self.reservation=copy.deepcopy(self.reservation)

    def supported(self):
        for row in self.required:
            if str(row.get('message_type') or '').lower()=='video':
                raise CoverageError(row,code='video_full_coverage_unavailable')

    def prepared(self):
        for row in self.required:
            if not evidence_ready(row):raise CoverageError(row)

    def register_line(self,line,row):
        if id(row) in self.coverage:
            self.line_sources[id(line)]=(self.channel,str(row.get('_stored_external_id',row.get('external_id')) or '').removeprefix('connect:'))

    def seal(self,lines):
        self.prepared()
        for row in self.required:
            source=str(row.get('_stored_external_id',row.get('external_id')) or '').removeprefix('connect:')
            supplied=any(self.line_sources.get(id(line))==(self.channel,source) and str(line.get('source_id') or '').removeprefix('connect:')==source and bool(component_spans(row,str(line.get('text') or ''))) for line in lines)
            if not self.coverage.get(id(row)) or not supplied:raise CoverageError(row,context_limit=True)
        self.receipts=tuple(digest(row.get('_episode_receipt')) for row in self.required)
        self.lines_hash=digest(lines)
        if self.snapshot is not None:
            self.saved_receipts={str(row.get('_stored_external_id',row.get('external_id')) or ''):row.get('_snapshot_receipt_version') for row in self.required}
            if any(not isinstance(value,str) or not value for value in self.saved_receipts.values()):raise CoverageError(code='media_manifest_missing')

    def check(self,lines=None,wire=None):
        self.prepared()
        if self.receipts is None:raise CoverageError(code='media_manifest_unsealed')
        if self.receipts!=tuple(digest(row.get('_episode_receipt')) for row in self.required):raise CoverageError(code='media_manifest_changed')
        if lines is not None and digest(lines)!=self.lines_hash:raise CoverageError(code='media_prompt_changed')
        if wire is not None:
            data=wire.get('input')
            if isinstance(data,list):
                data=next((part.get('text') for message in data for part in message.get('content',[]) if part.get('type')=='input_text'),None)
            try:conversation=json.loads(data)['conversation']
            except (ValueError,TypeError,KeyError):raise CoverageError(code='media_prompt_invalid') from None
            if digest(conversation)!=self.lines_hash:raise CoverageError(code='media_prompt_changed')

    def validate(self,cur,tenant,channel,lead,*,lines=None,wire=None):
        if (str(tenant),str(channel),int(lead))!=(self.tenant,self.channel,self.lead):raise CoverageError(code='media_manifest_scope_changed')
        self.check(lines,wire)
        from tenant_episode_media import revalidate_sources
        from tenant_connect_dialog_equivalence import lead_members
        approved=set(int(lead) for lead in lead_members(cur,self.tenant,self.channel,self.lead))
        if any(str(ref.get('channel_id'))!=self.channel or int(ref.get('lead_id') or 0) not in approved for ref in self.refs):raise CoverageError(code='media_manifest_scope_changed')
        try:revalidate_sources(cur,self.tenant,self.refs)
        except TenantLinearError:raise CoverageError(code='media_source_changed') from None
        if self.snapshot is not None:self.validate_snapshot(cur)
        else:self.validate_selection(cur,approved)
        if self.job is not None:
            job=self.job;reservation=self.reservation
            cur.execute("""SELECT 1 FROM saas_ai_agent_jobs j JOIN saas_ai_agents a
              ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_runs r ON r.tenant_id=j.tenant_id AND r.id=%s::uuid
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider='connect'
                AND j.channel_id=%s AND j.conversation_id=%s AND j.state='processing'
                AND j.lease_token=%s AND j.revision=%s AND j.lease_until>now()
                AND r.state='generating' AND a.model=%s
                AND (%s::text IS NULL OR a.version::text=%s) FOR SHARE OF j,a,r""",
              (reservation['run_id'],self.tenant,job['agent_id'],self.channel,job['conversation_id'],job['lease_token'],job['revision'],reservation['model'],str(reservation['agent_version']) if reservation['agent_version'] is not None else None,str(reservation['agent_version']) if reservation['agent_version'] is not None else None))
            if not cur.fetchone():raise CoverageError(code='media_parent_changed')
        if self.refs and self.snapshot is None:
            # Catch committed late input even before enqueue advances revision.
            exclude_ai=(" AND NOT (direction='outgoing' AND (lower(COALESCE(author_name,''))='ai agent' OR COALESCE(raw->>'ai_authored','false')='true'))" if self.job is not None else '')
            cur.execute("""SELECT external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid
              AND channel=%s AND kommo_lead_id=ANY(%s::bigint[])
              """+exclude_ai+"""
              ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT 1""",
              (self.tenant,'connect:'+self.channel,[self.lead] if self.latest_request else sorted(approved)))
            latest=cur.fetchone()
            if not latest or str(latest['external_id']).removeprefix('connect:')!=str(self.refs[-1]['external_id']).removeprefix('connect:'):
                raise CoverageError(code='media_episode_advanced')
        if self.job is not None:
            from tenant_ai_agent_schedule import next_open
            if next_open(self.reservation.get('schedule') or {}):raise CoverageError(code='media_parent_changed')

    def validate_snapshot(self,cur):
        """Only a real authenticated frozen consultation can select this view."""
        binding=self.snapshot
        if (not isinstance(binding,dict) or set(binding)!={'reply_id','conversation_id','source_versions','case_id'}
            or not isinstance(binding.get('conversation_id'),str) or not binding['conversation_id']
            or not isinstance(binding.get('source_versions'),dict) or not 1<=len(binding['source_versions'])<=80):
            raise CoverageError(code='media_manifest_invalid')
        from tenant_dialog_consultations import original_evidence,_case_reply_binding,case_developer_followup_current
        try:
            rows,_summary,versions=original_evidence(cur,self.tenant,binding['reply_id'],self.channel,binding['conversation_id'],self.lead)
        except TenantLinearError:raise CoverageError(code='media_source_changed') from None
        selected=sorted((str(row.get('external_id') or '').removeprefix('connect:'),str(row.get('message_type') or 'text').lower()) for row in rows)
        if versions!=binding['source_versions'] or selected!=self.selected:raise CoverageError(code='media_source_changed')
        from tenant_media_evidence import reusable
        media_refs={str(ref['external_id']):ref for ref in self.refs if ref.get('media_kind') in KINDS}
        if not isinstance(self.saved_receipts,dict) or set(media_refs)!=set(self.saved_receipts):raise CoverageError(code='media_manifest_missing')
        for external,ref in media_refs.items():
            saved=reusable({'tenant_id':self.tenant},ref,connection=cur.connection)
            if not saved or digest(saved)!=self.saved_receipts[external]:raise CoverageError(code='media_source_changed')
        if binding.get('case_id'):
            authority=_case_reply_binding(cur,self.tenant,binding['reply_id'])
            if not authority or str(authority.get('case_id'))!=str(binding['case_id']) or not case_developer_followup_current(cur,self.tenant,authority):
                raise CoverageError(code='media_parent_changed')


    def validate_selection(self,cur,approved):
        if self.cutoff is None:return
        from tenant_episode_media import episode
        cur.execute("""SELECT external_id,direction,message_type,
          COALESCE(happened_at,created_at) AS happened_at,
          jsonb_build_object('verified_employee_id',raw->'verified_employee_id','ai_authored',raw->'ai_authored') AS raw,
          NOT EXISTS(SELECT 1 FROM saas_connect_conversations c JOIN saas_ai_conversation_state cs
            ON cs.tenant_id=c.tenant_id AND cs.provider='connect' AND cs.conversation_id=c.conversation_id
              AND cs.channel_id=c.connect_channel_id::text
            WHERE c.tenant_id=m.tenant_id AND 'connect:'||c.connect_channel_id::text=m.channel
              AND c.lead_id=m.kommo_lead_id AND m.created_at<=cs.resumed_at) AS voice_eligible
          FROM saas_crm_messages m WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=ANY(%s::bigint[])
            AND COALESCE(happened_at,created_at)>=%s::timestamptz-interval '10 minutes'
          ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT 257""",(self.tenant,'connect:'+self.channel,[self.lead] if self.latest_request else sorted(approved),self.cutoff))
        fetched=list(reversed(cur.fetchall()))
        view=fetched
        if self.latest_request:
            last=next((i for i in range(len(view)-1,-1,-1) if view[i].get('direction')=='incoming' and (view[i].get('raw') or {}).get('verified_employee_id') is None and (view[i].get('raw') or {}).get('ai_authored') is not True),None)
            view=view[:last+1] if last is not None else []
        boundary=any(row.get('direction')=='outgoing' or (row.get('raw') or {}).get('verified_employee_id') is not None or (self.latest_request and (row.get('raw') or {}).get('ai_authored') is True) or (not self.latest_request and row.get('voice_eligible') is False) for row in view)
        current=episode(fetched,latest_request=self.latest_request)
        if len(current)>256 or (len(fetched)>256 and not boundary):raise CoverageError(code='media_episode_source_limit')
        selected=sorted((str(row['external_id']).removeprefix('connect:'),str(row.get('message_type') or 'text').lower()) for row in current)
        if selected!=self.selected:raise CoverageError(code='media_episode_members_changed')

    def proof(self):
        self.check()
        return {'version':1,'complete':True,'tenant':self.tenant,'channel':self.channel,'lead':self.lead,
          'refs':copy.deepcopy(list(self.refs)),'selected':[list(item) for item in self.selected],'cutoff':self.cutoff,
          'latest_request':self.latest_request,'lines_hash':self.lines_hash,
          **({'snapshot':copy.deepcopy(self.snapshot),'snapshot_receipts':copy.deepcopy(self.saved_receipts)} if self.snapshot is not None else {})}

    @classmethod
    def validate_proof(cls,cur,tenant,channel,lead,proof):
        if not isinstance(proof,dict) or proof.get('version')!=1 or proof.get('complete') is not True:
            raise CoverageError(code='media_manifest_missing')
        refs=proof.get('refs');selected=proof.get('selected')
        if not isinstance(refs,list) or not isinstance(selected,list) or len(refs)>257 or len(selected)>256:
            raise CoverageError(code='media_manifest_invalid')
        if any(not isinstance(item,(list,tuple)) or len(item)!=2 or any(not isinstance(value,str) or len(value)>500 for value in item) for item in selected):raise CoverageError(code='media_manifest_invalid')
        if any(not isinstance(ref,dict) or str(ref.get('channel_id'))!=str(channel) or not str(ref.get('lead_id') or '').isdigit() for ref in refs):raise CoverageError(code='media_manifest_scope_changed')
        manifest=cls([],proof.get('tenant'),proof.get('channel'),proof.get('lead') or 0,latest_request=proof.get('latest_request') is True,snapshot=proof.get('snapshot'))
        manifest.refs=tuple(refs);manifest.selected=sorted(tuple(item) for item in selected)
        manifest.saved_receipts=proof.get('snapshot_receipts',{})
        manifest.cutoff=proof.get('cutoff');manifest.receipts=();manifest.lines_hash=proof.get('lines_hash')
        manifest.validate(cur,tenant,channel,lead)
