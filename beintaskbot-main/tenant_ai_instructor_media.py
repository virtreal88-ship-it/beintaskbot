"""Bounded scenario/subtitle and media adapters for AI Instructor jobs."""
from __future__ import annotations

import os
import json
import shutil
import subprocess
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
    raw,usage=generator(prompt,max_output_tokens=min(700,token_budget_remaining),timeout=30)
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
    raw,usage=reviewer(prompt,max_output_tokens=min(300,token_budget_remaining),timeout=30)
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


def align_subtitles_to_timeline(cues, timeline_events, *, lead_ms=650, max_cue_ms=5000):
    """Anchor each caption to observed browser actions, slightly before the action starts."""
    if not isinstance(cues,list) or not cues:
        raise ContractError('subtitle_cues')
    events=[]
    for event in timeline_events if isinstance(timeline_events,list) else []:
        if (isinstance(event,dict) and type(event.get('at_ms')) is int and event['at_ms']>=0
                and event.get('action') in {'navigate','click','fill_demo'}):
            events.append(event)
    if not events:
        return [dict(cue) for cue in cues]
    # Keep room for every cue plus the four-second title card before SRT's
    # bounded ten-minute maximum; very late actions are proportionally mapped.
    cue_limit=600_000-4_000
    latest_anchor=max((event['at_ms'] for event in events),default=0)
    safe_anchor=max(0,cue_limit-len(cues)*1400)
    time_scale=min(1.0,safe_anchor/max(1,latest_anchor))
    output=[];previous_end=0
    for index,cue in enumerate(cues):
        event_index=round(index*(len(events)-1)/max(1,len(cues)-1))
        anchor=min(safe_anchor,round(events[event_index]['at_ms']*time_scale))
        start=max(previous_end,max(0,anchor-lead_ms))
        next_index=round((index+1)*(len(events)-1)/max(1,len(cues)-1)) if index+1<len(cues) else None
        next_anchor=min(safe_anchor,round(events[next_index]['at_ms']*time_scale)) if next_index is not None else start+max_cue_ms
        end=min(cue_limit,start+max_cue_ms,max(start+1400,next_anchor-100))
        output.append({**cue,'start_ms':start,'end_ms':end})
        previous_end=end
    return output


def _filter_path(path):
    return str(path).replace('\\','/').replace(':',r'\:').replace("'",r"\'").replace(',',r'\,')


