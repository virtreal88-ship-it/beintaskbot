"""Local UI verification with synthetic data; no CRM/network/database calls.

Run: python ui/preview.py. All edits live in process memory on 127.0.0.1.
This tool is not imported or deployed by the application server.
"""
import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DOCS = Path(__file__).resolve().parents[1] / 'docs'
MODULES = dict.fromkeys(['tasks','customers','deals','hot_orders','approvals','finance','linear','news','settings','employees','integrations','ai_agents'], True)
OWNER = dict(tenant_id='11111111-1111-4111-8111-111111111111',telegram_id=1,display_name='Nizam Qasımov',tenant_name='BeinSystems · Local preview',role='owner',active=True,permissions=list(MODULES),notification_rules={},onboarding={})
MEMBERS = [OWNER,dict(OWNER,telegram_id=2,display_name='Rüfət Həsənzadə',role='manager'),dict(OWNER,telegram_id=3,display_name='Hüseyn Səfərov',role='worker')]
PIPELINES = [dict(pipeline_id='10',name='Satış',active=True,owner_telegram_id=2,settings={},stages=[dict(stage_id='11',name='Yeni müraciət',sort_order=1,settings={}),dict(stage_id='12',name='Danışıqlar',sort_order=2,settings={})])]
DEALS = [dict(kommo_lead_id=i,name=f'Sifariş #{i}',contact_name=name,phone=phone,pipeline_id=10,status_id=11 if i==101 else 12,stage_name='Yeni müraciət' if i==101 else 'Danışıqlar',last_message='Təklif haqqında məlumat almaq istəyirəm.',source_updated_at='2026-10-13T08:46:00Z') for i,name,phone in [(101,'Araz Market','994501112233'),(102,'Elvin Məmmədov','994551234567'),(103,'Dəniz Restaurant','994701234567')]]
TASKS = [dict(kommo_task_id=201,kommo_lead_id=101,text='Müştəri ilə əlaqə saxlayın və təqdimat vaxtını razılaşdırın.',completed=False,due_at='2026-10-15T10:30:00Z')]
AGENTS = [dict(id='preview-agent',name='Satış köməkçisi',enabled=False,version=1,mode='manual',model='Chat AI modeli',language='az',style='balanced',instructions='Müştərinin sualına qısa və aydın cavab verin.',scopes=[dict(pipeline_id='10',pipeline_name='Satış',stage_id='11',channel_id='preview')])]
AI_SETTINGS = dict(enabled=True,auto_send_enabled=False,crm_actions_enabled=False,server_auto_send_enabled=False,server_crm_actions_enabled=False,max_replies_per_chat=20,company_instructions='Müştərilərə dəqiq məlumat verin.')
CONFIG = dict(success=True,pipelines=PIPELINES,stages=[dict(s,pipeline_id='10') for s in PIPELINES[0]['stages']],policies=[dict(policy_key='task_approval',value={}),dict(policy_key='members',value={}),dict(policy_key='modules',value=MODULES)])
POSTS = []

def me():
    return dict(success=True,member=OWNER,members=MEMBERS,role_permissions={r:list(MODULES) for r in ['owner','admin','manager','worker','master']},integrations=[dict(provider='kommo',status='connected',account_domain='preview.kommo.com'),dict(provider='linear',status='connected')],capabilities=dict(modules=MODULES,chat_ai=True,deal_board=PIPELINES,notification_events=[dict(key='task_assigned',label='Yeni tapşırıq',module='tasks',roles=['owner','admin','manager','worker'],channels=['telegram','push'])],hot_orders=dict(can_create=True,can_claim=True,services=[dict(id='service',name='Quraşdırma',active=True)])))

