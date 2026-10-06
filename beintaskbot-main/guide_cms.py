"""Authenticated guide editor and static site export. Never deploys files."""
import asyncio, base64, binascii, copy, hashlib, io, json, logging, re, threading, zipfile
from datetime import datetime, timezone
from pathlib import Path
from aiohttp import web

SLUG = re.compile(r'^/[a-z0-9][a-z0-9/-]{0,159}$')
FIELDS = ('slug', 'title', 'body', 'category', 'body_format')
logger = logging.getLogger(__name__)

def validate_images(images):
    if not isinstance(images, list) or len(images) > 5: raise ValueError('Maksimum 5 şəkil əlavə edin')
    result=[]
    for image in images:
        if not isinstance(image, dict): raise ValueError('Şəkil məlumatı düzgün deyil')
        value=image.get('data', '')
        if not isinstance(value, str) or len(value)>160000: raise ValueError('Şəkil çox böyükdür')
        try: raw=base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error): raise ValueError('Şəkil məlumatı düzgün deyil')
        if not 16 <= len(raw) <= 120000: raise ValueError('Şəkil ölçüsü düzgün deyil')
        if raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9'): mime='image/jpeg'
        elif raw.startswith(b'\x89PNG\r\n\x1a\n') and raw.endswith(b'IEND\xaeB`\x82'): mime='image/png'
        else: raise ValueError('Yalnız JPEG və PNG şəkilləri qəbul edilir')
        alt=str(image.get('alt') or '').strip()
        if len(alt)>180: raise ValueError('Şəkil təsviri maksimum 180 simvol olmalıdır')
        result.append({'data':value,'mime':mime,'alt':alt})
    return result

def validate_article(data):
    if not isinstance(data, dict): raise ValueError('Məqalə məlumatları düzgün deyil')
    item = {key: str(data.get(key) or '').strip() for key in FIELDS}
    if not SLUG.fullmatch(item['slug']) or '//' in item['slug'] or item['slug'].endswith('/'):
        raise ValueError('Ünvanı /products/inventory nümunəsində yazın')
    if not 1 <= len(item['title']) <= 180: raise ValueError('Başlıq tələb olunur (maksimum 180 simvol)')
    if not 1 <= len(item['body']) <= 60000: raise ValueError('Məqalənin mətni tələb olunur (maksimum 60000 simvol)')
    if len(item['category']) > 100: raise ValueError('Bölmə adı çox uzundur')
    item['body_format']='html' if data.get('body_format')=='html' else 'text'
    item['images']=validate_images(data.get('images',[]))
    return item

def export_zip(base_dir, published):
    base = Path(base_dir) / 'guide_site'
    payload = {'items': []}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        written=set()
        for row in published:
            item={k:row.get(k,'') for k in FIELDS};item['images']=[]
            for image in validate_images(row.get('images',[])):
                raw=base64.b64decode(image['data']);extension='jpg' if image['mime']=='image/jpeg' else 'png'
                name='images/articles/'+hashlib.sha256(raw).hexdigest()+'.'+extension
                if name not in written: archive.writestr(name,raw);written.add(name)
                item['images'].append({'src':'/'+name,'alt':image['alt']})
            payload['items'].append(item)
        archive.writestr('guide-articles.json', json.dumps(payload, ensure_ascii=False))
        archive.write(base / 'guide-articles.js', 'guide-articles.js')
        archive.write(base / 'index.html', 'index.html')
        if (base/'guide-live.js').exists():archive.write(base/'guide-live.js','guide-live.js')
        archive.writestr('UPLOAD.txt', 'Upload these files to the root of support.akul.az. Keep existing static, images, guide.css, guide.js and .htaccess. Export contains only articles explicitly marked ready. Nothing is uploaded automatically.\n')
    return output.getvalue()

def public_projection(doc):
    items=[]
    for row in doc.get('items',[]):
        published=row.get('published')
        if not published: continue
        item={key:published.get(key,'') for key in FIELDS};item['images']=[]
        for photo in validate_images(published.get('images',[])):
            raw=base64.b64decode(photo['data']);extension='jpg' if photo['mime']=='image/jpeg' else 'png'
            item['images'].append({'src':'/api/guides/public/images/'+hashlib.sha256(raw).hexdigest()+'.'+extension,'alt':photo['alt']})
        items.append(item)
    return {'success':True,'items':items}

