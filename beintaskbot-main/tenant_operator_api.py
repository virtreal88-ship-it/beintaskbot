"""Tenant-isolated operator dialog projection and local display-name overrides."""
import asyncio
import json
from functools import partial
from uuid import UUID
from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
from tenant_operator_dialogs import listing,history,colleague_options,save_display_name,mark_read,mark_all_read
from tenant_ai_group_coordination import configure as configure_group, register_employee, legacy_resolution_preview
from tenant_operator_assignment import options as assignment_options, assign as assign_agent, bulk_preview as assignment_bulk_preview, bulk_apply as assignment_bulk_apply
from tenant_operator_continuation import options as continuation_options, grant as grant_continuation
from tenant_operator_legacy import options as legacy_options, recover as recover_legacy
from tenant_operator_copilot import run as run_copilot
from tenant_connect_avatars import get_avatar
from tenant_group_participants import get_participants
from tenant_read_timing import ReadTiming
from worker_crm_capacity import avatar_auth_io, session_io, dialog_io, dialog_revision_io, CapacityUnavailable
from tenant_operator_access import observed_operator
from tenant_dialog_access import sources as dialog_sources
from tenant_dialog_lifecycle import mutate as dialog_lifecycle
from tenant_dialog_presence import get_status as presence_status, set_status as presence_set_status
from tenant_dialog_view_preferences import company_panel_preferences, preferences as dialog_view_preferences


class GroupWorkUpdates:
    """Wake bounded, scoped revision readers after a committed change."""
    def __init__(self, timeout=10, *, reject_when_full=False):
        self.timeout=timeout
        self.reject_when_full=reject_when_full
        self.waiters={}

    async def read(self, profile, conversation, known_revision, read_revision):
        scope=(str(profile['tenant_id']),conversation)
        actor=(scope[0],str(profile['telegram_id']))
        if len(self.waiters)>=128 or sum(owner==actor for _,owner in self.waiters.values())>=3:
            if self.reject_when_full:
                raise CapacityUnavailable()
            return await read_revision()
        changed=asyncio.Event()
        self.waiters[changed]=(scope,actor)
        try:
            # Subscribe before reading so a concurrent acceptance wakes the card.
            result=await read_revision()
            if result.get('revision')!=known_revision:
                return result
            try:
                await asyncio.wait_for(changed.wait(),timeout=self.timeout)
            except TimeoutError:
                pass
            return await read_revision()
        finally:
            self.waiters.pop(changed,None)

    def publish(self, profile, conversation):
        scope=(str(profile['tenant_id']),conversation)
        for changed,(key,_) in self.waiters.items():
            if key==scope:
                changed.set()


