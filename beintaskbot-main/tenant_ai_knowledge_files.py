"""Strict, bounded text extraction for the first knowledge-base formats."""
from io import BytesIO
from pathlib import PurePath
import zipfile
from xml.etree import ElementTree

from tenant_ai_agents_policy import MAX_FILE_BYTES, MAX_EXTRACTED_TEXT
from tenant_linear_policy import TenantLinearError


def extract(filename, data):
    name = PurePath(str(filename or '').replace('\\', '/')).name
    suffix = PurePath(name).suffix.lower()
    raw = bytes(data)
    if not name or len(raw) > MAX_FILE_BYTES:
        raise TenantLinearError('Fayl ölçüsü 5 MB-dan çox ola bilməz.')
    if suffix == '.txt':
        try:
            text = raw.decode('utf-8-sig', errors='strict')
        except UnicodeDecodeError:
            raise TenantLinearError('TXT faylı UTF-8 kodlaşdırmasında olmalıdır.') from None
    elif suffix == '.pdf':
        if not raw.startswith(b'%PDF-'):
            raise TenantLinearError('PDF faylı düzgün deyil.')
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw), strict=True)
            if reader.is_encrypted:
                raise TenantLinearError('Şifrələnmiş PDF faylı açıla bilməz.')
            if len(reader.pages) > 100:
                raise TenantLinearError('PDF faylı 100 səhifədən çox ola bilməz.')
            pieces, size = [], 0
            for page in reader.pages:
                part = page.extract_text() or ''
                size += len(part)
                if size > MAX_EXTRACTED_TEXT:
                    raise TenantLinearError('PDF-dən çıxarılan mətn həddən artıq böyükdür.')
                pieces.append(part)
            text = '\n'.join(pieces)
        except TenantLinearError:
            raise
        except Exception:
            raise TenantLinearError('PDF mətnini oxumaq mümkün olmadı.') from None
    elif suffix == '.docx':
        if not raw.startswith(b'PK'):
            raise TenantLinearError('DOCX faylı düzgün deyil.')
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > 2000 or sum(i.file_size for i in infos) > 20 * 1024 * 1024:
                    raise TenantLinearError('DOCX faylı açıldıqdan sonra həddən artıq böyükdür.')
                document = archive.getinfo('word/document.xml')
                if document.file_size > 10 * 1024 * 1024:
                    raise TenantLinearError('DOCX sənədi həddən artıq böyükdür.')
                root = ElementTree.fromstring(archive.read(document))
                paragraphs = []
                for paragraph in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
                    value = ''.join(node.text or '' for node in paragraph.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                    if value:
                        paragraphs.append(value)
                text = '\n'.join(paragraphs)
        except TenantLinearError:
            raise
        except Exception:
            raise TenantLinearError('DOCX mətnini oxumaq mümkün olmadı.') from None
    else:
        raise TenantLinearError('Yalnız TXT, PDF və DOCX yükləmək olar.')
    text = text.strip()
    if not text:
        raise TenantLinearError('Faylda çıxarıla bilən mətn yoxdur; skan edilmiş PDF üçün OCR tələb olunur.')
    if len(text) > MAX_EXTRACTED_TEXT:
        raise TenantLinearError('Fayldan çıxarılan mətn həddən artıq böyükdür.')
    return name[:180], suffix[1:], text
