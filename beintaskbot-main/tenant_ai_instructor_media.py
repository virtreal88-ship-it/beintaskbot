"""Bounded scenario/subtitle and media adapters for AI Instructor jobs."""
from __future__ import annotations

import os
import json
import math
import re
import shutil
import subprocess
import re
from dataclasses import dataclass
from pathlib import Path

from tenant_ai_instructor_contract import ContractError

_LABELS={
 'ru':{'open_demo':'Открыть демонстрационную программу','inventory':'Открыть склад','transfers':'Открыть перемещения',
       'new_transfer':'Создать демонстрационное перемещение','source_warehouse':'Выбрать исходный склад',
       'destination_warehouse':'Выбрать склад назначения','product':'Выбрать демонстрационный товар',
       'quantity':'Указать количество','save_demo_transfer':'Сохранить в демо-программе','transfer_result':'Проверить результат'},
 'az':{'open_demo':'Demo proqramını açın','inventory':'Anbarı açın','transfers':'Köçürmələri açın',
       'new_transfer':'Demo köçürməsi yaradın','source_warehouse':'Mənbə anbarı seçin',
       'destination_warehouse':'Təyinat anbarını seçin','product':'Demo məhsulunu seçin',
       'quantity':'Miqdarı daxil edin','save_demo_transfer':'Demo proqramında yadda saxlayın','transfer_result':'Nəticəni yoxlayın'},
 'en':{'open_demo':'Open the demo program','inventory':'Open inventory','transfers':'Open transfers',
       'new_transfer':'Create a demo transfer','source_warehouse':'Select the source warehouse',
       'destination_warehouse':'Select the destination warehouse','product':'Select a demo product',
       'quantity':'Set the quantity','save_demo_transfer':'Save in the demo program','transfer_result':'Verify the result'},
}


def build_script(events, language='az'):
    """Build a reviewable deterministic script from observed executor actions."""
    if language not in _LABELS or not isinstance(events,list) or not 1<=len(events)<=20:
        raise ContractError('invalid_scenario')
    slides=[];subtitle_cues=[];order=0
    for event in events:
        action=str(event.get('action') or '')
        target=str(event.get('target') or '')
        if action not in {'open_demo','observe','navigate','click','fill_demo','screenshot'}:
            raise ContractError('scenario_action_forbidden')
        label=_LABELS[language].get(target) or _LABELS[language].get(action)
        if not label: continue
        order+=1
        title=label
        text=str(event.get('verified_description') or label)[:800]
        slides.append({'order':order,'title':title,'text':text,'evidence_ref':str(event.get('evidence_ref') or '')[:80]})
        subtitle_cues.append({'start_ms':(order-1)*4500,'end_ms':order*4500,'text':label[:120]})
    if not slides: raise ContractError('scenario_empty')
    return {'language':language,'title':{'ru':'Демонстрационное перемещение товара','az':'Məhsulun demo köçürülməsi','en':'Demonstration product transfer'}[language],
            'slides':slides,'subtitles':subtitle_cues,'review_status':'draft','verified':False}


def generate_script(generator, events, language='az', *, token_budget_remaining=12000):
    """Optional injected Primary-model adapter with a hard per-call token ceiling.

    The caller must reserve usage through the existing AI Agent ledger before
    invoking this function. Tests use a fake generator; no provider is called here.
    """
    if language not in _LABELS or type(token_budget_remaining) is not int or not 1<=token_budget_remaining<=12000:
        raise ContractError('instructor_token_budget')
    bounded=[]
    for event in events[:20]:
        if event.get('action') not in {'open_demo','observe','navigate','click','fill_demo','screenshot'}:
            raise ContractError('scenario_action_forbidden')
        bounded.append({'action':str(event.get('action')),'target':str(event.get('target') or '')[:80],
                        'verified_description':str(event.get('verified_description') or '')[:400],
                        'evidence_ref':str(event.get('evidence_ref') or '')[:80]})
    prompt=('Create an accurate, reviewable user training script from these verified demo actions only. '
            'Do not claim successful completion unless an observed result confirms it. '
            'Return JSON with title, slides[{title,text}], subtitles[{start_ms,end_ms,text}]. '
            'Language='+language+'; events='+json.dumps(bounded,ensure_ascii=False,separators=(',',':')))
    raw,usage=generator(prompt,max_output_tokens=min(2400,token_budget_remaining),timeout=30)
    if not isinstance(usage,dict) or type(usage.get('total_tokens')) is not int or not 0<=usage['total_tokens']<=token_budget_remaining:
        raise ContractError('instructor_token_budget_exceeded')
    try: parsed=json.loads(raw)
    except (ValueError,TypeError): raise ContractError('scenario_invalid_json') from None
    slides=parsed.get('slides');cues=parsed.get('subtitles')
    if (not isinstance(parsed.get('title'),str) or not 1<=len(parsed['title'])<=180 or
        not isinstance(slides,list) or not 1<=len(slides)<=20 or not isinstance(cues,list) or len(cues)!=len(slides)):
        raise ContractError('scenario_invalid_shape')
    for slide in slides:
        if not isinstance(slide,dict) or not isinstance(slide.get('title'),str) or not isinstance(slide.get('text'),str) or len(slide['title'])>120 or len(slide['text'])>1000:
            raise ContractError('scenario_invalid_shape')
    srt_text(cues)
    return {'language':language,'title':parsed['title'],'slides':slides,'subtitles':cues,
            'review_status':'draft','verified':False,'tokens_used':usage['total_tokens']}


