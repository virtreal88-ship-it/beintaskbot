/* Both deal entry points use the current /app shell. External providers are fixtures. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../.support-test-deps/node_modules/playwright');
const {installFixture} = require('./task_read_save_flow_fixture.cjs');
const docs = path.resolve(__dirname, '../docs');
const assets = new Set(['tenant-operator-i18n.js', 'tenant-request-coordinator.js', 'tenant-view-demand.js',
  'tenant-task-flow.js', 'tenant-kommo-deal-card.js', 'tenant-deal-chat.js', 'tenant-chat-history.js', 'tenant-chat-ai.js']);
const html = fs.readFileSync(path.join(docs, 'tenant-app.html'), 'utf8').replace(
  /<script\b[^>]*src="([^"]+)"[^>]*><\/script>/gi,
  (_, url) => assets.has(path.basename(url)) ? '<script>' +
    fs.readFileSync(path.join(docs, path.basename(url)), 'utf8').replace(/<\/script/gi, '<\\/script') + '</script>' : '');

function installDealResponses({native, linking}) {
  const f = window.taskFlowFixture, originalFetch = window.fetch;
  const channel = '10000000-0000-4000-8000-000000000301';
  const route = {provider:'connect', channel_id:channel, conversation_id:'15550101010@s.whatsapp.net',
    lead_id:77, can_send:true, last_message_at:'2026-11-25T16:00:00Z'};
  const detail = {title:'Подключение кассового модуля', name:'Подключение кассового модуля', contact_name:'Айгюн Мамедова',
    contact_phone:'+994 50 123 45 67', phone:'+994 50 123 45 67', company_name:'Araz Retail',
    responsible_name:'Айдан Алиева', description:'Подключить кассовый модуль для новой точки в Баку и согласовать время настройки.',
    pipeline_name:'Подключение', stage_name:'В работе', price:320};
  const kommo = {...detail, kommo_lead_id:7, pipeline_id:1, status_id:2};
  const local = {...detail, id:f.deal, pipeline_id:'1', stage_id:'2', actions:{view:true,update:true},
    updated_at:'2026-11-25T16:00:00Z', channel_id:linking==='none'?'':channel,
    conversation_id:linking==='none'?'':route.conversation_id};
  const stages = [{pipeline_id:'1',pipeline_name:'Подключение',stage_id:'2',stage_name:'В работе'},
    {pipeline_id:'1',pipeline_name:'Подключение',stage_id:'3',stage_name:'Согласование'}];
  const history = [
    ['incoming','Айгюн Мамедова','Добрый день! Хотим подключить кассу в новой точке. Что нужно подготовить?'],
    ['outgoing','Айдан Алиева','Добрый день. Пришлите, пожалуйста, модель кассы и фото маркировки.'],
    ['incoming','Айгюн Мамедова','Отправляю фото. Принтер уже на месте.','image'],
    ['outgoing','Айдан Алиева','Спасибо, модель подходит. Можем подключить модуль завтра после 10:00.'],
    ['incoming','Айгюн Мамедова','Завтра после 10:00 удобно. Спасибо!']
  ].map(([direction, author_name, body, message_type='text'],i)=>({external_id:'demo-'+i,
    channel:'WhatsApp', direction, author_name, body, message_type, happened_at:'2026-11-25T16:0'+i+':00Z'}));
  const access = {view:true, create:true, update:true};
  Object.assign(f.profile.capabilities.modules,{deals:true,dialogs:true});
  f.profile.capabilities.actions={deals:access,tasks:access};
  f.profile.capabilities.pages={deals:true,dialogs:true,tasks:true,profile:true};
  f.profile.integrations=[{provider:'kommo',status:'connected'}];
  f.kommo=[{source:'kommo',id:'8',kommo_lead_id:7,description:'Согласовать время установки'}];
  window.dealProof={route,requests:[],opened:null};
  window.tenantOperator={hasRight:()=>true,openDialog:r=>{dealProof.opened=r;}};
  window.tenantChatSend={bind:options=>{dealProof.sendBound=options.leadId;}};
  const json=(data,status=200)=>new Response(JSON.stringify({success:status===200,...data}),{status,headers:{'Content-Type':'application/json'}});
  window.fetch=async (input,options={})=>{
    const url=new URL(input,location.href),p=url.pathname,method=options.method||'GET';
    dealProof.requests.push({p,method,query:url.search,body:options.body?JSON.parse(options.body):null});
    if(p==='/api/platform/crm/deal-view-preferences')return json({source:native?'native':'kommo'});
    if(p==='/api/platform/crm/native/deals')
      return json(url.searchParams.has('options')?{workflows:stages,contacts:[],members:[]}:{deals:[local]});
    if(p==='/api/platform/crm/native/deals/'+f.deal){
      if(method==='POST'){const change=JSON.parse(options.body);local.stage_id=change.stage_id||local.stage_id;
        local.updated_at='2026-11-25T16:10:00Z';}
      return json({deal:local});
    }
    if(p==='/api/platform/crm/deal')return json({deal:kommo});
    if(p==='/api/platform/crm/deal/notes')return json({notes:[{id:'note-1',text:'Клиент подтвердил время установки.'}]});
    if(p==='/api/platform/crm/deal-board-preferences')return json({catalog:[]});
    if(p==='/api/platform/ai/dialogs'){
      if(linking==='denied')return json({error:'Forbidden'},403);
      const available=linking==='wrong'?[{...route,channel_id:'other'}]:[native?route:{...route,provider:'kommo',lead_id:7}];
      return json({result:{dialogs:linking==='none'?[]:available}});
    }
    if(p==='/api/platform/crm/chat/history'||p==='/api/platform/crm/chat')
      return json({messages:linking==='linked'?history:[],next_cursor:null});
    return originalFetch(input,options);
  };
}

async function open(page, native) {
  await page.waitForFunction(()=>window.tenantProfileSnapshot?.member?.active===true);
  await page.evaluate(native=>{
    if(native){const trigger=document.createElement('button');trigger.dataset.nativeOpen='deal:'+taskFlowFixture.deal;
      document.body.append(trigger);trigger.click();trigger.remove();}
    else document.dispatchEvent(new CustomEvent('tenant-duplicate-open',{detail:{lead_id:7}}));
  },native);
  const modal=page.locator(native?'#nativeEntityCard':'#dealModal');
  await modal.getByRole('tab',{name:'Общее'}).waitFor();
  return modal;
}

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
  const screenshots=process.env.DEAL_CARD_SCREENSHOTS;
  if(screenshots)fs.mkdirSync(screenshots,{recursive:true});
  try{
    for(const [native,width,linking] of [[false,1440,'linked'],[false,390,'linked'],[false,1440,'empty'],[false,1440,'none'],[true,1440,'linked'],
      [true,390,'none'],[true,390,'wrong'],[true,390,'denied']]){
      const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
      page.setDefaultTimeout(7000);
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(installFixture,{});
      await page.addInitScript(installDealResponses,{native,linking});
      await page.route('**/*',r=>r.request().isNavigationRequest()?r.fulfill({contentType:'text/html',body:html}):r.abort());
      await page.goto('https://fixture.test/app?view=deals&lang=ru');
      const counter=await page.evaluate(()=>{
        const styles=id=>getComputedStyle(document.getElementById(id));
        const deal=styles('dealCount'), task=styles('nativeTaskCount');
        return {sameType:deal.fontFamily===task.fontFamily&&deal.fontSize===task.fontSize&&deal.color===task.color,
          background:deal.backgroundColor,border:deal.borderTopWidth,padding:deal.paddingTop};
      });
      assert.deepEqual(counter,{sameType:true,background:'rgba(0, 0, 0, 0)',border:'0px',padding:'0px'});
      const modal=await open(page,native);
      assert(await modal.getByRole('heading',{name:'Подключение кассового модуля'}).isVisible());
      assert.match(await modal.locator('.deal-chat-phone').textContent(),/Айгюн Мамедова.*\+994/);
      assert.equal(await modal.getByRole('tab',{name:'Общее'}).getAttribute('aria-selected'),'true');
      assert(await modal.getByRole('tabpanel',{name:'Общее'}).getByText('Araz Retail',{exact:true}).isVisible());
      assert.equal(await modal.locator('.modal-card').evaluate(n=>n.scrollWidth<=n.clientWidth+1),true);
      const savedCount=()=>page.evaluate(()=>dealProof.requests.filter(r=>r.p==='/api/platform/crm/chat/history').length);
      if(linking==='linked'){
        if(width<900){assert(await modal.locator('.deal-info-panel').isVisible());
          if(screenshots)await page.screenshot({path:path.join(screenshots,'mobile-details.png')});
          await modal.locator('[data-deal-pane=chat]').click();}
        await modal.getByText('Завтра после 10:00 удобно. Спасибо!',{exact:true}).waitFor();
        assert(await modal.getByRole('button',{name:'Открыть диалог'}).isVisible());
        assert.equal(await modal.locator('#chatRows').count(),1);
        assert.equal(await savedCount(),1);
        assert(await page.evaluate(()=>dealProof.requests.some(r=>r.p==='/api/platform/crm/chat/history'&&r.query.includes('limit=40'))));
        assert.equal(await modal.locator('[data-chat-attachment]').count(),1);
        assert.equal(await page.evaluate(()=>dealProof.sendBound),native?77:7);
        if(screenshots)await page.screenshot({path:path.join(screenshots,native?'desktop-beintaskbot.png':width<900?'mobile-chat.png':'desktop-kommo.png')});
        if(native){
          const stage=modal.getByRole('combobox',{name:'Стадия сделки'});
          await stage.selectOption('3');
          await page.waitForFunction(()=>dealProof.requests.some(r=>r.body?.action==='move_stage'&&r.body.stage_id==='3'));
          assert.equal(await stage.inputValue(),'3');
          await modal.locator('[data-native-card-edit]').click();
          await page.locator('#nativeDealModal.show').waitFor();
          assert.equal(await page.locator('#nativeDealTitle').inputValue(),'Подключение кассового модуля');
        }else if(width>=900){
          assert.equal(await page.evaluate(()=>dealProof.requests.filter(r=>r.p.endsWith('/deal/notes')).length),0);
          await modal.getByRole('tab',{name:'Задачи'}).click();
          const task=modal.locator('[data-related-task]');
          await task.waitFor();
          assert.match(await task.textContent(),/Согласовать время установки/);
          await modal.getByRole('tab',{name:'Заметки'}).click();
          await modal.getByText('Клиент подтвердил время установки.',{exact:true}).waitFor();
          assert(await modal.locator('.deal-chat-history').isVisible());
        }
      }else if(native){
        await modal.locator('[data-deal-pane=chat]').click();
        assert(await modal.getByText('К сделке не привязан доступный диалог.').isVisible());
        assert.equal(await modal.getByRole('button',{name:'Открыть диалог'}).count(),0);
        assert.equal(await modal.locator('#chatText').count(),0);
        assert.equal(await savedCount(),0);
      }else{
        await modal.getByText('Сообщений пока нет.',{exact:true}).waitFor();
        await page.waitForFunction(()=>dealProof.requests.some(r=>r.p==='/api/platform/ai/dialogs'));
        const link=modal.getByRole('button',{name:'Открыть диалог'});
        if(linking==='empty')await link.waitFor();
        else assert.equal(await link.count(),0);
      }
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('PASS deal card: Chromium native + Kommo, responsive layout, stage/edit, lazy related data, authorised and missing routes');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
