"""Bounded channel schedule, canonical authorization and safe approved-message rendering."""
import re
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from tenant_linear_policy import TenantLinearError


def settings(data):
    enabled=data.get('enabled')
    zone=data.get('timezone','Asia/Baku');start=data.get('start','09:00');end=data.get('end','19:00')
    interval=data.get('interval_minutes',60)
    if not isinstance(enabled,bool) or type(interval) is not int or not 60<=interval<=1440:
        raise TenantLinearError('İnterval ən azı 60 dəqiqə olmalıdır.')
    if (not isinstance(zone,str) or len(zone)>80 or zone.startswith(('right/','posix/'))
        or (zone not in {'UTC','GMT'} and not re.fullmatch(r'[A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)+',zone))):
        raise TenantLinearError('Saat qurşağı düzgün deyil.')
    try:ZoneInfo(zone)
    except (ZoneInfoNotFoundError,ValueError):raise TenantLinearError('Saat qurşağı düzgün deyil.') from None
    for value in (start,end):
        if not isinstance(value,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',value):
            raise TenantLinearError('Saat HH:MM formatında olmalıdır.')
    if start>end:raise TenantLinearError('Başlanğıc vaxt son vaxtdan sonra ola bilməz.')
    return {'enabled':enabled,'timezone':zone,'start':start,'end':end,'interval_minutes':interval}


def address(value):
    if not isinstance(value,str) or not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{4,31}|-100[0-9]{5,16}',value):
        raise TenantLinearError('Kanalı @kanal və ya -100… olaraq yazın.')
    return int(value) if value.startswith('-100') else value


def due(config,now):
    local=now.astimezone(ZoneInfo(config['timezone'])).strftime('%H:%M')
    return config['enabled'] is True and config['start']<=local<=config['end'] and (not config.get('next_at') or now>=config['next_at'])


async def verify(bot,channel,owner):
    chat=await bot.get_chat(address(str(channel)) if isinstance(channel,str) else channel)
    if chat.type!='channel' or int(chat.id)>=0:raise TenantLinearError('Yalnız Telegram kanalı qəbul edilir.')
    me=await bot.get_me()
    for user,is_bot in ((owner,False),(me.id,True)):
        member=await bot.get_chat_member(chat_id=chat.id,user_id=user)
        if member.status not in {'creator','administrator'} or (is_bot and member.status!='creator' and getattr(member,'can_post_messages',False) is not True):
            raise TenantLinearError('Şirkət sahibi və bot kanalda administrator olmalıdır. Bota paylaşım hüququ verin.',403)
    return {'chat_id':int(chat.id),'bot_id':int(me.id),'title':str(chat.title or '')[:200],'verified_by':int(owner)}


def message(payload):
    header=escape(payload['project_name'])
    if payload.get('identifier'):header+=' · '+escape(payload['identifier'][:100])
    result=f"<b>{header}</b>\n\n<b>{escape(payload['title'])}</b>\n\n{escape(payload['summary'])}"
    if payload.get('url'):result+='\n\n'+escape(payload['url'])
    return result