def _zoom_expressions(timeline_events, fps=30, zoom_level=1.28):
    """Build bounded, smooth focus moves from browser-measured element boxes."""
    events=[]
    for event in timeline_events if isinstance(timeline_events,list) else []:
        if not isinstance(event,dict) or event.get('action') not in {'click','fill_demo'}:continue
        focus=event.get('focus') or {}
        try:
            at=int(event['at_ms']);vw=float(event['viewport_width']);vh=float(event['viewport_height'])
            cx=(float(focus['x'])+float(focus['width'])/2)/vw
            cy=(float(focus['y'])+float(focus['height'])/2)/vh
        except (KeyError,TypeError,ValueError,ZeroDivisionError):continue
        if at<0 or vw<=0 or vh<=0:continue
        events.append((at,cx,cy,event))
    events=sorted(events,key=lambda item:item[0])[:12]
    zexpr='1';xexpr='(iw-iw/zoom)*0.5';yexpr='(ih-ih/zoom)*0.5';highlights=[]
    for at,cx,cy,event in reversed(events):
        zoom_in=max(0,round(at*fps/1000)-12);in_end=zoom_in+18
        next_ms=next((item[0] for item in events if item[0]>at),at+2500)
        hold_end=max(in_end+12,min(in_end+72,round(next_ms*fps/1000)-8))
        zoom_out=hold_end+18
        zexpr=(f"if(lt(on,{zoom_in}),{zexpr},if(lt(on,{in_end}),1+({zoom_level}-1)*(on-{zoom_in})/18,"
               f"if(lt(on,{hold_end}),{zoom_level},if(lt(on,{zoom_out}),{zoom_level}-({zoom_level}-1)*(on-{hold_end})/18,{zexpr}))))")
        progress=f"(on-{zoom_in})/18";smooth=f"(({progress})*({progress})*(3-2*({progress})))"
        tx=max(0.05,min(.95,cx));ty=max(0.05,min(.95,cy))
        target_x=f"max(0,min(iw-iw/zoom,iw*{tx:.5f}-iw/(2*zoom)))"
        target_y=f"max(0,min(ih-ih/zoom,ih*{ty:.5f}-ih/(2*zoom)))"
        moving_x=f"(iw-iw/zoom)*0.5+(({target_x})-(iw-iw/zoom)*0.5)*{smooth}"
        moving_y=f"(ih-ih/zoom)*0.5+(({target_y})-(ih-ih/zoom)*0.5)*{smooth}"
        xexpr=f"if(between(on,{zoom_in},{in_end}),{moving_x},if(between(on,{in_end},{zoom_out}),{target_x},{xexpr}))"
        yexpr=f"if(between(on,{zoom_in},{in_end}),{moving_y},if(between(on,{in_end},{zoom_out}),{target_y},{yexpr}))"
        focus=event.get('focus') or {};at_s=max(0,at/1000+4)
        # The crop centers the observed target; pulse a restrained focus frame
        # around that point after the zoom settles.
        w=max(34,min(1100,round(float(focus['width'])*1.5*zoom_level+18)))
        h=max(30,min(800,round(float(focus['height'])*1.5*zoom_level+18)))
        highlights.append((960-w//2,540-h//2,w,h,at_s))
    return zexpr,xexpr,yexpr,highlights


def render_mp4(recording, subtitles, output, *, ffmpeg_path=None, timeline_events=None,
               title='', disclaimer='', subtitle_cues=None):
    """Render a reviewable 1080p/30 lesson with action-timed focus, captions and bookends."""
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
    title_file=output.with_name(output.stem+'.title.txt');outro_file=output.with_name(output.stem+'.outro.txt')
    title_file.write_text(str(title or 'Təlimat videosu')[:180],encoding='utf-8')
    outro_file.write_text(str(disclaimer or 'Nümunə tamamlandı')[:240],encoding='utf-8')
    z,x,y,highlights=_zoom_expressions(timeline_events)
    subtitle_path=_filter_path(subtitles);title_path=_filter_path(title_file);outro_path=_filter_path(outro_file)
    duration_seconds=None
    probe=Path(shutil.which('ffprobe') or Path(ffmpeg).with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe'))
    if probe.is_file():
        try:
            measured=subprocess.run([str(probe),'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(recording)],capture_output=True,text=True,timeout=10,check=False)
            parsed=float(measured.stdout.strip())
            if measured.returncode==0 and 0<parsed<=600:duration_seconds=parsed
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    focus_filters=[]
    for bx,by,bw,bh,at_s in highlights:
        focus_filters.append(f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=24B8D8@0.72:t=3:enable='between(t,{at_s:.3f},{at_s+.65:.3f})'")
    cue_filters=[]
    for index,cue in enumerate(subtitle_cues or [],1):
        try:start=max(0,int(cue['start_ms'])/1000);end=max(start+.1,int(cue['end_ms'])/1000)
        except (KeyError,TypeError,ValueError):continue
        cue_filters.append(f"drawtext=font='sans':text='{index:02d} / {len(subtitle_cues):02d}':fontcolor=0x74DDF5:fontsize=22:x=72:y=916:enable='between(t,{start:.3f},{end:.3f})'")
    video_filters=['fps=30','scale=1920:1080:flags=lanczos',
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s=1920x1080:fps=30"]
    if duration_seconds:
        video_filters.append(f"drawbox=x=0:y=0:w='max(1,iw*min(1,t/{duration_seconds:.3f}))':h=5:color=24B8D8@0.95:t=fill")
    video_filters.extend(['drawbox=x=0:y=900:w=iw:h=180:color=0B1727@0.96:t=fill',*focus_filters,*cue_filters,
        f"subtitles=filename='{subtitle_path}':force_style='FontName=Arial,FontSize=34,PrimaryColour=&H00FFFFFF,OutlineColour=&H00202B35,BorderStyle=3,BackColour=&HAA0B1727,Outline=1,Shadow=0,Alignment=2,MarginV=72,WrapStyle=2'",
        'format=yuv420p'])
    filters=','.join(video_filters)
    graph=(f"color=c=0B1B2B:s=1920x1080:r=30:d=4,drawbox=x=480:y=492:w=960:h=5:color=24B8D8:t=fill,"
           f"drawtext=font='sans':textfile='{title_path}':fontcolor=white:fontsize=58:x=(w-text_w)/2:y=410[intro];"
           f"[0:v]{filters}[main];"
           f"color=c=0B1B2B:s=1920x1080:r=30:d=4,drawbox=x=480:y=345:w=960:h=5:color=24B8D8:t=fill,"
           f"drawtext=font='sans':text='Nümunə tamamlandı':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=390,"
           f"drawtext=font='sans':textfile='{outro_path}':fontcolor=0xCFE6EF:fontsize=30:x=(w-text_w)/2:y=470[outro];"
           "[intro][main][outro]concat=n=3:v=1:a=0[out]")
    args=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y','-i',str(recording),
          '-filter_complex',graph,'-map','[out]','-an','-c:v','libx264','-preset','veryfast','-crf','21',
          '-pix_fmt','yuv420p','-r','30','-movflags','+faststart','-t','600',str(output)]
    try:
        result=subprocess.run(args,capture_output=True,text=True,timeout=240,check=False)
        if result.returncode or not output.is_file() or output.stat().st_size<1024:
            output.unlink(missing_ok=True)
            raise RuntimeError('ffmpeg_render_failed') from None
    finally:
        title_file.unlink(missing_ok=True);outro_file.unlink(missing_ok=True)
    return {'path':str(output),'size_bytes':output.stat().st_size,'mime':'video/mp4'}
