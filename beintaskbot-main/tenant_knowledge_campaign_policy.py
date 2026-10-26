"""Versioned consent and conservative per-fragment campaign reservations."""
from tenant_linear_policy import TenantLinearError

FIELDS={'allow_external_media','allow_paid_asr','max_main_calls','max_asr_calls','max_vision_calls'}


def validate(data, total):
    value={k:data.get(k,False if k.startswith('allow_') else total if k=='max_main_calls' else 0) for k in FIELDS}
    for k in ('allow_external_media','allow_paid_asr'):
        if type(value[k]) is not bool: raise TenantLinearError('Некорректное разрешение медиа.')
    for k in ('max_main_calls','max_asr_calls','max_vision_calls'):
        if type(value[k]) is not int or not 0<=value[k]<=total:
            raise TenantLinearError('Некорректный лимит вызовов.')
    if value['allow_paid_asr'] and not value['allow_external_media']:
        raise TenantLinearError('ASR требует разрешения передачи медиа.')
    if not value['allow_paid_asr'] and value['max_asr_calls']:
        raise TenantLinearError('Платный ASR выключен.')
    if not value['allow_external_media'] and value['max_vision_calls']:
        raise TenantLinearError('Передача изображений выключена.')
    return value


def reservation(policy):
    # Upper bounds, including cache misses. Unused reservations are deliberately
    # retained: never refund a possibly dispatched external operation.
    if policy.get('max_main_calls')==0 and policy.get('max_vision_calls')==1 and policy.get('allow_external_media') is True and policy.get('max_asr_calls')==0 and not policy.get('allow_paid_asr'):
        return {'main':0,'asr':0,'vision':1,'calls':1,'tokens':2648}
    asr=int(policy.get('allow_paid_asr',False) and policy.get('max_asr_calls',0)>0)
    vision=2 if policy.get('allow_external_media',False) and policy.get('max_vision_calls',0)>0 else 0
    return {'main':1,'asr':asr,'vision':vision,'calls':1+asr+vision,
            'tokens':16000+8000*asr+4000*vision}


def fits(policy, used, amount):
    return all(used.get(kind,0)+amount[kind]<=policy.get('max_'+kind+'_calls',0)
               for kind in ('main','asr','vision'))
