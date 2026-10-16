"""Wall-clock night intervals, independent of the AI worker and its settings."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from tenant_linear_policy import TenantLinearError


def clock(value):
    try:
        parsed=time.fromisoformat(str(value))
        if parsed.tzinfo or len(str(value))!=5 or parsed.second or parsed.microsecond: raise ValueError()
        return parsed
    except (ValueError,TypeError): raise TenantLinearError('Время должно быть в формате HH:MM.') from None


def wall(day, hour, zone, fold):
    naive=datetime.combine(day,hour)
    value=naive.replace(tzinfo=zone,fold=fold)
    if value.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=naive:
        raise TenantLinearError('Выбранное время отсутствует из-за перехода на летнее время. Выберите другую границу смены.')
    return value


def interval(day='',zone='Asia/Baku',start='19:00',end='09:00',*,now=None):
    try: tz=ZoneInfo(str(zone))
    except (ZoneInfoNotFoundError,ValueError,TypeError): raise TenantLinearError('Неизвестный часовой пояс.') from None
    first,last=clock(start),clock(end)
    if first==last: raise TenantLinearError('Начало и окончание смены должны различаться.')
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: raise ValueError('now must be timezone-aware')
    local=now.astimezone(tz)
    try:
        selected=date.fromisoformat(str(day)) if day else local.date()-timedelta(days=local.time().replace(tzinfo=None)<first)
        if day and selected.isoformat()!=day: raise ValueError()
        begins=wall(selected,first,tz,0)
        finishes=wall(selected+timedelta(days=last<first),last,tz,1)
    except (ValueError,OverflowError): raise TenantLinearError('Некорректная дата смены.') from None
    left,right=begins.astimezone(timezone.utc),finishes.astimezone(timezone.utc)
    current=now.astimezone(timezone.utc)
    cutoff=max(left,min(right,current))
    return {'date':selected.isoformat(),'timezone':str(zone),'start':start,'end':end,
            'starts_at':begins.isoformat(),'ends_at':finishes.isoformat(),
            'starts_at_utc':left.isoformat(),'ends_at_utc':right.isoformat(),'cutoff_at':cutoff.isoformat(),
            'state':'not_started' if current<left else 'ongoing' if current<right else 'completed',
            'duration_minutes':int((right-left).total_seconds()/60)}
