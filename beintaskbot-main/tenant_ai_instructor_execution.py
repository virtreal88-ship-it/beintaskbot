"""Dependency-injected, side-effect-fenced execution pipeline for instructor jobs.

The caller must supply tenant-authorized Primary/Expert providers, durable token
reservation callbacks, a verified demo connection, and a result verifier. There is
no default worker/provider here, so importing this module cannot run a job.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path

from tenant_ai_instructor_contract import BrowserAction, DemoConnection, TaskScope, ContractError
from tenant_ai_instructor_media import generate_script, review_script, render_mp4, srt_text

_FORBIDDEN_TARGETS={'billing','users','settings','crm','send','whatsapp','delete','publish'}


def _decode_plan(raw,scope,connection):
    try:plan=json.loads(raw)
    except (ValueError,TypeError):raise ContractError('primary_plan_invalid_json') from None
    rows=plan.get('actions') if isinstance(plan,dict) else None
    if not isinstance(rows,list) or not 1<=len(rows)<=9:raise ContractError('primary_plan_invalid_shape')
    result=[]
    for row in rows:
        if not isinstance(row,dict) or set(row)-{'action','target','demo_value'}:raise ContractError('primary_plan_invalid_shape')
        if str(row.get('target') or '').casefold() in _FORBIDDEN_TARGETS:raise ContractError('action_forbidden')
        action=BrowserAction(row.get('action'),row.get('target'),row.get('demo_value'))
        action.validate(scope,connection)
        # The executor opens the demo itself and captures a sanitized frame
        # after each approved operation. Models may propose operations only.
        if action.action not in {'navigate','click','fill_demo'}:raise ContractError('plan_action_forbidden')
        result.append({'action':action.action,'target':action.target,'demo_value':action.demo_value})
    return result


async def _provider_call(call,prompt,cap,reserve,settle):
    reservation=await asyncio.to_thread(reserve,prompt,cap)
    if not reservation:raise ContractError('instructor_token_budget')
    try:
        raw,usage=await asyncio.to_thread(call,prompt,max_output_tokens=cap,timeout=30)
    except Exception:
        # Provider timeouts have uncertain billing. Keep the full reservation and
        # do not issue a retry from this execution path.
        await asyncio.to_thread(settle,reservation,None,uncertain=True)
        raise
    total=usage.get('total_tokens') if isinstance(usage,dict) else None
    if (type(total) is not int or total<0 or
        total>int(reservation.get('reserved_tokens',0))):
        await asyncio.to_thread(settle,reservation,None,uncertain=True)
        raise ContractError('instructor_usage_unknown')
    await asyncio.to_thread(settle,reservation,total,uncertain=False)
    return raw,total


async def execute_job(*,job,connection:DemoConnection,scope:TaskScope,credentials,executor,
                      primary_planner,expert_plan_review,primary_script_writer,expert_script_reviewer,
                      reserve_tokens,settle_tokens,record_action,event_sink,add_artifact,
                      save_lesson,verify_transfer,finish_job,ffmpeg_path=None):
    """Run one scoped training task and save only a private employee-review draft.

    This function never sends messages, writes CRM data, performs handoff or
    publishes lessons. The approved browser operation itself is restricted by the
    existing BrowserAction contract to the isolated demo account.
    """
    connection.validate();scope.validate(connection)
    if (str(job.get('tenant_id'))!=str(scope.tenant_id) or str(job.get('id'))!=str(scope.task_id) or
        str(job.get('primary_agent_id'))!=str(scope.primary_agent_id) or
        str(job.get('expert_agent_id'))!=str(scope.expert_agent_id)):
        raise ContractError('job_scope_mismatch')
    if not callable(verify_transfer):raise ContractError('transfer_verifier_required')
    session=None;screenshot_paths=[];evidence_rows=[]
    token_left=min(12000,scope.tokens_remaining)
    def reserve(prompt,output):
        nonlocal token_left
        estimate=len(prompt.encode('utf-8'))+output+512
        if estimate>token_left:raise ContractError('instructor_token_budget')
        value=reserve_tokens(prompt,output)
        if not value:raise ContractError('instructor_token_budget')
        token_left-=int(value.get('reserved_tokens',estimate))
        return value
    def settle(reservation,actual,*,uncertain):
        nonlocal token_left
        result=settle_tokens(reservation['id'],actual,uncertain=uncertain)
        if not uncertain:token_left+=max(0,int(reservation.get('reserved_tokens',0))-int(actual))
        return result
    try:
        session=await executor.open_demo(connection,scope,credentials)
        if isinstance(credentials,dict):credentials.clear()
        await asyncio.to_thread(record_action,str(scope.tenant_id),str(scope.task_id),'observe',None)
        first=await executor.observe(session,scope)
        prompt=('Plan a warehouse transfer in this verified isolated demo only. Use only the fixed semantic targets '
          'and fixture values in the registered contract. Never use production data or unrelated modules. '
          'Do not claim success. Return JSON {actions:[{action,target,demo_value?}]}. Objective='+
          str(job.get('title') or '')[:180]+'; language='+str(job.get('language') or 'az')+
          '; initial_demo_observation='+json.dumps(first,ensure_ascii=False)[:1200])
        raw,_=await _provider_call(primary_planner,prompt,min(1600,token_left),reserve,settle)
        actions=_decode_plan(raw,scope,connection)
        canonical=json.dumps(actions,ensure_ascii=False,separators=(',',':'),sort_keys=True)
        plan_hash=hashlib.sha256(canonical.encode()).hexdigest()
        review_prompt=('Review this exact allowlisted plan for the single inventory transfer operation. '
          'Approve only if every target/value is safe and within the demo contract. Return JSON '
          '{approved:boolean,plan_sha256:string}. No execution or publish authority. plan_sha256='+plan_hash+
          '; plan='+canonical)
        expert_raw,_=await _provider_call(expert_plan_review,review_prompt,min(700,token_left),reserve,settle)
        try:decision=json.loads(expert_raw)
        except (ValueError,TypeError):raise ContractError('expert_plan_review_invalid_json') from None
        if (not isinstance(decision,dict) or decision.get('approved') is not True or
            decision.get('plan_sha256')!=plan_hash):raise ContractError('expert_plan_not_approved')
        for item in actions:
            action=BrowserAction(**item)
            await asyncio.to_thread(record_action,str(scope.tenant_id),str(scope.task_id),action.action,action.target)
            observed=await executor.act(session,scope,action)
            await asyncio.to_thread(record_action,str(scope.tenant_id),str(scope.task_id),'screenshot',None)
            evidence=await executor.capture(session,scope)
            screenshot=executor.screenshot_path(session,evidence)
            screenshot_paths.append(screenshot)
            artifact=await asyncio.to_thread(add_artifact,str(scope.tenant_id),str(scope.task_id),'screenshot',screenshot,
                                                'image/png',evidence.sanitization_receipt_ref)
            description=str(observed.get('transfer_result') or observed.get('status') or observed.get('title') or '')[:300]
            event={'action':action.action,'target':action.target or '',
                   'verified_description':description,'evidence_ref':artifact['id']}
            evidence_rows.append(event)
            await asyncio.to_thread(event_sink,str(scope.tenant_id),str(scope.task_id),'action_completed',
                                    {'action':action.action,'target':action.target or ''})
            await asyncio.to_thread(event_sink,str(scope.tenant_id),str(scope.task_id),'screenshot_captured',
                                    {'artifact_id':artifact['id'],'sha256':evidence.sha256,'kind':'screenshot'})
        await asyncio.to_thread(record_action,str(scope.tenant_id),str(scope.task_id),'observe',None)
        final_observation=await executor.observe(session,scope)
        verified=await asyncio.to_thread(verify_transfer,final_observation,job)
        if verified is not True:raise ContractError('transfer_result_not_verified')
        script_generator=lambda prompt,**kwargs:_sync_provider(primary_script_writer,prompt,kwargs,reserve,settle)
        remaining=max(1,min(12000,token_left))
        scenario=generate_script(script_generator,evidence_rows,job.get('language') or 'az',token_budget_remaining=remaining)
        expert_generator=lambda prompt,**kwargs:_sync_provider(expert_script_reviewer,prompt,kwargs,reserve,settle)
        review=review_script(expert_generator,scenario,evidence_rows,token_budget_remaining=max(1,min(12000,token_left)))
        if review.get('issues'):
            scenario['review_issues']=review['issues']
        subtitles=srt_text(scenario['subtitles'])
        from tenant_ai_instructor_store import _artifact_root
        try:root=_artifact_root()
        except Exception:raise ContractError('private_artifact_storage_unavailable') from None
        output_dir=(root/str(scope.tenant_id)/str(scope.task_id));output_dir.mkdir(parents=True,exist_ok=True)
        subtitle_path=output_dir/'captions.srt';subtitle_path.write_text(subtitles,encoding='utf-8')
        scenario_path=output_dir/'scenario.json';scenario_path.write_text(json.dumps(scenario,ensure_ascii=False),encoding='utf-8')
        closed=await executor.close(session);session=None
        recording_path=closed.get('recording_path')
        if not recording_path:raise ContractError('browser_recording_missing')
        recording=Path(recording_path)
        if recording.stat().st_size>250_000_000:raise ContractError('recording_too_large')
        await asyncio.to_thread(add_artifact,str(scope.tenant_id),str(scope.task_id),'subtitle',subtitle_path,'application/x-subrip',str(uuid.uuid4()))
        await asyncio.to_thread(add_artifact,str(scope.tenant_id),str(scope.task_id),'scenario',scenario_path,'application/json',str(uuid.uuid4()))
        video_path=output_dir/'lesson.mp4'
        await asyncio.to_thread(render_mp4,recording,subtitle_path,video_path,ffmpeg_path=ffmpeg_path)
        video=await asyncio.to_thread(add_artifact,str(scope.tenant_id),str(scope.task_id),'recording',video_path,'video/mp4',str(uuid.uuid4()))
        lesson=await asyncio.to_thread(save_lesson,str(scope.tenant_id),str(scope.task_id),scenario,screenshot_paths,video['id'])
        await asyncio.to_thread(event_sink,str(scope.tenant_id),str(scope.task_id),'lesson_saved',
                                {'lesson_id':lesson['lesson_id'],'published':False})
        await asyncio.to_thread(finish_job,str(scope.tenant_id),str(scope.task_id),'completed',None)
        return {'status':'completed','lesson':lesson,'video':video,'employee_review_required':True,
                'verified_demo_transfer':True,'real_erp_pilot':'unverified'}
    except Exception as exc:
        safe_reason=str(exc) if isinstance(exc,ContractError) and str(exc).replace('_','').isalnum() else 'instructor_execution_failed'
        state='blocked' if isinstance(exc,ContractError) else 'failed'
        try:await asyncio.to_thread(event_sink,str(scope.tenant_id),str(scope.task_id),state,{'reason':safe_reason})
        except Exception:pass
        try:await asyncio.to_thread(finish_job,str(scope.tenant_id),str(scope.task_id),state,safe_reason)
        except Exception:pass
        raise
    finally:
        if session is not None:
            try:await executor.close(session)
            except Exception:pass
        if isinstance(credentials,dict):credentials.clear()


def _sync_provider(provider,prompt,kwargs,reserve,settle):
    reservation=reserve(prompt,kwargs['max_output_tokens'])
    try:raw,usage=provider(prompt,max_output_tokens=kwargs['max_output_tokens'],timeout=kwargs['timeout'])
    except Exception:
        settle(reservation,None,uncertain=True);raise
    total=usage.get('total_tokens') if isinstance(usage,dict) else None
    if (type(total) is not int or total<0 or
        total>int(reservation.get('reserved_tokens',0))):
        settle(reservation,None,uncertain=True);raise ContractError('instructor_usage_unknown')
    settle(reservation,total,uncertain=False)
    return raw,usage

