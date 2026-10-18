"""Build an actual-media browser test; click Start to grant audio playback."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HEAD='<!doctype html><meta charset="utf-8"><title>AZ voice browser regression</title><button>Start voice test</button><output style="display:block;white-space:pre-wrap">Ready</output><iframe title="Narrated lesson" width="360" height="700" src="beinsystems-warehouse-transfer-az.html"></iframe>'
script=(Path(__file__).parent/'instructor_voice_browser_test.js').read_text(encoding='utf-8')
(ROOT/'output/lessons/voice-browser-test.html').write_text(HEAD+'<script>'+script+'</script>',encoding='utf-8')
