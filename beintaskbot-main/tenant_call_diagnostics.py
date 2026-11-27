"""Per-call technical evidence. Numeric counters and fixed enums only."""
import time

from tenant_linear_policy import TenantLinearError

END_ORIGINS = {'unknown','command_end','command_reject','audio_transport','duration_limit','whatsapp_terminate',
               'whatsapp_reject','server_end','gateway_shutdown','gateway_sync_timeout','process_closed'}
END_CODES = {'unknown','user_ended','declined','timeout','busy','cancelled','failed','do_not_disturb'}
STREAM_CODES = {'call_closed','provider_start_failed','output_backpressure','handshake_failed',
                'stream_finished','output_write_error','output_write_timeout','input_closed',
                'input_invalid_pcm','input_rate_limit'}
RELAY_CODES = {'browser_closed','provider_closed','browser_invalid_pcm','provider_invalid_pcm',
               'browser_rate_limit','provider_rate_limit','browser_send_timeout','provider_send_timeout',
               'membership_changed','authority_changed','authority_denied','duration_limit',
               'relay_cancelled','transport_error'}
BROWSER_REASONS = {'ongoing','user_end','audio_closed','audio_backpressure','audio_setup_failed',
                   'connection_failed','connect_timeout','control_unavailable','auth_changed',
                   'page_hidden','server_ended'}
CLOSE_CODES = {0,1000,1001,1002,1003,1006,1007,1008,1009,1010,1011,1012,1013,1014,1015,4001}


def migrate(cur):
    cur.execute("""ALTER TABLE saas_browser_calls
      ADD COLUMN IF NOT EXISTS diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb""")


def checked(data, source):
    """Reject unknown keys and raw provider strings at the persistence boundary."""
    def integer(value):
        if type(value) is not int or not 0 <= value <= 2**53-1:
            raise ValueError()
        return value

    def node(value, fields):
        if not isinstance(value,dict) or set(value)-set(fields):
            raise ValueError()
        out={}
        for key,item in value.items():
            rule=fields[key]
            if isinstance(rule,set):
                if not isinstance(item,str) or item not in rule:
                    raise ValueError()
                out[key]=item
            elif isinstance(rule,dict):
                if item is not None:
                    out[key]=node(item,rule)
            else:
                out[key]=integer(item)
                if key=='close_code' and item not in CLOSE_CODES:
                    raise ValueError()
        return out

    audio={key:None for key in ('frames','gaps_over_200ms','max_gap_ms','last_gap_at_ms','last_frame_at_ms')}
    schemas={
        'gateway': {'version':None,'at_ms':None,'end':{'origin':END_ORIGINS,'code':END_CODES,'at_ms':None},
                    'stream':{'code':STREAM_CODES,'input_frames':None,'output_frames':None,'at_ms':None},
                    'browser_to_engine':audio,'whatsapp_to_engine':audio},
        'relay': {'version':None,'code':RELAY_CODES,'at_ms':None,
                  'browser_to_worker':audio,'engine_to_worker':audio,
                  'browser_close':{'close_code':None,'error':{'none','timeout','connection_lost','transport_error'}},
                  'engine_close':{'close_code':None,'error':{'none','timeout','connection_lost','transport_error'}}},
        'browser': {'version':None,'seq':None,'at_ms':None,'reason':BROWSER_REASONS,
                    'close_code':None,'capture_dropped_samples':None,'playback_underrun_samples':None,
                    'playback_overflow_samples':None,'context_suspensions':None,'worker_to_browser':audio},
    }
    try:
        result=node(data,schemas[source])
        if result.get('version')!=1:
            raise ValueError()
        return result
    except (ValueError,TypeError,KeyError):
        raise TenantLinearError('call_diagnostics_invalid',400) from None


def save_gateway(cur, config, boot, item):
    from tenant_platform import _json
    if 'diagnostics' not in item:
        return
    data=checked(item['diagnostics'],'gateway')
    encoded=_json(data)
    cur.execute("""UPDATE saas_browser_calls
      SET diagnostics=jsonb_set(diagnostics,'{gateway}',%s::jsonb)
      WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND boot_id=%s::uuid
        AND provider_id=%s AND peer_jid=%s AND direction=%s
        AND (state NOT IN ('ended','failed') OR %s='ended')
        AND diagnostics->'gateway' IS DISTINCT FROM %s::jsonb""",
        (encoded,config['tenant_id'],config['gateway_id'],boot,item['provider_id'],
         item['peer_jid'],item['direction'],item['state'],encoded))


def save_browser(cur, tenant, actor, row, request, evidence):
    from tenant_platform import _json
    data=checked(evidence,'browser')
    if row['actor_id']!=actor or str(row.get('client_request'))!=request:
        raise TenantLinearError('call_diagnostics_denied',403)
    cur.execute("""UPDATE saas_browser_calls
      SET diagnostics=jsonb_set(diagnostics,'{browser}',%s::jsonb)
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND actor_id=%s AND client_request=%s::uuid
        AND COALESCE((diagnostics->'browser'->>'seq')::bigint,-1)<%s""",
        (_json(data),tenant,str(row['id']),actor,request,data.get('seq',0)))


def save_relay(profile, authority, nonce, evidence):
    from tenant_platform import _connect, _json
    gateway,provider,call=authority
    data=checked(evidence,'relay')
    with _connect() as db,db.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='1s'")
        cur.execute("SET LOCAL statement_timeout='2s'")
        cur.execute("""UPDATE saas_browser_calls
          SET diagnostics=jsonb_set(diagnostics,'{relay}',%s::jsonb)
          WHERE tenant_id=%s::uuid AND id=%s::uuid AND actor_id=%s AND client_request=%s::uuid
            AND gateway_id=%s::uuid AND provider_id=%s""",
          (_json(data),profile['tenant_id'],call,profile['telegram_id'],nonce,gateway,provider))


class Arrivals:
    def __init__(self):
        self.frames=0
        self.gaps=0
        self.maximum=0
        self.last=None
        self.last_gap=0
        self.last_frame=0

    def add(self, now):
        if self.last is not None:
            gap=int((now-self.last)*1000)
            if gap>200:
                self.gaps+=1
                self.maximum=max(self.maximum,gap)
                self.last_gap=int(time.time()*1000)
        self.last=now
        self.last_frame=int(time.time()*1000)
        self.frames+=1

    def snapshot(self):
        return {'frames':self.frames,'gaps_over_200ms':self.gaps,
                'max_gap_ms':self.maximum,'last_gap_at_ms':self.last_gap,
                'last_frame_at_ms':self.last_frame}
