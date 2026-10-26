"""Opt-in background worker for verified AI Instructor jobs.

The worker consumes only explicitly queued, owner-selected Primary/Expert pairs.
It uses the tenant's existing Chat AI credential/model settings, reserves every
provider call against the instructor's bounded per-job ledger, and never calls
the client messaging or CRM action layers.
"""
from __future__ import annotations

from tenant_ai_agent_key_policy import retired_text_model

import asyncio
import json
import os
from datetime import datetime, timezone

from tenant_ai_instructor_contract import DemoConnection, RESEARCH_PERMISSIONS, DEMONSTRATION_PERMISSIONS, TaskScope, ContractError
import tenant_ai_instructor_store as store


def enabled():
    truth={'1','true','yes','on'}
    if not (str(os.environ.get('AI_INSTRUCTOR_ENABLED') or '').strip().casefold() in truth and
            str(os.environ.get('AI_INSTRUCTOR_WORKER_ENABLED') or '').strip().casefold() in truth):
        return False
    try:
        store._artifact_root()
        import tiktoken  # noqa: F401
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:return False
    return True


def _provider_pair(job):
    """Resolve only enabled Manual Primary/Expert models from the tenant's existing AI settings."""
    from tenant_platform import _connect, _fernet
    tenant=str(job['tenant_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT a.id,a.role,a.enabled,a.mode,a.model,i.metadata,i.secrets,s.enabled AS settings_enabled,t.status AS tenant_status
              FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
              JOIN saas_tenant_integrations i ON i.tenant_id=a.tenant_id AND i.provider='chat_ai'
              JOIN saas_tenants t ON t.id=a.tenant_id
              WHERE a.tenant_id=%s::uuid AND a.id=ANY(%s::uuid[])''',
              (tenant,[job['primary_agent_id'],job['expert_agent_id']]))
            rows={str(row['id']):dict(row) for row in cur.fetchall()}
    primary=rows.get(str(job['primary_agent_id']));expert=rows.get(str(job['expert_agent_id']))
    if not primary or not expert or primary.get('role')!='primary' or expert.get('role')!='expert':
        raise ContractError('agent_configuration_changed')
    if any(row.get('enabled') is not True or row.get('mode')!='manual' or row.get('settings_enabled') is not True for row in (primary,expert)):
        raise ContractError('agent_configuration_changed')
    from tenant_ai_agent_access import tenant_status_allows_ai
    if not tenant_status_allows_ai(primary.get('tenant_status'),'kommo'):
        raise ContractError('tenant_inactive')
    metadata=primary.get('metadata') if isinstance(primary.get('metadata'),dict) else {}
    secret_blob=primary.get('secrets')
    if not metadata.get('enabled') is True or not metadata.get('consent') is True or not secret_blob:
        raise ContractError('chat_ai_not_authorized')
    try:
        secret=json.loads(_fernet().decrypt(bytes(secret_blob)).decode('utf-8'))
    except Exception:
        raise ContractError('chat_ai_credentials_unavailable') from None
    if secret.get('tenant_id')!=tenant or secret.get('provider')!='chat_ai' or not isinstance(secret.get('api_key'),str) or not secret['api_key']:
        secret.clear()
        raise ContractError('chat_ai_credentials_unavailable')
    models={str(x).strip() for x in metadata.get('models',[]) if isinstance(x,str) and x.strip()}
    if str(metadata.get('model') or '').strip():models.add(str(metadata['model']).strip())
    selected=[]
    for row in (primary,expert):
        model=str(row.get('model') or metadata.get('model') or '').strip()
        if retired_text_model(model):
            secret.clear()
            raise ContractError('configuration_required')
        if not model or model not in models:
            secret.clear()
            raise ContractError('agent_model_unavailable')
        selected.append(model)
    return secret['api_key'],selected[0],selected[1],secret


def _provider(api_key,model):
    def call(prompt,*,max_output_tokens,timeout):
        from tenant_chat_ai_provider import generate
        from tenant_ai_instructor_execution import INSTRUCTOR_SYSTEM_PROMPT
        return generate(api_key,model,'summary',[],prompt,instructions=INSTRUCTOR_SYSTEM_PROMPT,
            with_usage=True,max_output_tokens=min(1800,int(max_output_tokens)),request_timeout=min(60,int(timeout)))
    call.instructor_model=model
    return call


def _scope(job,connection):
    deadline=job.get('deadline_at')
    if hasattr(deadline,'tzinfo'):
        if deadline.tzinfo is None:deadline=deadline.replace(tzinfo=timezone.utc)
        seconds=max(1,min(store.MAX_JOB_SECONDS,int((deadline-datetime.now(timezone.utc)).total_seconds())))
    else:seconds=store.MAX_JOB_SECONDS
    tokens=max(1,min(store.MAX_JOB_TOKENS,int(job.get('budget_tokens') or store.MAX_JOB_TOKENS)-
                     int(job.get('tokens_used') or 0)-int(job.get('reserved_tokens') or 0)))
    actions=max(1,min(store.MAX_JOB_ACTIONS,int(job.get('max_actions') or store.MAX_JOB_ACTIONS)-int(job.get('actions_used') or 0)))
    return TaskScope(str(job['tenant_id']),str(job['id']),str(job['primary_agent_id']),str(job['expert_agent_id']),
        'inventory.warehouse_transfer',False,False,None,None,seconds,actions,tokens,
        str(job.get('permission_level') or 'demonstration_operation'))


async def _heartbeat(tenant_id,job_id,stop):
    while not stop.is_set():
        try:active=await asyncio.to_thread(store.renew_lease,tenant_id,job_id)
        except Exception:active=False
        if not active:return
        try:await asyncio.wait_for(stop.wait(),timeout=30)
        except asyncio.TimeoutError:pass


async def process_job(job,*,executor_factory=None):
    """Run one claimed job; all external work is fenced by an attested demo connection."""
    secret=None;stop=asyncio.Event();pulse=None
    try:
        from tenant_ai_instructor_browser import PlaywrightDemoExecutor
        from tenant_ai_instructor_execution import execute_job
        connection_data=job['connection']
        permission_level=job.get('permission_level') or 'demonstration_operation'
        permissions=RESEARCH_PERMISSIONS if permission_level=='research' else DEMONSTRATION_PERMISSIONS
        connection=DemoConnection(str(job['tenant_id']),str(connection_data['demo_company_id']),str(connection_data['id']),
            str(connection_data['origin']),str(connection_data['account_ref']),str(connection_data['attestation_ref']),
            str(connection_data['restore_receipt_ref']) if connection_data.get('restore_receipt_ref') else None,
            permissions,True)
        scope=_scope(job,connection)
        primary_key,primary_model,expert_model,secret=_provider_pair(job)
        credentials=job.get('credentials') if isinstance(job.get('credentials'),dict) else {}
        executor=(executor_factory() if executor_factory else PlaywrightDemoExecutor())
        tenant,job_id=str(job['tenant_id']),str(job['id'])
        profile={'tenant_id':tenant,'telegram_id':int(job['actor'])}
        pulse=asyncio.create_task(_heartbeat(tenant,job_id,stop),name='ai-instructor-lease')
        await execute_job(job=job,connection=connection,scope=scope,credentials=credentials,executor=executor,
            primary_planner=_provider(primary_key,primary_model),expert_plan_review=_provider(primary_key,expert_model),
            primary_script_writer=_provider(primary_key,primary_model),expert_script_reviewer=_provider(primary_key,expert_model),
            reserve_tokens=lambda prompt,cap,model=None:store.reserve_token_budget(tenant,job_id,prompt,cap,model=model),
            settle_tokens=lambda reservation,actual,uncertain:store.settle_token_budget(tenant,job_id,reservation,actual,uncertain=uncertain),
            record_action=store.record_action,event_sink=store.record_event,add_artifact=store.add_artifact,
            save_lesson=lambda t,j,scenario,screens,video:store.save_generated_lesson_draft(profile,j,scenario,screens,video),
            verify_transfer=lambda observation,_job:bool(isinstance(observation,dict) and observation.get('result_verified') is True),
            finish_job=store.finish_job,check_job=store.is_running)
    except Exception as exc:
        code=str(exc) if isinstance(exc,ContractError) and str(exc).replace('_','').isalnum() else 'instructor_worker_failed'
        try:await asyncio.to_thread(store.finish_job,str(job['tenant_id']),str(job['id']),'blocked',code)
        except Exception:pass
        return {'status':'blocked','reason':code}
    finally:
        stop.set()
        if pulse:
            pulse.cancel()
            try:await pulse
            except asyncio.CancelledError:pass
        if secret:secret.clear()
        if isinstance(job.get('credentials'),dict):job['credentials'].clear()
    return {'status':'completed'}


async def run_worker(logger,*,sleep=asyncio.sleep,executor_factory=None):
    """Dedicated polling loop; disabled unless both server flags explicitly opt in."""
    if not enabled():return
    while True:
        try:
            job=await asyncio.to_thread(store.claim_one)
            if job:
                outcome=await process_job(job,executor_factory=executor_factory)
                logger.info('AI Instructor job finished with state=%s',outcome['status'])
            else:
                await sleep(2)
        except asyncio.CancelledError:raise
        except Exception:
            logger.warning('AI Instructor worker iteration failed safely')
            await sleep(5)
