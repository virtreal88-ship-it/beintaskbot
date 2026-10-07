"""Read-only history/media regressions; never contact Kommo."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

TREE = ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))


def function(name, namespace):
    node = next(item for item in TREE.body if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)) and item.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<history>', 'exec'),namespace)
    return namespace[name]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.messages=Mock(return_value=([{'id':1,'created_at':1}],False,False))
        self.events=Mock(return_value=([{'id':2,'created_at':2,'text':'Older history'}],False))
        self.ns={'_lead_contact_ids':lambda lead:[], '_fetch_talks':lambda *args,**kw:[{'id':8,'updated_at':2},{'id':9,'updated_at':1}],
                 '_talk_id_of':lambda row:row['id'],'_talk_is_whatsapp_business':lambda row:False,
                 '_lead_open_talk':{},'_talk_channel_key':lambda row:'whatsapp',
                 '_fetch_talk_messages':self.messages,'_format_chat_message':lambda row,origin:row,
                 '_talk_chat_id':lambda row:'chat','_fetch_chat_history_by_chat_id':Mock(return_value=[]),
                 '_fetch_chat_events':self.events,'_fetch_entity_notes':Mock(return_value=[]),
                 '_fetch_entity_files_as_chat':Mock(return_value=[]),'_voice_urls':{}}
        self.history=function('_kommo_history_rows',self.ns)

    def test_sparse_talk_is_supplemented_and_newest_gets_second_page(self):
        rows=self.history(7,lead={})
        self.assertEqual([row['id'] for row in rows],[1,2])
        self.events.assert_called_once_with(7,[])
        self.assertEqual([call.kwargs['pages'] for call in self.messages.call_args_list],[2,1])

    def test_full_history_does_not_trigger_expensive_fallback_and_stays_bounded(self):
        self.messages.return_value=([{'id':i,'created_at':i} for i in range(160)],False,False)
        rows=self.history(7,lead={})
        self.assertEqual(len(rows),120)
        self.events.assert_not_called()
        self.assertEqual(rows[-1]['id'],159)


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def test_expired_drive_url_is_refreshed_by_file_uuid(self):
        failed=SimpleNamespace(status_code=404)
        downloaded=SimpleNamespace(status_code=200,content=b'image',headers={'Content-Type':'image/jpeg'})
        get=Mock(side_effect=[failed,failed,downloaded])
        request=SimpleNamespace(rel_url=SimpleNamespace(query={'src':'https://old/image','uuid':'file-id','k':'valid'}))
        resolve=Mock(return_value='https://fresh/image')
        ns={'web':SimpleNamespace(Request=object,Response=lambda **kw:kw),'unquote':lambda value:value,
            'parse_deal_share_token':lambda token:True,'_is_allowed_media_url':lambda src:True,
            '_is_allowed_kommo_media_url':lambda src:False,'requests':SimpleNamespace(get=get),
            'KOMMO_TOKEN':'not-used','_drive_file_download_url':resolve,'_sniff_media_type':lambda *args:'image/jpeg',
            '_is_ogg_bytes':lambda data:False,'_media_bytes_response':lambda req,data,mime:(data,mime),'logger':Mock()}
        response=function('_deal_file_response',ns)(request)
        self.assertEqual(response,(b'image','image/jpeg'))
        resolve.assert_called_once_with('file-id')
        self.assertEqual(get.call_args.args[0],'https://fresh/image')

    async def test_file_io_runs_off_event_loop_in_bounded_pool(self):
        loop_thread=__import__('threading').get_ident()
        def read(request):
            self.assertNotEqual(__import__('threading').get_ident(),loop_thread)
            return request
        with ThreadPoolExecutor(max_workers=1) as pool:
            handler=function('handle_api_deal_file',{'asyncio':asyncio,'_media_io':pool,'_deal_file_response':read,
                                                    'web':SimpleNamespace(Request=object,Response=object)})
            self.assertEqual(await handler('request'),'request')


if __name__=='__main__':unittest.main()
