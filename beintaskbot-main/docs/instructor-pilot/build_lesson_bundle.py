"""Offline self-contained demo lesson, with no remote assets or customer route."""
from pathlib import Path
import json,base64
ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).parent
lesson=json.loads((BASE/'warehouse-transfer.lesson.json').read_text(encoding='utf-8'))
assets={s['image']['asset_id']:base64.b64encode((BASE/'visual-az'/s['image']['asset_id']).read_bytes()).decode() for s in lesson['slides']}
narration=json.loads((ROOT/'output/lessons/audio/narration.json').read_text(encoding='utf-8'))
audios={row['asset_id']:base64.b64encode((ROOT/'output/lessons/audio'/row['asset_id']).read_bytes()).decode() for row in narration['slides']}

def js(value):return json.dumps(value,ensure_ascii=False).replace('<','\\u003c')
html='<!doctype html><html lang="az"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \x27none\x27; img-src blob:; media-src blob:; style-src \x27unsafe-inline\x27; script-src \x27unsafe-inline\x27; connect-src \x27none\x27; base-uri \x27none\x27; form-action \x27none\x27"><title>Bein Demo — Anbarlar arasında yerdəyişmə</title><style>'+(ROOT/'docs/tenant-instructor-lesson.css').read_text(encoding='utf-8')+'</style><main id="lesson"></main><script>'+(ROOT/'docs/tenant-instructor-lesson.js').read_text(encoding='utf-8')+'</script><script>const lesson='+js(lesson)+';const captures='+js(assets)+';const narration='+js(narration)+';const audioCaptures='+js(audios)+';const urls=new Map();function asset(id){if(!urls.has(id)){const bytes=Uint8Array.from(atob(captures[id]||audioCaptures[id]),c=>c.charCodeAt(0));urls.set(id,URL.createObjectURL(new Blob([bytes],{type:id.endsWith(".mp3")?"audio/mpeg":"image/jpeg"})));}return urls.get(id);}BeinLesson.mount(document.getElementById("lesson"),lesson,{resolveAsset:asset,narration:narration,resolveAudio:asset});window.addEventListener("pagehide",()=>{for(const u of urls.values())URL.revokeObjectURL(u);});</script></html>'
out=ROOT/'output/lessons';out.mkdir(parents=True,exist_ok=True)
(out/'beinsystems-warehouse-transfer-az.html').write_text(html,encoding='utf-8')
print('Built offline lesson with',len(assets),'real captures')
