"""Formatter contract for existing dialogue attachment cards; no browser or media I/O."""
import re
import subprocess
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_media_size_label_is_compact_localized_and_never_guesses_zero():
    source=(ROOT/'docs'/'tenant-operator-dialogs.js').read_text(encoding='utf-8')
    match=re.search(r'(function mediaSize\(value\)\{[^\n]+\})',source)
    assert match, 'media size formatter is missing'
    script='''
const mediaSize=new Function('t', %r+'; return mediaSize;')((key)=>({mediaSizeUnknown:'Размер неизвестен'}[key]||key));
const result={known:mediaSize(1048576),byteBoundary:mediaSize(1024),zero:mediaSize(0),malformed:mediaSize('bad'),overflow:mediaSize('9007199254740992')};
if(JSON.stringify(result)!==JSON.stringify({known:'1.0 MB',byteBoundary:'1 KB',zero:'Размер неизвестен',malformed:'Размер неизвестен',overflow:'Размер неизвестен'}))process.exit(2);
''' % match.group(1)
    completed=subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True,check=False)
    assert completed.returncode==0, completed.stderr
    i18n=(ROOT/'docs'/'tenant-operator-i18n.js').read_text(encoding='utf-8')
    assert "mediaSizeUnknown:['Размер неизвестен','Ölçü məlum deyil','Size unknown']" in i18n
