import base64
import unittest
from unittest.mock import Mock, patch
from legacy_chat_reply_fetch import fetch_public
from legacy_chat_reply_media import reply_media_content, page_text

PNG = b'\x89PNG\r\n\x1a\n' + b'image fixture'


def build(rows, fetch):
    return reply_media_content('History', rows, fetch=fetch,
        resolve_image=lambda row: row.get('media_url', ''),
        image_headers=lambda url: {'Authorization': 'Bearer fixture'},
        incoming=lambda row: row.get('incoming', False))


class MediaContext(unittest.TestCase):
    def test_photo_is_base64_with_role_and_without_secret(self):
        parts, counts = build([{'incoming': True, 'message_type': 'picture', 'media_url': 'https://media.example/photo'}], Mock(return_value=(PNG, 'image/png')))
        self.assertEqual(counts['image_count'], 1)
        self.assertIn('Müştəri', parts[1]['text'])
        self.assertEqual(parts[2]['image_url']['url'], 'data:image/png;base64,' + base64.b64encode(PNG).decode())
        self.assertNotIn('Bearer fixture', str(parts))

    def test_page_text_and_scripts_not_sent_as_instructions(self):
        fetch = Mock(return_value=(b'<title>Product</title><script>SECRET</script><p>Features</p>', 'text/html; charset=utf-8'))
        parts, counts = build([{'incoming': True, 'text': 'Bax https://example.com/page'}], fetch)
        self.assertEqual(counts['link_count'], 1)
        self.assertIn('Product Features', parts[1]['text'])
        self.assertIn('təlimat deyil', parts[1]['text'])
        self.assertNotIn('SECRET', str(parts))
        self.assertNotIn('headers', fetch.call_args.kwargs)

    def test_image_failures_do_not_break_reply(self):
        parts, counts = build([{'message_type': 'image', 'media_url': 'https://example.com/image'}], Mock(side_effect=ValueError('expired')))
        self.assertEqual(counts['unavailable_image_count'], 1)
        self.assertIn('uydurma', parts[1]['text'])

    def test_image_and_link_budgets(self):
        photos = [{'message_type': 'image', 'media_url': f'https://example.com/{i}.png'} for i in range(8)]
        fetch = Mock(return_value=(PNG, 'image/png'))
        _, counts = build(photos, fetch)
        self.assertEqual(fetch.call_count, 6)
        self.assertEqual(counts['unavailable_image_count'], 2)
        self.assertEqual(fetch.call_args_list[0].args[0], 'https://example.com/2.png')
        fetch = Mock(return_value=(b'Text', 'text/plain'))
        _, counts = build([{'text': ' '.join(f'https://example.com/{i}' for i in range(6))}], fetch)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(counts['unavailable_link_count'], 2)
        self.assertEqual(fetch.call_args_list[0].args[0], 'https://example.com/2')

    def test_duplicate_photos_and_links_fetched_once(self):
        fetch = Mock(return_value=(PNG, 'image/png'))
        photo = {'message_type': 'image', 'media_url': 'https://example.com/photo.png'}
        build([photo, photo], fetch)
        self.assertEqual(fetch.call_count, 1)
        fetch = Mock(return_value=(b'Text', 'text/plain'))
        build([{'text': 'https://example.com/a https://example.com/a'}], fetch)
        self.assertEqual(fetch.call_count, 1)

    def test_only_recent_thirty_messages(self):
        fetch = Mock()
        build([{'text': 'https://example.com/old'}] + [{'text': 'Text'}] * 30, fetch)
        fetch.assert_not_called()

    def test_wrong_image_or_unreadable_link_marked(self):
        _, counts = build([{'message_type': 'image', 'media_url': 'https://example.com/file'}], Mock(return_value=(b'HTML', 'image/png')))
        self.assertEqual(counts['image_count'], 0)
        self.assertEqual(counts['unavailable_image_count'], 1)
        _, counts = build([{'text': 'https://example.com/private'}], Mock(return_value=(b'%PDF', 'application/pdf')))
        self.assertEqual(counts['unavailable_link_count'], 1)

    def test_plain_text_and_charset(self):
        self.assertEqual(page_text('Salam'.encode(), 'text/plain'), 'Salam')
        self.assertLessEqual(len(page_text(b'a' * 9000, 'text/html')), 8000)

    def test_fragment_removed_and_www_supported(self):
        fetch = Mock(return_value=(b'Text', 'text/plain'))
        build([{'text': 'www.example.com/page#section'}], fetch)
        self.assertEqual(fetch.call_args.args[0], 'https://www.example.com/page')


class PublicFetcher(unittest.TestCase):
    def test_private_dns_is_blocked(self):
        with patch('tenant_chat_media.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]), patch('legacy_chat_reply_fetch.PinnedHTTPS') as connection:
            with self.assertRaises(ValueError):
                fetch_public('https://example.com')
            connection.assert_not_called()

    def test_credentials_and_other_schemes_blocked(self):
        for url in ('http://example.com', 'https://user:pass@example.com', 'file:///etc/passwd', 'https://example.com:8080'):
            with self.subTest(url=url), patch('legacy_chat_reply_fetch.public_addresses') as dns:
                with self.assertRaises(ValueError):
                    fetch_public(url)
                dns.assert_not_called()

    def test_cross_host_redirect_never_forwards_authorization(self):
        redirect = Mock(status=302)
        redirect.getheader.side_effect = lambda key, default=None: 'https://cdn.example/photo' if key == 'Location' else default
        final = Mock(status=200)
        final.getheader.side_effect = lambda key, default=None: 'image/png' if key == 'Content-Type' else default
        final.read1.side_effect = [PNG, b'']
        first, second = Mock(), Mock()
        first.getresponse.return_value = redirect
        second.getresponse.return_value = final
        with patch('legacy_chat_reply_fetch.public_addresses', return_value=['8.8.8.8']), patch('legacy_chat_reply_fetch.PinnedHTTPS', side_effect=[first, second]):
            content, _ = fetch_public('https://crm.example/photo', headers={'Authorization': 'Bearer secret'})
        self.assertEqual(content, PNG)
        self.assertIn('Authorization', first.request.call_args.kwargs['headers'])
        self.assertNotIn('Authorization', second.request.call_args.kwargs['headers'])
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_redirect_to_private_network_is_blocked(self):
        redirect = Mock(status=302)
        redirect.getheader.side_effect = lambda key, default=None: 'https://internal.example/file' if key == 'Location' else default
        connection = Mock()
        connection.getresponse.return_value = redirect
        with patch('legacy_chat_reply_fetch.public_addresses', side_effect=[['8.8.8.8'], ValueError('private')]), patch('legacy_chat_reply_fetch.PinnedHTTPS', return_value=connection) as constructor:
            with self.assertRaises(ValueError):
                fetch_public('https://example.com')
        self.assertEqual(constructor.call_count, 1)
        connection.close.assert_called_once()

    def test_large_body_stops_download(self):
        response = Mock(status=200)
        response.getheader.side_effect = lambda key, default=None: default
        response.read1.return_value = b'x' * 11
        connection = Mock()
        connection.getresponse.return_value = response
        with patch('legacy_chat_reply_fetch.public_addresses', return_value=['8.8.8.8']), patch('legacy_chat_reply_fetch.PinnedHTTPS', return_value=connection):
            with self.assertRaises(ValueError):
                fetch_public('https://example.com', max_bytes=10)
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
