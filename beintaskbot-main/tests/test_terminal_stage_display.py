"""Terminal stage labels and incoming suppression without Kommo or Telegram."""
import ast
from pathlib import Path
import re
import threading
import unittest
from crm_stage_labels import stage_display_name

ROOT=Path(__file__).resolve().parents[1]


class TerminalStageTests(unittest.TestCase):
    def setUp(self):
        tree=ast.parse((ROOT/'bot.py').read_text(encoding='utf-8-sig'))
        names={'_is_successful_deal','_stage_key_from_kommo','_stage_maps_from_statuses','_inbox_pulse_payload'}
        tree.body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
        self.ns={'stage_display_name':stage_display_name,'_fold_stage_name':lambda value:str(value).lower(),'re':re,
            '_personal_overview_cache':{},'_inbox_pulse_lock':threading.Lock(),'_inbox_pulse_rev':2,
            '_inbox_pulse_events':[],'is_admin':lambda user:True,'get_funnel_owner':lambda user:{'pipeline_id':1},
            '_pulse_event_visible':lambda *args,**kwargs:True,'_get_user_seen_map':lambda user:{},
            '_has_terminal_reentry':lambda lead:True}
        exec(compile(tree,'<terminal-stages>','exec'),self.ns)

    def test_terminal_names_are_azerbaijani_for_numeric_or_string_ids(self):
        for status in (142,'142'):self.assertEqual(stage_display_name(status,'Успешно реализовано'),'Uğurla tamamlandı')
        for status in (143,'143'):self.assertEqual(stage_display_name(status,'Закрыто и не реализовано'),'İmtina olundu')
        self.assertEqual(stage_display_name(777,'Danışıqlar'),'Danışıqlar')

    def test_imported_pipeline_names_are_localized_without_changing_ids(self):
        stages,names,ui=self.ns['_stage_maps_from_statuses']([
            {'id':142,'name':'Успешно реализовано','sort':2},
            {'id':143,'name':'Закрыто и не реализовано','sort':3},
            {'id':555,'name':'Danışıqlar','sort':1}])
        self.assertEqual(stages['ugurlu'],142);self.assertEqual(stages['imtina'],143)
        self.assertEqual(names[142],'Uğurla tamamlandı');self.assertEqual(names[143],'İmtina olundu')
        self.assertIn(('ugurlu','Uğurla tamamlandı'),ui)

    def test_successful_incoming_is_suppressed_even_without_viewer_cache(self):
        self.ns['_inbox_pulse_events']=[{'rev':2,'lead_id':12,'status_id':142,'last_client_message':'Salam','last_incoming_at':10}]
        body=self.ns['_inbox_pulse_payload'](20,0)
        self.assertEqual(body['chats'],[]);self.assertEqual(body['events'],[])
        self.assertEqual(body['hidden_chat_ids'],[12])

    def test_admin_filter_cannot_revive_successful_deal_from_other_funnel(self):
        self.ns['_personal_overview_cache']={99:{'deals':[{'id':12,'status_id':142}]}}
        self.ns['_inbox_pulse_events']=[{'rev':2,'lead_id':12,'last_client_message':'Salam'}]
        self.assertEqual(self.ns['_inbox_pulse_payload'](20,0)['chats'],[])

    def test_older_untyped_pulse_cannot_revive_successful_lead(self):
        self.ns['_inbox_pulse_events']=[{'rev':1,'lead_id':12,'last_client_message':'Old'},
                                      {'rev':2,'lead_id':12,'stage_key':'ugurlu','last_client_message':'New'}]
        self.assertEqual(self.ns['_inbox_pulse_payload'](20,0)['chats'],[])

    def test_declined_and_working_incoming_keep_existing_behavior(self):
        for status in (143,777):
            self.ns['_inbox_pulse_events']=[{'rev':2,'lead_id':12,'status_id':status,'last_client_message':'Salam'}]
            body=self.ns['_inbox_pulse_payload'](20,0)
            self.assertEqual(len(body['chats']),1);self.assertEqual(body['chats'][0]['status_id'],status)

    def test_personal_funnel_fallbacks_no_longer_contain_russian_terminal_labels(self):
        tree=ast.parse((ROOT/'bot.py').read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Dict) and any(
                isinstance(target,ast.Name) and target.id.endswith('STAGE_NAMES') for target in node.targets):
                mapping=ast.literal_eval(node.value)
                if 142 in mapping:self.assertEqual(mapping[142],'Uğurla tamamlandı')
                if 143 in mapping:self.assertEqual(mapping[143],'İmtina olundu')