def handler(member_from_request,origin,logger,resource, *, group_work_updates=None,message_updates=None):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        scope_verified=False
        revision_read=resource=='history' and request.query.get('group_work_revision')=='1'
        timing=ReadTiming(1 if resource=='dialogs' else 2,logger) if request.method=='GET' and resource in {'dialogs','history'} and not revision_read else None
        try:
            if timing:
                profile = await session_io.run(timing.authenticate, member_from_request, request)
            else:
                pool = avatar_auth_io if resource == 'avatar' else session_io
                profile = await pool.run(member_from_request, request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            query=request.query
            if request.method=='GET':
                # Optional identity binding for newer clients; authorization remains unchanged.
                if 'X-Tenant-Id' in request.headers or 'X-User-Id' in request.headers:
                    _expected({'expected_tenant_id':request.headers.get('X-Tenant-Id'),'expected_user_id':request.headers.get('X-User-Id')},profile)
                    scope_verified=True
                if resource=='copilot' and query.get('saved_only')=='1':
                    _expected(query,profile)
                    from tenant_saved_only_copilot import preflight
                    result=await asyncio.to_thread(preflight,profile,dict(query))
                elif resource=='message_updates':
                    from tenant_dialog_message_revision import read_revision
                    if not scope_verified:
                        _expected(query,profile)
                    channel,conversation=query.get('channel_id',''),query.get('conversation_id','')
                    selected=None
                    if channel or conversation:
                        if not channel or not 0<len(conversation)<=180:
                            raise ValueError('Invalid selected dialog')
                        selected=(str(UUID(channel)),conversation)
                    initial = True

                    async def read_message_revision():
                        nonlocal initial
                        current=profile if initial else await session_io.run(member_from_request,request)
                        initial=False
                        if not current:
                            raise TenantLinearError('Нет доступа.',403)
                        _expected({'expected_tenant_id':profile['tenant_id'],
                                   'expected_user_id':profile['telegram_id']},current)
                        return await dialog_revision_io.run(read_revision,current,selected)

                    known=query.get('revision','')
                    if message_updates and 0<len(known)<=80:
                        result=await message_updates.read(profile,'',known,read_message_revision)
                    else:
                        result=await read_message_revision()
                elif resource=='presence':
                    _expected(query,profile)
                    result=await asyncio.to_thread(presence_status,profile)
                elif resource=='company_panel_preferences':
                    if not scope_verified:
                        _expected(query,profile)
                    result=await asyncio.to_thread(company_panel_preferences,profile)
                elif resource=='view_preferences':
                    result=await asyncio.to_thread(dialog_view_preferences,profile)
                elif resource=='sources':
                    result=await asyncio.to_thread(dialog_sources,profile)
                elif resource=='group_participants':
                    result=await get_participants(profile,dict(query))
                elif resource=='transfer_candidates':
                    if not scope_verified:
                        _expected(query,profile)
                    from tenant_dialog_lifecycle import transfer_candidates
                    result=await asyncio.to_thread(transfer_candidates,profile,dict(query))
                elif resource=='transfer_details':
                    from tenant_dialog_lifecycle import transfer_details
                    result=await asyncio.to_thread(transfer_details,profile,dict(query))
                elif resource=='deal_dialogs':
                    _expected(query,profile)
                    from tenant_deal_dialogs import linked_dialogs
                    result=await asyncio.to_thread(linked_dialogs,profile,query.get('source'),query.get('deal_id'))
                elif resource=='deal_dialog_history':
                    _expected(query,profile)
                    from tenant_deal_dialog_links import history as deal_history
                    result=await asyncio.to_thread(deal_history,profile,query.get('source'),
                        query.get('deal_id'),query.get('link_id'),query.get('before'))
                elif resource=='deal_dialog_phone_candidates':
                    _expected(query,profile)
                    from tenant_deal_dialog_links import phone_candidates
                    result=await asyncio.to_thread(phone_candidates,profile,query.get('deal_id'),query.get('message_id'))
                elif resource=='contact_phone':
                    from tenant_contact_phone import get_phone
                    result=await get_phone(profile,dict(query))
                elif resource=='avatar':
                    content,mime=await get_avatar(profile,dict(query))
                    return web.Response(body=content,content_type=mime,headers={
                        'Cache-Control':'private, max-age=3600',
                        'X-Content-Type-Options':'nosniff','Vary':'Cookie'})
                elif resource=='dialogs' and query.get('colleague_options')=='1':
                    _expected(query,profile)
                    if 'observed_operator_id' in query:
                        raise TenantLinearError('Некорректный режим просмотра.',400)
                    result=await timing.run(colleague_options,profile)
                elif resource=='dialogs':
                    colleague=observed_operator(query,incompatible=('search_overview','owner_overview',
                        'completion_preview','paired_history','mixed_history_card'))
                    if colleague is not None:
                        _expected(query,profile)
                    options={'combine_history':True} if query.get('paired_history')=='1' else {}
                    if options and query.get('mixed_history_card')=='1':
                        options['allow_mixed_history_card']=True
                    if query.get('completion_preview')=='1': options['completion_preview']=True
                    if 'lifecycle' in query: options['lifecycle']=query['lifecycle']
                    if query.get('search_overview')=='1' and query.get('search','').strip():
                        _expected(query,profile)
                        options['search_overview']=True
                    if query.get('owner_overview')=='1':
                        _expected(query,profile)
                        options['owner_overview']=True
                    if colleague is not None:
                        options['observed_operator_id']=colleague
                    elif query.get('message_revision')=='1':
                        options['include_message_revision']=True
                    read=partial(listing,**options) if options else listing
                    result=await timing.run(read,profile,query.get('search',''),query.get('channel',''),query.get('agent',''),query.get('state',''),query.get('offset',0))
                elif revision_read:
                    _expected(query,profile)
                    if query.get('provider')!='connect':
                        raise TenantLinearError('Диалог не найден.',404)
                    from tenant_dialog_group_work import read_revision
                    conversation=query.get('conversation_id')
                    async def read_group_revision():
                        current=await session_io.run(member_from_request, request)
                        if not current:
                            raise TenantLinearError('Нет доступа.',403)
                        _expected(query,current)
                        return await dialog_io.run(read_revision,current,query.get('channel_id'),
                            conversation,query.get('lead_id'))
                    known=query.get('known_group_work_revision','')
                    if group_work_updates and 0<len(known)<=80:
                        result=await group_work_updates.read(profile,conversation,known,read_group_revision)
                    else:
                        result=await dialog_io.run(read_revision,profile,query.get('channel_id'),
                            conversation,query.get('lead_id'))
                elif resource=='history':
                    colleague=observed_operator(query,incompatible=('search_overview','owner_overview',
                        'support_case_only','paired_history','paired_history_redirect','anchor',
                        'operator_events','shared_reminder_events','internal_comments'))
                    if colleague is not None:
                        _expected(query,profile)
                    case_only=query.get('support_case_only')=='1'
                    options={'combine_history':True} if query.get('paired_history')=='1' else {}
                    # The compact right-panel read never enriches messages or
                    # selects staff timeline metadata, even with both flags.
                    if case_only:
                        options={'support_case_only':True}
                    elif any(query.get(flag)=='1' for flag in ('operator_events','shared_reminder_events','internal_comments')):
                        _expected(query,profile)
                        if options.get('combine_history') and query.get('paired_history_redirect')=='1':
                            options['redirect_paired_history']=True
                        if query.get('operator_events')=='1':
                            options.update(include_operator_events=True,operator_events_revision=query.get('operator_events_revision'))
                        if query.get('shared_reminder_events')=='1':
                            options['include_shared_reminder_events']=True
                        if query.get('internal_comments')=='1':
                            options['include_internal_comments']=True
                    if not case_only and query.get('owner_overview')=='1':
                        _expected(query,profile)
                        options['owner_overview']=True
                    if not case_only and query.get('search_overview')=='1' and query.get('search','').strip():
                        _expected(query,profile)
                        options.update(search_overview=True,search=query.get('search',''))
                    if colleague is not None:
                        options['observed_operator_id']=colleague
                    read=partial(history,**options) if options else history
                    result=await timing.run(read,profile,query.get('provider',''),query.get('lead_id'),query.get('channel_id',''),query.get('conversation_id',''),
                        None if case_only else query.get('before'),None if case_only else query.get('anchor'))
                    dialog=result.get('dialog') or {}
                    group_refresh=query.get('group_work_refresh')=='1' and dialog.get('group_work_revision')
                    if not case_only and not group_refresh and not dialog.get('archive_read_only'):
                        from tenant_message_delivery import enrich_history
                        result=await enrich_history(profile,result)
                elif resource=='agent_assignment':
                    result=await asyncio.to_thread(assignment_options,profile,dict(query))
                elif resource=='agent_assignment_bulk':
                    result=await asyncio.to_thread(assignment_bulk_preview,profile,query.get('channel_id'),query.get('agent_id'))
                elif resource=='group_legacy_resolution':
                    _expected(query,profile)
                    result=await asyncio.to_thread(legacy_resolution_preview,profile,dict(query))
                elif resource in {'legacy_recovery','legacy_continuation'}:
                    _expected(query,profile)
                    result=await asyncio.to_thread(continuation_options if resource=='legacy_continuation' else legacy_options,profile,dict(query))
                else:
                    raise TenantLinearError('Метод не поддерживается.',405)
            else:
                _origin(request,origin)
                if request.method!='POST' or resource not in {'presence','view_preferences','company_panel_preferences','dialogs_lifecycle','dialogs_remove','sources','display_name','agent_assignment','agent_assignment_bulk','group_coordination','group_staff','legacy_recovery','legacy_continuation','copilot','dialogs_read','dialogs_read_all','dialog_duplicates','dialog_duplicate_preview','contact_display_phone','dialog_history_merges','quick_replies','internal_comments','deal_dialog_link'} or request.content_type!='application/json':
                    raise TenantLinearError('Метод не поддерживается.',405)
                if request.content_length is not None and request.content_length>20_000:
                    raise TenantLinearError('Слишком большой запрос.',413)
                raw=await request.read()
                if len(raw)>20_000: raise TenantLinearError('Слишком большой запрос.',413)
                data=json.loads(raw)
                if not isinstance(data,dict): raise TenantLinearError('Некорректный запрос.')
                _expected(data,profile)
                payload={key:value for key,value in data.items() if key not in {'expected_tenant_id','expected_user_id'}}
                if 'observed_operator_id' in payload:
                    raise TenantLinearError('Режим просмотра не допускает изменений.',403)
                if resource=='dialogs_remove':
                    from tenant_operator_dialog_removal import remove
                    result=await asyncio.to_thread(remove,profile,payload)
                    return web.json_response({'success':True,'result':result},headers=headers)
                if resource=='deal_dialog_link':
                    from tenant_deal_dialog_links import attach
                    result=await asyncio.to_thread(attach,profile,payload)
                    return web.json_response({'success':True,'result':result},headers=headers)
                if resource=='dialog_history_merges':
                    from tenant_dialog_history_merge_jobs import action as history_merge_action
                    return web.json_response({'success':True,'result':{'job':await asyncio.to_thread(history_merge_action,profile,payload)}},headers=headers)
                if resource=='dialog_duplicates':
                    # Query text may contain an exact phone; do not put it in access-log URLs.
                    from tenant_dialog_duplicates import discover
                    return web.json_response({'success':True,'result':await asyncio.to_thread(discover,profile,payload)},headers=headers)
                if resource in {'dialog_duplicate_preview','contact_display_phone'}:
                    from tenant_dialog_duplicate_actions import preview, save_phone
                    action={'dialog_duplicate_preview':preview,
                            'contact_display_phone':save_phone}[resource]
                    return web.json_response({'success':True,'result':await asyncio.to_thread(action,profile,payload)},headers=headers)
                if resource=='internal_comments':
                    from tenant_dialog_internal_comments import save as save_comment
                    result=await asyncio.to_thread(save_comment,profile,payload)
                    return web.json_response({'success':True,'result':result},headers=headers)
                if resource=='quick_replies':
                    from tenant_dialog_quick_replies import quick_replies
                    result=await asyncio.to_thread(quick_replies,profile,payload)
                    return web.json_response({'success':True,'result':result},headers=headers)
                result=await asyncio.to_thread(presence_set_status if resource=='presence' else dialog_view_preferences if resource=='view_preferences' else company_panel_preferences if resource=='company_panel_preferences' else dialog_lifecycle if resource=='dialogs_lifecycle' else dialog_sources if resource=='sources' else mark_read if resource=='dialogs_read' else mark_all_read if resource=='dialogs_read_all' else run_copilot if resource=='copilot' else assignment_bulk_apply if resource=='agent_assignment_bulk' else grant_continuation if resource=='legacy_continuation' else recover_legacy if resource=='legacy_recovery' else register_employee if resource=='group_staff' else configure_group if resource=='group_coordination' else assign_agent if resource=='agent_assignment' else save_display_name,profile,payload)
                if group_work_updates and resource=='dialogs_lifecycle' and result.get('group_changed'):
                    group_work_updates.publish(profile,payload.get('conversation_id'))
            return web.json_response({'success':True,'result':result},headers=headers)
        except CapacityUnavailable:
            return web.json_response({'success':False,'error':'Диалоги временно недоступны.'}, status=503, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error),
                **({'keep_request':error.keep_request} if resource=='copilot' and type(getattr(error,'keep_request',None)) is bool else {}),
                **({'scope_verified':True} if scope_verified and error.status==409 else {})},status=error.status,headers=headers)
        except (TypeError,ValueError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос.'},status=400,headers=headers)
        except Exception:
            logger.exception('Operator dialog read failed')
            return web.json_response({'success':False,'error':'Диалоги временно недоступны.'},status=503,headers=headers)
        finally:
            if timing: timing.finish()
    return handle