def review_script(reviewer, scenario, observed_events, *, token_budget_remaining):
    """Ask an injected authorized Expert to flag unsupported claims; never auto-approve."""
    if type(token_budget_remaining) is not int or not 1<=token_budget_remaining<=12000:
        raise ContractError('instructor_token_budget')
    slides=scenario.get('slides') if isinstance(scenario,dict) else None
    if (not isinstance(slides,list) or not 1<=len(slides)<=20 or
        not isinstance(scenario.get('title'),str) or not isinstance(observed_events,list) or len(observed_events)>20):
        raise ContractError('scenario_invalid_shape')
    evidence=[{'action':str(e.get('action') or '')[:40],'target':str(e.get('target') or '')[:80],
               'verified_description':str(e.get('verified_description') or '')[:300]} for e in observed_events[:20]]
    prompt=('Review this draft lesson against only the observed actions. Return JSON {supported:boolean, '
            'issues:[short strings]}. This is advice for an employee and must never approve or publish. '
            'Draft='+json.dumps({'title':scenario.get('title','')[:180],'slides':slides},ensure_ascii=False)[:12000]+
            '; observed='+json.dumps(evidence,ensure_ascii=False,separators=(',',':')))
    raw,usage=reviewer(prompt,max_output_tokens=min(700,token_budget_remaining),timeout=30)
    if not isinstance(usage,dict) or type(usage.get('total_tokens')) is not int or not 0<=usage['total_tokens']<=token_budget_remaining:
        raise ContractError('instructor_token_budget_exceeded')
    try: result=json.loads(raw)
    except (ValueError,TypeError): raise ContractError('expert_review_invalid_json') from None
    if (not isinstance(result,dict) or type(result.get('supported')) is not bool or
        not isinstance(result.get('issues'),list) or len(result['issues'])>10 or
        any(not isinstance(x,str) or len(x)>240 for x in result['issues'])):
        raise ContractError('expert_review_invalid_shape')
    return {'supported':result['supported'],'issues':result['issues'],
            'review_status':'employee_review_required','tokens_used':usage['total_tokens'],
            'verified':False,'published':False}


def srt_text(cues):
    if not isinstance(cues,list) or not 1<=len(cues)<=20: raise ValueError('subtitle_cues')
    def stamp(ms):
        if type(ms) is not int or not 0<=ms<=600_000: raise ValueError('subtitle_timestamp')
        hours,rem=divmod(ms,3_600_000);minutes,rem=divmod(rem,60_000);seconds,millis=divmod(rem,1000)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}'
    rows=[];previous=0
    for i,cue in enumerate(cues,1):
        start,end=cue.get('start_ms'),cue.get('end_ms');text=str(cue.get('text') or '').strip()
        if type(start) is not int or type(end) is not int or start<previous or end<=start or len(text)>120 or not text or any(ord(ch)<32 and ch not in '\n\t' for ch in text):
            raise ValueError('subtitle_cue_invalid')
        rows.extend((str(i),f'{stamp(start)} --> {stamp(end)}',text,'',''));previous=end
    return '\n'.join(rows)


