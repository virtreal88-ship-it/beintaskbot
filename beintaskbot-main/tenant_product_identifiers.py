"""Conservative product-label candidates for private, exact-source AI context.

No media I/O, caption parsing, redactor overrides or confirmed business facts.
The caller must already be authorized to read the exact receipt. Cached artifacts
are server-produced metadata, never accepted from a client or model JSON object.
"""
import hashlib
import json
import re
import uuid

VERSION = 'product-label-block-v1'
PRODUCER_RULE = (' For a single product image only, when exactly one printed packaging barcode and every digit '
    'are clearly visible, return ONLY this optional block: first line "Product packaging observed."; '
    'optional lines Product name: "<visible name>" and Product size: "<visible size>" using JSON-quoted '
    'strings in their original script; last line "Barcode on product packaging: <literal digits>." '
    'Use these exact English field names and no other prose with this block. Copy digits without '
    'repair, inference or checksum completion. Omit the block for ambiguity, unreadable codes, multiple '
    'products, screenshots, phone/contact numbers or remote IDs; otherwise use ordinary observations. '
    'The block is an unconfirmed visual candidate, never a diagnosis.')
_FORMATS = {8: 'EAN-8', 12: 'UPC-A', 13: 'EAN-13'}
_LABEL = re.compile(r'\b(?:(?P<label>EAN(?:[- ]?(?:8|13))?|UPC(?:[- ]?A)?)\s*(?:barcode)?|barcode)\s+on\s+(?:the\s+)?(?:product packaging(?: label)?|(?:bottle|carton|packet|package|can|jar) label)\s*[:=#-]\s*(?P<value>[0-9][0-9 -]{6,24}[0-9])\b', re.I)
_UNSAFE = re.compile(r'\b(?:anydesk|teamviewer|remote|desktop|screenshot|screen|window|your address|address|session|credential|password|secret|token|api.key|customer text|chat|message says|quoted|unreadable|illegible|blurred|blurry|obscured|partial|guess|possibly|maybe|uncertain|ambiguous|not visible|no product|no packaging|not product|not packaging|not on|cannot read|can.t read)\b', re.I)
_CONTACT = re.compile(r'\b(?:phone|telephone|mobile|contact|tel|fax|whatsapp|id)\b', re.I)
_OTHER_OR_NEGATED = re.compile(r'\b(?:not|no|without|neither|example|sample|hypothetical|separate|badge|hotline|support|invoice|receipt|serial|computer|monitor|webpage|drawing|illustration|loyalty|card|badge|ticket|sticker)\b', re.I)
_FIELD_PREFIX = re.compile(r'(?:(?:the\s+)?(?:printed|visible|readable|clearly visible))?', re.I)


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _valid(value):
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and len(value) in _FORMATS and len(set(value))>1
            and (sum(int(d) * (3 if i % 2 == 0 else 1)
                     for i, d in enumerate(reversed(value[:-1]))) + int(value[-1])) % 10 == 0)


def extract(text, *, kind='image', origin=None):
    """One literal on one product image, not checksum-only OCR classification.

    Fixed field grammar only; quoted product names may retain their original script.
    A separately labelled contact line is allowed, but never its number.
    """
    if kind != 'image' or origin or not isinstance(text, str) or len(text) >= 2400:
        return None
    if _UNSAFE.search(text) or _OTHER_OR_NEGATED.search(text):
        return None
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if lines.count('Product packaging observed.') != 1:
        return None
    fields=set();matches=[];contacts=[]
    for line in lines:
        if line=='Product packaging observed.':
            continue
        detail=re.fullmatch(r'Product (name|size): (.+)',line)
        if detail:
            if detail[1] in fields:
                return None
            fields.add(detail[1])
            try:
                label=json.loads(detail[2])
            except (TypeError,ValueError):
                return None
            if not isinstance(label,str) or not 1<=len(label)<=160 or any(ord(c)<32 for c in label):
                return None
            # JSON escapes cannot hide a contradictory/sensitive label or a
            # second numeric identifier from the same exclusion policy.
            if (_UNSAFE.search(label) or _OTHER_OR_NEGATED.search(label) or _CONTACT.search(label)
                    or len(re.sub(r'[^0-9]','',label))>=8):
                return None
            continue
        match=_LABEL.search(line)
        if match and _FIELD_PREFIX.fullmatch(line[:match.start()].strip()) and line[match.end():].strip() in {'','.'}:
            matches.append(match)
            continue
        # A separately labelled contact value is not promoted. Headings,
        # free prose (in ANY language) and other objects fail closed.
        if re.fullmatch(r'(?:contact\s+)?(?:phone|telephone|mobile|tel|fax|whatsapp)\s*:\s*\+?[0-9][0-9 ()-]{6,20}[0-9]\.?',line,re.I):
            contacts.append(line)
            continue
        return None
    if len(matches)!=1:
        return None
    match=matches[0]
    value = re.sub(r'[ -]', '', match['value'])
    if not _valid(value):
        return None
    declared = re.sub(r'[ -]', '', (match['label'] or '').upper())
    if declared and declared not in {'EAN', 'UPC'} and declared != _FORMATS[len(value)].replace('-', ''):
        return None
    if declared == 'EAN' and len(value) not in {8, 13} or declared == 'UPC' and len(value) != 12:
        return None
    if any(value in re.sub(r'[^0-9]','',line) for line in contacts):
        return None
    if len(re.findall(re.escape(value), re.sub(r'[ -]', '', text))) != 1:
        return None
    return {'version': VERSION, 'format': _FORMATS[len(value)], 'value': value,
            'verification': 'observed_unconfirmed'}


