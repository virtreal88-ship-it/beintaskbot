from pathlib import Path


def test_historical_duplicate_audit_is_read_only_and_channel_scoped():
    source=Path(__file__).parents[1].joinpath('scripts','audit_connect_direct_duplicates.py').read_text(encoding='utf-8')
    assert 'SET TRANSACTION READ ONLY' in source
    assert 'connect_channel_id::text=p.channel_id' in source
    assert 'duplicate_requires_review' in source
    assert 'DELETE ' not in source and 'UPDATE ' not in source and 'INSERT ' not in source
