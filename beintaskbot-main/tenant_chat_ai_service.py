"""Reservation precedes paid calls; partial audio is explicitly disclosed."""
import asyncio
import time
from tenant_chat_ai_policy import command, history
from tenant_chat_ai_store import reserve, validate, cache_transcript, finish
from tenant_chat_media import download
from tenant_chat_ai_provider import generate, transcribe
from tenant_linear_policy import TenantLinearError


async def run(session, data, logger):
    item = await asyncio.to_thread(reserve, session, command(data))
    if 'cached_result' in item:
        return item['cached_result']
    result = None
    audio_deadline = time.monotonic() + 180
    try:
        for row in item['pending_audio']:
            if time.monotonic() >= audio_deadline:
                break
            await asyncio.to_thread(validate, item)
            try:
                media = await asyncio.to_thread(download, row.get('media_url'), item['config']['media_hosts'])
                await asyncio.to_thread(validate, item)
                text = await asyncio.to_thread(transcribe, item['api_key'], item['config']['transcription_model'], *media)
                await asyncio.to_thread(cache_transcript, item, row, text)
                item['transcripts'][str(row['external_id'])] = text
            except TenantLinearError:
                raise
            except Exception:
                logger.warning('Tenant chat audio unavailable; no automatic retry')
        lines, missing = history(item['rows'], item['transcripts'])
        if item['command']['mode'] == 'transcribe':
            text = item['transcripts'].get(str(item['rows'][0]['external_id']))
            if not text:
                raise TenantLinearError('Səs yazısını mətnə çevirmək alınmadı. Domen, format və AI ayarlarını yoxlayın.', 503)
        else:
            await asyncio.to_thread(validate, item)
            text = await asyncio.to_thread(generate, item['api_key'], item['config']['model'], item['command']['mode'], lines, item['command']['draft'])
        result = {'text': text, 'mode': item['command']['mode'], 'missing_audio': missing,
                  'transcribed_audio': len(item['transcripts']), 'message_count': len(item['rows'])}
    except TenantLinearError:
        raise
    except Exception:
        logger.warning('Tenant chat AI result unknown; no automatic retry')
        raise TenantLinearError('AI cavabı alınmadı. Bu sorğu avtomatik təkrarlanmır.', 503) from None
    finally:
        await asyncio.to_thread(finish, item, result)
    return result
