"""Capability adapters and queued snapshot fences; no network or provider use."""
from unittest.mock import Mock
import pytest


def test_internal_photo_adapter_prefers_selected_capability_without_mutating_shared_settings():
    from tenant_dialog_media_adapters import configuration
    source={'transcription_model':'whisper-1','audio_enabled':True,'photo_model':'legacy-photo',
            'video_model':'existing-video','video_enabled':False,'daily_limit':17}
    selected=configuration('gpt-4.1',source)
    assert selected['photo_model']=='gpt-4.1'
    assert source['photo_model']=='legacy-photo'
    for key in ('transcription_model','audio_enabled','video_model','video_enabled','daily_limit'):
        assert selected[key]==source[key]
    assert configuration('text-only-model',source)['photo_model']=='legacy-photo'
    assert 'photo_model' not in configuration('text-only-model',{})


@pytest.mark.parametrize('current',[
    {'agent_model':'different-model','agent_version':1},
    {'agent_model':'selected-model','agent_version':2},
    None,
])
def test_automatic_media_stops_before_decode_or_provider_when_agent_snapshot_changes(monkeypatch,current):
    import tenant_connect_voice as voice
    monkeypatch.setattr('tenant_ai_agent_admission.allowed_job',lambda _:True)
    monkeypatch.setattr('tenant_ai_agent_run_store.load_agent_config',lambda _:current)
    connection=Mock(side_effect=AssertionError('stale agent reached expensive work'))
    monkeypatch.setattr(voice,'_connect',connection)
    assert not voice.active({}, {'model':'selected-model','agent_version':1})
    connection.assert_not_called()
