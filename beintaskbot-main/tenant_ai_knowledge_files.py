"""Strict, bounded text extraction for the first knowledge-base formats."""
from io import BytesIO
from pathlib import PurePath
import re
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
    elif suffix == '.xlsx':
        # XLSX is a ZIP container. Read only its local workbook XML; external
        # links/macros are never followed or executed. Keep sheet/cell refs so
        # downstream summaries can point back to the exact source location.
        if not raw.startswith(b'PK'):
            raise TenantLinearError('XLSX faylı düzgün deyil.')
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > 2000 or sum(i.file_size for i in infos) > 20 * 1024 * 1024:
                    raise TenantLinearError('XLSX faylı açıldıqdan sonra həddən artıq böyükdür.')
                if any(info.flag_bits & 0x1 for info in infos):
                    raise TenantLinearError('Şifrələnmiş XLSX faylı açıla bilməz.')
                names = {info.filename for info in infos}
                unsupported_prefixes=('xl/drawings/','xl/charts/','xl/media/','xl/embeddings/',
                    'xl/externalLinks/','xl/pivotTables/','xl/slicers/','xl/model/',
                    'xl/comments','xl/threadedComments/')
                if any(name.startswith(unsupported_prefixes) for name in names):
                    raise TenantLinearError('XLSX faylında analiz edilməyən qrafik, şəkil və ya daxil edilmiş məlumat var.',415)
                shared = []
                if 'xl/sharedStrings.xml' in names:
                    xml = archive.read('xl/sharedStrings.xml')
                    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                        raise TenantLinearError('XLSX XML faylı təhlükəsiz deyil.')
                    root = ElementTree.fromstring(xml)
                    shared = [''.join(node.text or '' for node in entry.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'))
                              for entry in root.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si')]
                sheets = sorted((n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)),
                                key=lambda n: int(n.rsplit('sheet', 1)[1][:-4]))
                if not sheets:
                    raise TenantLinearError('XLSX faylında oxuna bilən vərəq yoxdur.')
                if len(sheets) > 200:
                    raise TenantLinearError('XLSX faylında vərəq sayı həddən artıqdır.')
                pieces, cell_count, extracted_size = [], 0, 0
                ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                for sheet_index, sheet in enumerate(sheets, 1):
                    if archive.getinfo(sheet).file_size > 10 * 1024 * 1024:
                        raise TenantLinearError('XLSX vərəqi həddən artıq böyükdür.')
                    xml = archive.read(sheet)
                    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                        raise TenantLinearError('XLSX XML faylı təhlükəsiz deyil.')
                    root = ElementTree.fromstring(xml)
                    for cell in root.iter(ns + 'c'):
                        cell_count += 1
                        if cell_count > 20000:
                            raise TenantLinearError('XLSX xanasının sayı həddən artıqdır.')
                        if cell.find(ns + 'f') is not None:
                            raise TenantLinearError('XLSX düsturları dəstəklənmir; hesablanmış dəyərləri ixrac edin.',415)
                        value_node = cell.find(ns + 'v')
                        value = value_node.text if value_node is not None else ''
                        if cell.attrib.get('t') == 's' and value:
                            value = shared[int(value)]
                        elif cell.attrib.get('t') == 'inlineStr':
                            value = ''.join(node.text or '' for node in cell.iter(ns + 't'))
                        if value:
                            piece = f"[Sheet {sheet_index}, cell {cell.attrib.get('r', '?')}] {value}"
                            extracted_size += len(piece)
                            if extracted_size > MAX_EXTRACTED_TEXT:
                                raise TenantLinearError('XLSX-dən çıxarılan mətn həddən artıq böyükdür.')
                            pieces.append(piece)
                text = '\n'.join(pieces)
        except TenantLinearError:
            raise
        except Exception:
            raise TenantLinearError('XLSX cədvəlini oxumaq mümkün olmadı.') from None
    else:
        raise TenantLinearError('Yalnız TXT, PDF, DOCX və XLSX yükləmək olar.')
    text = text.strip()
    if not text:
        raise TenantLinearError('Faylda çıxarıla bilən mətn yoxdur; skan edilmiş PDF üçün OCR tələb olunur.')
    if len(text) > MAX_EXTRACTED_TEXT:
        raise TenantLinearError('Fayldan çıxarılan mətn həddən artıq böyükdür.')
    return name[:180], suffix[1:], text