def artifact(text, redacted_text, content_hash, *, kind='image', origin=None):
    candidate = extract(text, kind=kind, origin=origin)
    if not candidate:
        return None
    return {**candidate, 'observation_hash': _digest(redacted_text), 'image_sha256': content_hash}


def read_artifact(value, text, content_hash=None):
    if not isinstance(text, str) or not isinstance(value, dict) or set(value) != {'version', 'format', 'value', 'verification', 'observation_hash', 'image_sha256'}:
        return None
    if (value['version'] != VERSION or value['verification'] != 'observed_unconfirmed'
            or not _valid(value['value']) or value['format'] != _FORMATS[len(value['value'])]
            or value['observation_hash'] != _digest(text)
            or value['image_sha256'] is not None and not re.fullmatch('[a-f0-9]{64}', str(value['image_sha256']))
            or content_hash is not None and value['image_sha256'] != content_hash):
        return None
    # Original saved observations have exact source-version attribution, but
    # need not have a downloaded-byte hash. Never invent one. Such a candidate
    # must still be independently extractable from the surviving literal.
    if value['image_sha256'] is None and extract(text) != {key:value[key] for key in ('version','format','value','verification')}:
        return None
    return dict(value)


def _source(tenant, ref):
    from tenant_media_evidence import source_hash
    try:
        tenant = str(uuid.UUID(str(tenant)))
        channel = str(uuid.UUID(str(ref.get('channel_id'))))
    except (ValueError, TypeError, AttributeError):
        return None
    if (ref.get('media_kind') != 'image' or not ref.get('external_id')
            or not re.fullmatch('[a-f0-9]{64}', str(ref.get('media_version')))):
        return None
    return {'tenant_id': tenant, 'channel_id': channel, 'external_id': str(ref['external_id']),
            'source_hash': source_hash(ref), 'media_version': ref['media_version'], 'observation_index': 0}


def candidate(tenant, ref, saved):
    """Classify an already exact-matched receipt, or validate its private field."""
    source = _source(tenant, ref)
    if not source or not isinstance(saved, dict) or saved.get('status') != 'processed':
        return None
    coverage = saved.get('coverage') if isinstance(saved.get('coverage'),dict) else {}
    if coverage.get('complete') is not True or coverage.get('kind', 'image') != 'image':
        return None
    provenance = saved.get('provenance') if isinstance(saved.get('provenance'),dict) else {}
    if provenance.get('source_version', source['source_hash']) != source['source_hash']:
        return None
    result = saved.get('result') if isinstance(saved.get('result'),dict) else {}
    observations = result.get('observations',[])
    if not isinstance(observations,list):
        return None
    observations = list(observations)
    if result.get('observation'):
        observations.append({'text': result['observation']})
    if len(observations) != 1 or not isinstance(observations[0], dict) or observations[0].get('origin'):
        return None
    text = observations[0].get('text')
    if not isinstance(text, str):
        return None
    retained = result.get('product_barcode_candidate') or result.get('_product_barcode_candidate')
    if retained is not None:
        if not isinstance(retained, dict) or retained.get('source') != source:
            return None
        found = read_artifact(retained.get('artifact'), text)
        return {'source': source, 'artifact': found} if found else None
    found = artifact(text, text, None)
    return {'source': source, 'artifact': found} if found else None


def bind_artifact(tenant, ref, value, text, origin=None):
    source = _source(tenant, ref)
    found = read_artifact(value, text) if isinstance(text, str) and not origin else None
    return {'source': source, 'artifact': found} if source and found else None


def context(tenant, ref, saved):
    found = candidate(tenant, ref, saved)
    if not found:
        return ''
    item = found['artifact']
    return ('\n[Product-packaging barcode candidate from this exact image only; observed, unconfirmed; '
            'use only if the current question refers to this product; confirm only this field if needed; '
            'not proof of product registration or any diagnosis] ' + item['format'] + ': ' + item['value'])