class GuideCMS:
    def __init__(self, base_dir, allowed, storage):
        self.base = Path(base_dir)
        self.allowed = allowed
        self.storage = storage
        self.lock = threading.Lock()
        self.filename = 'guide_articles.json'

    def seed(self):
        return json.loads((self.base / 'guide_site' / 'seed.json').read_text(encoding='utf-8'))

    def read(self):
        doc = self.storage._load_file(self.filename)
        if not doc: return {'version': 0, 'items': self.seed()}
        if not isinstance(doc, dict) or not isinstance(doc.get('items'), list):
            raise RuntimeError('Invalid guide storage')
        return copy.deepcopy(doc)

    def save(self, data, actor):
        item = validate_article(data)
        if data.get('action') not in ('draft', 'ready'): raise ValueError('Qaralama və ya ixraca hazır seçimini edin')
        with self.lock:
            doc = self.read()
            if data.get('version') != doc.get('version', 0): raise FileExistsError('Məqalələr dəyişib. Saxlamazdan əvvəl səhifəni yeniləyin.')
            old = next((row for row in doc['items'] if row['slug'] == item['slug']), None)
            if old is None:
                old = {'slug': item['slug'], 'published': None}
                doc['items'].append(old)
            old['draft'] = item
            if data['action'] == 'ready': old['published'] = copy.deepcopy(item)
            old['updated_at'] = datetime.now(timezone.utc).isoformat()
            old['updated_by'] = actor
            doc['version'] = doc.get('version', 0) + 1
            with self.storage._lock:
                previous = copy.deepcopy(self.storage._cache.get(self.filename, {}))
                self.storage._cache[self.filename] = doc
            if not self.storage._save_file(self.filename):
                with self.storage._lock: self.storage._cache[self.filename] = previous
                raise RuntimeError('Saxlama alınmadı; dəyişikliklər yadda saxlanılmadı')
            return doc

    async def api(self, request):
        actor = int(request.get('authenticated_chat_id') or 0)
        if not actor or not self.allowed(actor): return web.json_response({'success': False, 'error': 'Təlimatları redaktə etmək icazəniz yoxdur'}, status=403)
        try:
            if request.method == 'GET': doc = await asyncio.to_thread(self.read)
            else: doc = await asyncio.to_thread(self.save, await request.json(), actor)
            return web.json_response({'success': True, **doc}, headers={'Cache-Control': 'no-store'})
        except FileExistsError as exc: return web.json_response({'success': False, 'error': str(exc)}, status=409)
        except web.HTTPRequestEntityTooLarge: return web.json_response({'success': False, 'error':'Şəkillərin ümumi ölçüsü çox böyükdür'},status=413)
        except (ValueError, json.JSONDecodeError) as exc: return web.json_response({'success': False, 'error': str(exc)}, status=400)
        except Exception:
            logger.exception('Guide editor storage operation failed')
            return web.json_response({'success': False, 'error': 'Təlimat yaddaşı hazırda əlçatan deyil'}, status=503)

    async def export(self, request):
        actor = int(request.get('authenticated_chat_id') or 0)
        if not actor or not self.allowed(actor): return web.json_response({'success': False, 'error': 'Təlimatları redaktə etmək icazəniz yoxdur'}, status=403)
        try:
            doc = await asyncio.to_thread(self.read)
            articles = [row['published'] for row in doc['items'] if row.get('published')]
            body = await asyncio.to_thread(export_zip, self.base, articles)
            return web.Response(body=body, content_type='application/zip', headers={'Content-Disposition': 'attachment; filename="support-articles.zip"', 'Cache-Control': 'no-store'})
        except Exception: return web.json_response({'success': False, 'error': 'İxrac hazırda əlçatan deyil'}, status=503)

    async def page(self, request):
        return web.FileResponse(self.base / 'docs' / 'guide-editor.html', headers={'Cache-Control': 'no-store'})

    async def public_feed(self, request):
        try:
            doc=await asyncio.to_thread(self.read)
            return web.json_response(public_projection(doc),headers={'Access-Control-Allow-Origin':'*','Cache-Control':'public, max-age=30'})
        except Exception:
            logger.exception('Public guide feed load failed')
            return web.json_response({'success':False,'error':'Təlimatlar hazırda əlçatan deyil'},status=503,headers={'Access-Control-Allow-Origin':'*'})

    async def public_image(self, request):
        filename=request.match_info['filename']
        if not re.fullmatch(r'[a-f0-9]{64}\.(jpg|png)',filename):raise web.HTTPNotFound()
        try:
            doc=await asyncio.to_thread(self.read)
            for row in doc['items']:
                if not row.get('published'):continue
                for photo in validate_images(row['published'].get('images',[])):
                    raw=base64.b64decode(photo['data']);extension='jpg' if photo['mime']=='image/jpeg' else 'png'
                    if hashlib.sha256(raw).hexdigest()+'.'+extension==filename:
                        return web.Response(body=raw,content_type=photo['mime'],headers={'Access-Control-Allow-Origin':'*','Cache-Control':'public, max-age=86400','X-Content-Type-Options':'nosniff'})
        except Exception:raise web.HTTPServiceUnavailable()
        raise web.HTTPNotFound()

def register_guide_cms(app, base_dir, allowed, storage):
    cms = GuideCMS(base_dir, allowed, storage)
    app.router.add_get('/guide-editor', cms.page)
    app.router.add_get('/api/guides', cms.api)
    app.router.add_post('/api/guides', cms.api)
    app.router.add_get('/api/guides/export', cms.export)
    app.router.add_get('/api/guides/public',cms.public_feed)
    app.router.add_get('/api/guides/public/images/{filename}',cms.public_image)
    return cms
