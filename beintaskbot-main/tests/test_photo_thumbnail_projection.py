"""Only real, bounded current-message thumbnails leave the read projection."""
import base64
import io
from PIL import Image
from tenant_operator_dialogs import _media_thumbnail,_message


def encoded(size=(16,12)):
    output=io.BytesIO()
    Image.new('RGB',size,'blue').save(output,format='PNG')
    return base64.b64encode(output.getvalue()).decode()


def test_real_thumbnail_is_reencoded_and_projected_only_for_photo():
    raw={'jpegThumbnail':encoded(),'mediaId':'image-one','secret':'not-projected'}
    value=_message({'id':'row','external_id':'connect:photo','channel':'connect:channel','direction':'incoming','message_type':'image','body':'','raw':raw})
    result=value['media_thumbnail']
    assert result.startswith('data:image/jpeg;base64,') and len(result)<=24023
    assert 'raw' not in value and 'secret' not in repr(value)
    assert 'media_thumbnail' not in _message({'channel':'connect:channel','message_type':'audio','raw':raw})


def test_urls_svg_corrupt_oversized_and_quoted_thumbnails_are_never_used():
    for raw in ({'jpegThumbnail':'https://example.invalid/photo.jpg'},
                {'jpegThumbnail':'data:image/svg+xml;base64,'+base64.b64encode(b'<svg/>').decode()},
                {'jpegThumbnail':'not-base64'}, {'jpegThumbnail':'a'*44001},
                {'jpegThumbnail':encoded((1025,1))},
                {'quotedMessage':{'imageMessage':{'jpegThumbnail':encoded()}}},
                {'message':{'contextInfo':{'quotedMessage':{'imageMessage':{'jpegThumbnail':encoded()}}}}}):
        assert _media_thumbnail(raw)==''
    assert _media_thumbnail({'message':{'imageMessage':{'jpegThumbnail':encoded()}}})
