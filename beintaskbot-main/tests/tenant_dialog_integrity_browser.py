"""Loopback-only visual fixture using the real /app dialogue HTML, JS and CSS."""
import json,re
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parents[1]
BOOT=r'''
const query=new URLSearchParams(location.search);document.documentElement.dataset.appTheme=query.get('theme')||'light';
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const profile={member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};window.tenantProfileSnapshot=profile;
const route={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Synthetic customer',channel_name:'Synthetic WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:false,controls_allowed:true,agent_status:'manual',agent_mode:'manual',avatar_status:''};
const messages=[{id:'message-1',external_id:'external-1',direction:'incoming',message_type:'text',body:'Synthetic customer question',author_type:'customer',author_name:'Synthetic customer',happened_at:'2026-10-23T10:00:00Z'},{id:'message-2',external_id:'external-2',direction:'outgoing',message_type:'text',body:'Synthetic employee reply',author_type:'operator',author_name:'Operator',happened_at:'2026-10-23T10:01:00Z'}];
window.fetch=async(target,options={})=>{const url=new URL(target,location.href);let value={success:true,result:{}};
if(url.pathname==='/api/platform/me')value={success:true,...profile};
else if(url.pathname.endsWith('/dialogs'))value={success:true,result:{dialogs:[{...route,unread_count:2,last_message_id:'message-2',last_message_direction:'outgoing',last_message_text:'Synthetic preview',last_message_at:'2026-10-23T10:01:00Z'}],channels:[{id:channel,name:'Synthetic WhatsApp'}],agents:[],unread_count:2,total_count:1,next_offset:null}};
else if(url.pathname.endsWith('/dialogs/history'))value={success:true,result:{dialog:route,messages,next_before:null}};
else if(url.pathname==='/api/platform/push/devices')value={success:true,devices:[],delivery_enabled:true,public_key:''};
else if(options.method&&options.method!=='GET'&&!url.pathname.endsWith('/dialogs/read'))throw Error('Mutation blocked in visual fixture');
return {ok:true,json:async()=>value};};
document.querySelectorAll('.view').forEach(node=>node.classList.remove('active'));document.querySelector('#view-dialogs').classList.add('active');
'''
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def do_GET(self):
        url=urlsplit(self.path)
        if url.path=='/app':
            text=(ROOT/'docs/tenant-app.html').read_text(encoding='utf8')
            text=re.sub(r'<script\b[^>]*>[\s\S]*?</script>','',text)
            text=text.replace('</body>','<script>'+BOOT+'</script><script src="/assets/tenant-operator-i18n.js"></script><script src="/assets/tenant-operator-dialogs.js"></script><script src="/assets/tenant-push.js"></script></body>')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(text.encode());return
        if url.path.endswith('/dialogs/avatar'):
            body=b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#167d69"/><text x="32" y="42" text-anchor="middle" fill="white" font-size="34">S</text></svg>'
            self.send_response(200);self.send_header('Content-Type','image/svg+xml');self.end_headers();self.wfile.write(body);return
        if url.path.startswith('/assets/'):self.path='/docs/'+url.path.split('/assets/',1)[1]
        super().do_GET()
if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',8782),Handler).serve_forever()