class Preview(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, body, status=200):
        content=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(content)

    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/health':return self.respond(dict(success=True,preview=True))
        if path=='/__preview/posts':return self.respond(dict(success=True,posts=POSTS))
        if path.startswith('/api/'):
            return self.api(path)
        name='tenant-app.html' if path in ['/','/app'] else path.removeprefix('/assets/')
        file=(DOCS/name).resolve()
        if DOCS not in file.parents or not file.is_file():return self.respond(dict(success=False,error='Preview file missing'),404)
        data=file.read_bytes();self.send_response(200);self.send_header('Content-Type','text/javascript; charset=utf-8' if file.suffix=='.js' else 'text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)

    def do_POST(self):self.write_api()
    def do_PATCH(self):self.write_api()
    def do_DELETE(self):self.write_api()

    def write_api(self):
        path=urlparse(self.path).path
        data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
        POSTS.append(dict(path=path,method=self.command,data=data))
        if path.endswith('/members'):
            for member in MEMBERS:
                if str(member['telegram_id'])==str(data.get('telegram_id')):
                    if data.get('action')=='deactivate':member['active']=False
                    else:member.update({k:data[k] for k in ['display_name','role','permissions'] if k in data})
        elif path.endswith('/workflow'):CONFIG.update({k:data[k] for k in ['pipelines','stages','policies'] if k in data})
        elif path.endswith('/tasks/complete'):
            for task in TASKS:
                if task['kommo_task_id']==int(data.get('task_id',0)):task['completed']=True
            return self.respond(dict(success=True,task_id=data.get('task_id')))
        elif path.endswith('/crm/tasks'):return self.respond(dict(success=True,task_id=202))
        elif path.endswith('/invites'):return self.respond(dict(success=True,invite=dict(url='http://127.0.0.1:8766/join/local-preview')))
        elif path.endswith('/ai/agents/settings'):AI_SETTINGS.update(data)
        elif '/ai/agents/' in path:
            agent=next((x for x in AGENTS if x['id']==path.split('/')[-1]),None)
            if agent:agent.update(data);return self.respond(dict(success=True,result=agent))
        elif path.endswith('/ai/agents'):
            agent=dict(data,id='local-new-agent',version=1);AGENTS.append(agent);return self.respond(dict(success=True,result=agent))
        return self.api(path)

    def api(self,path):
        result=dict(success=True)
        if path.endswith('/me'):result=me()
        elif path.endswith('/access-requests'):result.update(requests=[])
        elif path.endswith('/workflow/catalog'):result.update(pipelines=PIPELINES,users=[],warnings=[])
        elif path.endswith('/workflow'):result=copy.deepcopy(CONFIG)
        elif path.endswith('/crm/deals'):result.update(deals=DEALS,total=len(DEALS))
        elif path.endswith('/crm/deal') or '/crm/deals/' in path:result.update(deal=DEALS[0])
        elif path.endswith('/crm/tasks/options'):result.update(executors=[dict(id=x['telegram_id'],name=x['display_name'],role=x['role'],pipelines=PIPELINES) for x in MEMBERS],deals=[dict(id=d['kommo_lead_id'],name=d['contact_name'],pipeline_id=10) for d in DEALS],pipelines=PIPELINES)
        elif path.endswith('/crm/tasks'):result.update(tasks=TASKS)
        elif path.endswith('/crm/chat') or path.endswith('/crm/chat/messages') or path.endswith('/crm/chat/history'):result.update(messages=[dict(external_id='message-1',body='Salam. Proqram haqqında məlumat verə bilərsiniz?',direction='incoming',channel='WhatsApp',happened_at='2026-10-13T08:30:00Z'),dict(external_id='message-2',body='Salam. Hansı sahə üçün maraqlanırsınız?',direction='outgoing',channel='WhatsApp',happened_at='2026-10-13T08:31:00Z')],next_cursor=None,tenant_id=OWNER['tenant_id'],user_id=1)
        elif path.endswith('/crm/chat/receipts'):result.update(receipts=[])
        elif path.endswith('/task-approvals') or path.endswith('/deal-approvals'):result.update(approvals=[],has_more=False)
        elif path.endswith('/hot-orders'):result.update(orders=[dict(id='hot-1',service_name='Quraşdırma',client_name='Araz Market',description='Kassa avadanlığını quraşdırmaq və yoxlamaq.',status='open',priority='normal',phone='994501112233',address='Bakı',capabilities=dict(can_claim=True,can_cancel=True))],total=1)
        elif path.endswith('/finance/members'):result.update(members=MEMBERS)
        elif path.endswith('/finance'):result.update(entries=[dict(id='entry-1',amount='100.00',kind='credit',note='Başlanğıc balans',actor_name='Administrator',created_at='2026-10-13T08:00:00Z')],balance='100.00',currency='AZN',display_name='Nizam Qasımov',active=True,total=1)
        elif path.endswith('/linear/tasks') and 'catalog=1' in self.path:
            result.update(owner=True,config_version='preview',required_fields=['account','operator','project','body'],binding={},states=[dict(id='triage',name='Triage'),dict(id='done',name='Done')],projects=[dict(id='project-1',name='AKUL')],assignees=[dict(id='user-1',name='Developer')])
        elif path.endswith('/linear/tasks'):
            result.update(tenant_id=OWNER['tenant_id'],user_id=1,config_version='preview',capabilities=dict(can_create=True),pageInfo=dict(hasNextPage=False),issues=[dict(id='linear-1',identifier='BS-2041',title='Yeni hesabat ekranı',account='Araz Market',operator='operator@example.test',body='Satış hesabatına tarix filtri əlavə edin.',description='Satış hesabatına tarix filtri əlavə edin.',priority=2,state=dict(id='triage',name='Triage'),project=dict(id='project-1',name='AKUL'),createdAt='2026-10-13T08:00:00Z',updatedAt='preview',capabilities=dict(can_edit=True,can_change_status=True,buttons=[]))])
        elif path.endswith('/linear/settings'):result.update(status='connected',settings={},config={},catalog=dict(teams=[],states=[],projects=[],members=[]))
        elif path.endswith('/chat/ai/settings'):result.update(configured=True,enabled=True,consent=True,audio_enabled=True,model='Chat AI modeli',transcription_model='whisper-1',daily_limit=50,media_hosts=[],updated_at='preview')
        elif path.endswith('/news/ai'):result.update(configured=True,enabled=True,model='Chat AI modeli',daily_limit=10,updated_at='preview')
        elif path.endswith('/news/telegram/settings'):result.update(configured=True,enabled=False,title='BeinSystems',chat_id='@preview',timezone='Asia/Baku',start='09:00',end='19:00',interval_minutes=60,updated_at='preview',bot_username='local_preview_bot')
        elif path.endswith('/news'):result.update(tenant_id=OWNER['tenant_id'],user_id=1,news=[dict(id='news-1',project_name='AKUL',identifier='BS-2041',title='Yeni satış hesabatı',summary='Satış hesabatlarını tarixə görə süzərək daha rahat izləyə bilərsiniz.',url='https://akul.az',status='pending',created_at='2026-10-13T08:00:00Z')],after=None)
        elif path.endswith('/integrations/beinsystems'):result.update(integration=dict(status='not_connected',api_ready=False,base_url='',branch_id=''))
        elif '/ai/' in path:
            value=[]
            if path.endswith('/agents/settings'):value=AI_SETTINGS
            elif path.endswith('/agents/test-mode'):value=dict(enabled=False,verified=False,protection_state='blocked_unbound')
            elif path.endswith('/agents'):value=AGENTS
            elif path.endswith('/knowledge'):value=[dict(id='knowledge-1',title='Məhsul haqqında',active=True,chunks=3)]
            result.update(result=value)
        elif 'sync' in path:result.update(resources=[],state='idle',jobs=[])
        return self.respond(result)

if __name__=='__main__':
    print('Synthetic UI preview: http://127.0.0.1:8766/app — no external services',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8766),Preview).serve_forever()