def render_mp4(recording, subtitles, output, *, ffmpeg_path=None):
    """Mux a browser recording and subtitle track; paths are explicit local artifacts."""
    ffmpeg=str(ffmpeg_path or os.environ.get('FFMPEG_PATH') or shutil.which('ffmpeg') or '')
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            pass
    if not ffmpeg: raise RuntimeError('ffmpeg_unavailable')
    recording=Path(recording).resolve();subtitles=Path(subtitles).resolve();output=Path(output).resolve()
    if not recording.is_file() or not subtitles.is_file(): raise ValueError('media_input_missing')
    output.parent.mkdir(parents=True,exist_ok=True)
    args=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y','-i',str(recording),'-i',str(subtitles),
          '-map','0:v:0','-map','0:a?','-map','1:0','-c:v','libx264','-preset','veryfast','-crf','24',
          '-c:a','aac','-c:s','mov_text','-movflags','+faststart','-t','600',str(output)]
    result=subprocess.run(args,capture_output=True,text=True,timeout=180,check=False)
    if result.returncode or not output.is_file() or output.stat().st_size<1024:
        output.unlink(missing_ok=True)
        raise RuntimeError('ffmpeg_render_failed') from None
    return {'path':str(output),'size_bytes':output.stat().st_size,'mime':'video/mp4'}


@dataclass(frozen=True)
class ElevenLabsBudget:
    max_usd: float=1.0
    max_characters: int=2000
    # Pricing metadata must be supplied and reviewed by an operator; no guessed default.
    confirmed_usd_per_1000_chars: float=0.0
    confirmation_id: str=''
    allow_one_call: bool=False


class ElevenLabsAdapter:
    """Optional TTS connector. Refuses before network access unless cap is provable."""
    def __init__(self, api_key=None, *, budget=None, transport=None, reserve_one_call=None, model_id='eleven_multilingual_v2'):
        self.api_key=api_key or os.environ.get('ELEVENLABS_API_KEY','')
        self.budget=budget or ElevenLabsBudget()
        self.model_id=model_id
        self.transport=transport or self._request_audio
        self.reserve_one_call=reserve_one_call
        self._calls=0

    def preflight(self, text):
        chars=len(str(text or ''))
        rate=self.budget.confirmed_usd_per_1000_chars
        if (not self.budget.allow_one_call or self._calls>=1 or not self.api_key or not self.reserve_one_call or
            not self.budget.confirmation_id or type(self.budget.max_usd) not in (int,float) or not math.isfinite(self.budget.max_usd) or self.budget.max_usd<=0 or self.budget.max_usd>1.0 or
            type(rate) not in (int,float) or not math.isfinite(rate) or rate<=0 or
            chars<1 or chars>self.budget.max_characters):
            return {'allowed':False,'reason':'price_cap_unverified_or_call_unavailable','estimated_usd':None}
        cost=chars*rate/1000
        if cost>self.budget.max_usd:
            return {'allowed':False,'reason':'price_cap_exceeded','estimated_usd':round(cost,6)}
        return {'allowed':True,'reason':'within_confirmed_cap','estimated_usd':round(cost,6)}

    def synthesize(self, text, *, voice_id):
        check=self.preflight(text)
        if not check['allowed']: raise RuntimeError(check['reason'])
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(voice_id or '')):raise RuntimeError('elevenlabs_voice_id_invalid')
        if self.model_id not in {'eleven_multilingual_v2','eleven_flash_v2_5','eleven_v3','eleven_v4'}:
            raise RuntimeError('elevenlabs_model_not_allowlisted')
        # Count before the first outbound request: timeout/uncertain outcomes can
        # never result in an automatic paid retry.
        self._calls+=1
        if self.reserve_one_call(self.budget.confirmation_id,check['estimated_usd']) is not True:
            raise RuntimeError('elevenlabs_single_call_budget_already_used')
        return self.transport(self.api_key,str(voice_id),str(text),timeout=20)

    def _request_audio(self,api_key,voice_id,text,*,timeout):
        import requests
        url='https://api.elevenlabs.io/v1/text-to-speech/'+voice_id
        try:
            response=requests.post(url,headers={'xi-api-key':api_key,'Content-Type':'application/json',
                'Accept':'audio/mpeg'},params={'output_format':'mp3_44100_128'},
                json={'text':text,'model_id':self.model_id},timeout=(3,timeout),allow_redirects=False,stream=True)
            with response:
                if response.status_code<200 or response.status_code>=300:raise RuntimeError('elevenlabs_http_'+str(response.status_code))
                if not str(response.headers.get('Content-Type','')).lower().startswith('audio/'):raise RuntimeError('elevenlabs_unexpected_content_type')
                audio=bytearray()
                for chunk in response.iter_content(65536):
                    if chunk:
                        audio.extend(chunk)
                        if len(audio)>12_000_000:raise RuntimeError('elevenlabs_audio_too_large')
                if not audio:raise RuntimeError('elevenlabs_empty_audio')
                return bytes(audio)
        except requests.RequestException:
            # A timeout after reservation is treated as an unknown paid call;
            # adapter never automatically retries.
            raise RuntimeError('elevenlabs_request_unknown') from None

