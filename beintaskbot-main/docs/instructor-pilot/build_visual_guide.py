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
STEPS=[
('01-navigation.jpg','Yerdəyişmələr bölməsini açın',
 'Əvvəlcə sağ yuxarıda Bein Demo şirkətini yoxlayın. Məhsullar bölməsində Yerdəyişmələr alt bölməsini açın. Sonra + Yerdəyişmə düyməsini seçin.',
 'Mavi çərçivələr keçidi göstərir: Məhsullar → Yerdəyişmələr → + Yerdəyişmə. Mövcud sənədləri açmaq və dəyişmək lazım deyil.',
 [(230,1,96,57,BLUE),(362,60,137,49,BLUE),(187,140,115,33,BLUE),(695,10,105,27,BLUE)]),
('02-new-form.jpg','Yeni sənədin formasını yoxlayın',
 'Yeni Yerdəyişmə forması açılır. Yerdəyişmə № və Tarix sahələrini yoxlayın. Bu nümunədə sistemin verdiyi nömrə və tarix dəyişdirilməyib.',
 'Anbardan və Anbara ulduzla işarələnmiş seçim sahələridir. Yadda saxla düyməsini bu təlim nümunəsində basmayın.',
 [(17,98,204,80,BLUE),(316,98,207,79,BLUE)]),
('03-source-warehouse.jpg','Göndərən anbarı seçin',
 'Anbardan sahəsini açın və məhsulun göndəriləcəyi anbarı seçin. Bu demo nümunəsində Test Anbar seçilib.',
 'Seçimdən sonra anbarın adı sahədə görünməlidir. Yanındakı + düyməsi bu ssenari üçün lazım deyil; yeni anbar yaratmayın.',
 [(317,98,182,30,BLUE)]),
('04-destination-warehouse.jpg','Qəbul edən anbarı seçin',
 'Anbara sahəsində məhsulun qəbul ediləcəyi anbarı seçin. Nümunə: test üçün. Seçilmiş adın həqiqətən sahədə göründüyünü yoxlayın.',
 'Göndərən və qəbul edən anbarlar fərqli olmalıdır: Test Anbar → test üçün. Bu mərhələdə hələ heç bir yerdəyişmə saxlanılmır.',
 [(316,49,184,29,BLUE),(316,97,184,29,BLUE)]),
('05-product-search.jpg','Test məhsulunu tapın və əlavə edin',
 'Məhsul əlavə et sahəsində ad, barkod və ya artkod üzrə axtarın. Nümunədə Beta TEST yazılıb; nəticələrdən Beta TEST Sud seçilib.',
 'Axtarış nəticəsində 0 əd görünürdü. Bu nümunə yalnız formanı göstərir: real əməliyyatdan əvvəl göndərən anbardakı mövcud qalığı ayrıca yoxlayın.',
 [(74,366,188,36,BLUE),(42,407,285,79,BLUE)]),
('06-quantity.jpg','Miqdarı daxil edin',
 'Əlavə edilmiş məhsul sətrində Miqdar xanasını seçin və lazım olan sayını yazın. Nümunədə 2 daxil edilib. Tab ilə xanadan çıxın.',
 'Ekranın aşağı hissəsində Miqdar: 2 əd göstərilməsi yoxlanılıb. Bu, forma üzrə cəmdir; anbar qalığının dəyişdiyini göstərmir.',
 [(603,314,58,38,BLUE),(615,468,125,30,BLUE)]),
('07-final-check.jpg','Saxlamadan əvvəl məlumatları yoxlayın',
 'İki anbarı, məhsulu, miqdarı və tarixi yenidən yoxlayın. Digər bölməsində Status, Cavabdeh, Şöbə və Təsdiqlənib sahələri görünür.',
 'Təsdiqlənib nümunədə əvvəlcədən işarəli idi və dəyişdirilmədi. Saxlamanın və bu işarənin anbar uçotuna təsiri yoxlanılmayıb. Qırmızı çərçivədəki Yadda saxla düyməsini basmayın.',
 [(18,9,99,35,RED),(317,49,182,78,BLUE),(609,201,86,29,RED),(122,8,61,36,BLUE)]),
('08-discard-changes.jpg','Təlimi yadda saxlamadan bağlayın',
 'Bağla düyməsini basın. Dəyişikliklər yadda saxlanılmayacaq xəbərdarlığı açılır. Dəyişiklikləri saxlamadan çıxmaq üçün Ok seçin.',
 'Bu addım real olaraq yoxlanılıb: Ok seçildikdən sonra Yerdəyişmələr siyahısına qayıdıldı. Xəbərdarlıqdakı Yadda saxla seçilməyib. Sənəd saxlanılmayıb və keçirilməyib.',
 [(298,175,278,33,BLUE),(637,237,45,36,BLUE),(188,236,101,37,RED)])
]

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
