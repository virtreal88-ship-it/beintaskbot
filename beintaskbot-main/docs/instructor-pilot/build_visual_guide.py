"""Build AZ guide from eight distinct real captures; annotate using vectors only."""
from pathlib import Path
import base64, hashlib, json, zipfile
from xml.sax.saxutils import escape
from PIL import Image
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor, Color
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

ROOT=Path(__file__).resolve().parents[2]
SRC=Path(__file__).parent/'visual-az'
OUT=ROOT/'output/pdf';OUT.mkdir(parents=True,exist_ok=True)
FONT=Path('C:/Windows/Fonts')
pdfmetrics.registerFont(TTFont('AZ',str(FONT/'arial.ttf')))
pdfmetrics.registerFont(TTFont('AZBold',str(FONT/'arialbd.ttf')))
BLUE='#006E9C';RED='#BC3434'
# Rectangles are callouts over original pixels, never reconstructed UI.
LESSON=json.loads((Path(__file__).parent/'warehouse-transfer.lesson.json').read_text(encoding='utf-8'))
STEPS=[(s['image']['asset_id'],s['title'],s['explanation'],s['note'],
        [(a['x'],a['y'],a['width'],a['height'],RED if a['intent']=='warning' else BLUE) for a in s['areas']])
       for s in LESSON['slides']]

PDF=OUT/'beinsystems-anbar-yerdeyishme-az.pdf'
c=Canvas(str(PDF),pagesize=(720,900),pageCompression=1)
c.setTitle('BeinSystems: Anbarlar arasında yerdəyişmə - vizual təlimat')
c.setAuthor('Beintaskbot - demo UI pilot')
style=ParagraphStyle('body',fontName='AZ',fontSize=14,leading=21,textColor=HexColor('#253746'))
small=ParagraphStyle('note',fontName='AZ',fontSize=12,leading=18,textColor=HexColor('#253746'))
manifest=[]
for i,(name,title,body,note,rects) in enumerate(STEPS,1):
    source=SRC/name;im=Image.open(source);w,h=im.size
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    manifest.append({'step':i,'capture':name,'sha256':digest,'size':[w,h],'rectangles':rects})
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
         f'<title>{escape(str(i)+". "+title)}</title>',
         f'<image width="{w}" height="{h}" xlink:href="data:image/jpeg;base64,{base64.b64encode(source.read_bytes()).decode()}"/>']
    for x,y,rw,rh,color in rects:
        svg.append(f'<rect x="{x}" y="{y}" width="{rw}" height="{rh}" rx="4" fill="none" stroke="{color}" stroke-width="3"/>')
    svg.append('</svg>');(SRC/(source.stem+'-annotated.svg')).write_text(''.join(svg),encoding='utf-8')
    c.setFillColor(HexColor('#123349'));c.rect(0,852,720,48,fill=1,stroke=0)
    c.setFillColor(HexColor('#FFFFFF'));c.setFont('AZBold',12);c.drawString(36,870,'BEINSYSTEMS  /  ANBARLAR ARASINDA YERDƏYİŞMƏ')
    c.setFillColor(HexColor('#006E9C'));c.setFont('AZBold',11);c.drawString(36,831,f'ADDIM {i:02d} / 08    •    BEIN DEMO    •    18.10.2026')
    head=Paragraph(f'{i}. {escape(title)}',ParagraphStyle('head',fontName='AZBold',fontSize=23,leading=29,textColor=HexColor('#123349')))
    _,hh=head.wrap(648,100);head.drawOn(c,36,806-hh)
    para=Paragraph(escape(body),style);_,ph=para.wrap(648,120);para.drawOn(c,36,767-ph)
    scale=min(648/w,492/h);dw,dh=w*scale,h*scale;ix=36+(648-dw)/2;iy=203
    c.drawImage(str(source),ix,iy,width=dw,height=dh,mask='auto')
    for x,y,rw,rh,color in rects:
        c.setStrokeColor(HexColor(color));c.setLineWidth(2.2)
        c.roundRect(ix+x*scale,iy+(h-y-rh)*scale,rw*scale,rh*scale,3,fill=0,stroke=1)
    c.setFillColor(HexColor('#EFF6F9'));c.roundRect(36,66,648,115,8,fill=1,stroke=0)
    para=Paragraph(escape(note),small);_,nh=para.wrap(616,100);para.drawOn(c,52,164-nh)
    c.setFillColor(HexColor('#526571'));c.setFont('AZ',10)
    c.drawString(36,37,'Saxlamadan nümayiş  |  dev.akul.az  |  İnterfeys versiyası göstərilmirdi')
    c.drawRightString(684,37,str(i));c.showPage()
c.save()
(SRC/'capture-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
with zipfile.ZipFile(OUT/'beinsystems-transfer-screenshots.zip','w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(SRC.iterdir()):
        if f.suffix in {'.jpg','.svg','.json'}:z.write(f,f.name)
print(PDF)
