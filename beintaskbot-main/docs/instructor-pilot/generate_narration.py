"""Generate only public instructional narration; never accepts ERP/session inputs.
Requires edge-tts and mutagen (local developer tools, not production dependencies).
Run once; existing files are reused unless --regenerate is explicitly passed.
"""
from pathlib import Path
import asyncio,json,sys,hashlib
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.instructor-tts-deps'))
import edge_tts
from mutagen.mp3 import MP3
SCRIPTS=[
'Əvvəlcə demo şirkətini yoxlayın. Məhsullar bölməsini açın, Yerdəyişmələr seçin. Yeni forma üçün Yerdəyişmə düyməsinə basın.',
'Yeni yerdəyişmə formasını nəzərdən keçirin. Tarix, göndərən və qəbul edən anbar sahələrini yoxlayın. Bu təlimdə sənədi yadda saxlamırıq.',
'Anbardan sahəsini açın. Məhsulun göndəriləcəyi test anbarını seçin. Seçilmiş anbarın adı sahədə görünməlidir.',
'Anbara sahəsini açın və qəbul edən test anbarını seçin. Göndərən və qəbul edən anbarların fərqli olduğunu yoxlayın.',
'Məhsul əlavə et sahəsində test məhsulunu axtarın. Uyğun nəticəni seçin. Sıfır qalıq özlüyündə xəta deyil. Mənfi qalığa icazə şirkətin ayarlarından asılıdır.',
'Miqdar xanasına köçürüləcək sayı yazın. Nümunədə iki daxil edilir. Tab düyməsi ilə xanadan çıxın və forma üzrə cəmi yoxlayın.',
'Anbarları, məhsulu və miqdarı yenidən yoxlayın. Yadda saxlamaq və sənədi keçirmək bu təlimdə yoxlanılmayıb. Bu əməliyyatları yerinə yetirməyin.',
'Təlimi bitirmək üçün Bağla düyməsini basın. Dəyişikliklərin saxlanılmayacağı xəbərdarlığında Ok seçin. Yadda saxla seçimini basmayın. Bu nümunədə sənəd saxlanılmayıb.'
]
async def main():
 out=ROOT/'output/lessons/audio';out.mkdir(exist_ok=True)
 rows=[]
 for i,text in enumerate(SCRIPTS,1):
  path=out/f'step-{i:02}.mp3';meta=out/f'step-{i:02}.words.json'
  if not path.exists() or '--regenerate' in sys.argv:
   words=[]
   with path.open('wb') as f:
    async for chunk in edge_tts.Communicate(text,'az-AZ-BanuNeural',rate='-5%',boundary='WordBoundary').stream():
     if chunk['type']=='audio':f.write(chunk['data'])
     elif chunk['type']=='WordBoundary':words.append({k:chunk[k] for k in ('offset','duration','text')})
   meta.write_text(json.dumps(words,ensure_ascii=False),encoding='utf-8')
  words=json.loads(meta.read_text(encoding='utf-8'))
  duration=round(MP3(path).info.length*1000)
  terms=[['Məhsullar','Yerdəyişmələr','Yerdəyişmə'],[],['Anbardan'],['Anbara'],['Məhsul'],['Miqdar'],[],['Bağla']][i-1]
  cues=[]
  for term in terms:
   word=next((w for w in words if w['text'].strip('.,').casefold()==term.casefold()),None)
   cues.append(max(650,round(word['offset']/10000)-250) if word else 1400)
  rows.append({'cues':cues,'asset_id':path.name,'text':text,'duration_ms':duration,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'words':words})
  print(f'Step {i}: {duration} ms',flush=True)
 (out/'narration.json').write_text(json.dumps({'language':'az-AZ','voice':'az-AZ-BanuNeural','quality':'pilot_requires_native_speaker_review','slides':rows},ensure_ascii=False,indent=2),encoding='utf-8')
asyncio.run(main())
