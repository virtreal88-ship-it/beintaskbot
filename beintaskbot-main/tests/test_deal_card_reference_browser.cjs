/* Both deal entry points use the current /app shell. External providers are fixtures. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium,devices} = require('../.support-test-deps/node_modules/playwright');
const {installFixture} = require('./task_read_save_flow_fixture.cjs');
const docs = path.resolve(__dirname, '../docs');
const assets = new Set(['tenant-operator-i18n.js', 'tenant-request-coordinator.js', 'tenant-view-demand.js',
  'tenant-task-flow.js', 'tenant-kommo-deal-card.js', 'tenant-deal-chat.js', 'tenant-chat-history.js', 'tenant-chat-ai.js',
  'tenant-dialog-company.js']);
const html = fs.readFileSync(path.join(docs, 'tenant-app.html'), 'utf8').replace(
  /<script\b[^>]*src="([^"]+)"[^>]*><\/script>/gi,
  (_, url) => assets.has(path.basename(url)) ? '<script>' +
    fs.readFileSync(path.join(docs, path.basename(url)), 'utf8').replace(/<\/script/gi, '<\\/script') + '</script>' : '');

function installDealResponses({native, linking, limited, operatorCompany, longDetails}) {
  const f = window.taskFlowFixture, originalFetch = window.fetch;
  const channel = '10000000-0000-4000-8000-000000000301';
  const route = {provider:'connect', channel_id:channel, conversation_id:'15550101010@s.whatsapp.net',
    lead_id:77, can_send:true, last_message_at:'2026-11-25T16:00:00Z'};
  const detail = {title:'Подключение кассового модуля', name:'Подключение кассового модуля', contact_name:'Айгюн Мамедова',
    contact_phone:'+994 50 123 45 67', phone:'+994 50 123 45 67', company_name:'Araz Retail',
    responsible_name:'Айдан Алиева', description:'Подключить кассовый модуль для новой точки в Баку и согласовать время настройки.',
    pipeline_name:'Подключение', stage_name:'В работе', price:320};
  const kommo = {...detail, responsible_id:99, responsible_name:'',kommo_lead_id:7, pipeline_id:1, status_id:2};
  if(longDetails)Object.assign(kommo,{contact_name:'',contact_phone:'',phone:'',company_name:'',
    detail_fields:Array.from({length:26},(_,i)=>({label:'Custom field '+i,value:'Value '+i}))});
  const local = {...detail, id:f.deal, pipeline_id:'1', stage_id:'2', actions:{view:true,update:!limited},
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
  const access = {view:true, create:!limited, update:!limited};
  Object.assign(f.profile.capabilities.modules,{deals:true,dialogs:true});
  f.profile.capabilities.actions={deals:access,tasks:access};
  f.profile.capabilities.pages={deals:true,dialogs:true,tasks:true,profile:true};
  f.profile.capabilities.deal_operator_company=operatorCompany;
  f.profile.integrations=[{provider:'kommo',status:'connected'}];
  f.kommo=[{source:'kommo',id:'8',kommo_lead_id:7,description:'Согласовать время установки'}];
  window.dealProof={route,requests:[],opened:null,rejectMove:false};
  window.tenantOperator={hasRight:()=>true,openDialog:r=>{dealProof.opened=r;}};
  window.tenantChatSend={bind:options=>{dealProof.sendBound=options.leadId;}};
  const json=(data,status=200)=>new Response(JSON.stringify({success:status===200,...data}),{status,headers:{'Content-Type':'application/json'}});
  let companyBinding=limited?'test-binding':null;
  const binding=()=>({company_id:companyBinding,company_name:companyBinding?'deal-company':null,
    can_manage:!limited,can_search_operator:!limited,can_unlink:!limited});
  window.fetch=async (input,options={})=>{
    const url=new URL(input,location.href),p=url.pathname,method=options.method||'GET';
    dealProof.requests.push({p,method,query:url.search,body:options.body?JSON.parse(options.body):null});
    if(p.startsWith('/api/platform/operator/deal-company')){
      const fields=method==='GET'?Object.fromEntries(url.searchParams):JSON.parse(options.body);
      if(fields.source!==(native?'native':'kommo')||fields.deal_id!==(native?f.deal:'7'))return json({},404);
      if(method==='GET')return json({binding:binding()});
      if('operator_search' in fields)return limited?json({},403):json({accounts:[{account:'deal-company'}]});
      if(fields.expected_company_id!==companyBinding)return json({},409);
      if(fields.operator_account==='deal-company'&&!limited){companyBinding='test-binding';return json({binding:binding()});}
      if(fields.company_id===null&&!limited){companyBinding=null;return json({binding:binding()});}
      if(fields.action==='company_profile')return json({company_profile:{state:'available',registration_phone:'+994 12 123 45 67',balance:25,partner_name:'Test partner'}});
      if(p.endsWith('/public-link'))return json({public_link:{state:'available',url:'https://1000.az/test01'}});
      const rows={company_tasks:[['TEST-22','Operator task','Open']],company_modules:[['Point of sale']],
        company_history:[['2026-11-25','25','credit','AZN']]};
      if(rows[fields.action])return json({company_section:{state:'available',rows:rows[fields.action]}});
      return json({},400);
    }
    if(p==='/api/platform/crm/deal-view-preferences')return json({source:native?'native':'kommo'});
    if(p==='/api/platform/crm/native/deals')
      return json(url.searchParams.has('options')?{workflows:stages,contacts:[],members:[]}:{deals:[local]});
    if(p==='/api/platform/crm/native/deals/'+f.deal){
      if(method==='POST'){if(dealProof.rejectMove)return json({error:'Смена этапа отклонена'},403);
        const change=JSON.parse(options.body);local.stage_id=change.stage_id||local.stage_id;
        local.updated_at='2026-11-25T16:10:00Z';}
      return json({deal:local});
    }
    if(p==='/api/platform/crm/deal')return url.searchParams.has('details')?
      json({details:limited?{}:{responsible_name:'Айдан Алиева'}}):json({deal:kommo});
    if(p==='/api/platform/crm/deal/notes')return json({notes:[{id:'note-1',text:'Клиент подтвердил время установки.',created_at:'2026-11-25T16:00:00Z'}],
      calls:[{type:'call_in',created_at:'2026-11-25T16:01:00Z'}],stages:[{stage_id:'2',pipeline_id:'1',created_at:'2026-11-25T15:00:00Z'}]});
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
  await modal.getByRole('tab',{name:'Чат',exact:true}).waitFor();
  return modal;
}


async function verifyTaskCompany(browser){
  for(const [source,width,capability,canManage] of [
    ['native',1440,true,true],['kommo',360,true,true],['native',360,true,false],['kommo',360,false,false]
  ]){
    const page=await browser.newPage({...(width<900?devices['Pixel 7']:{}),viewport:{width,height:820}});
    page.setDefaultTimeout(6500);
    try{
      await page.addInitScript(installFixture,{});
      await page.addInitScript(({capability,canManage})=>{
        const fixture=window.taskFlowFixture,original=window.fetch;
        fixture.profile.capabilities.task_operator_company=capability;
        const names=new Map(canManage?[]:[[fixture.task,'readonly-account']]);
        window.companyRequests=[];
        window.fetch=async(input,options={})=>{
          const url=new URL(input,location.href);
          if(!url.pathname.startsWith('/api/platform/operator/task-company'))return original(input,options);
          const fields=options.method==='POST'?JSON.parse(options.body):Object.fromEntries(url.searchParams);
          companyRequests.push({path:url.pathname,method:options.method,fields});
          const json=(data,status=200)=>new Response(JSON.stringify({success:status===200,...data}),{status,headers:{'Content-Type':'application/json'}});
          if(fields.expected_tenant_id!==fixture.A||fields.expected_user_id!=='11'||!fixture[fields.source]?.some(task=>task.id===fields.task_id)||'deal_id' in fields)return json({},404);
          const name=names.get(fields.task_id);
          const binding=()=>({company_id:names.has(fields.task_id)?'test-binding':null,
            company_name:names.get(fields.task_id)??null,can_manage:canManage,can_search_operator:canManage,can_unlink:canManage});
          if(options.method==='GET')return json({binding:binding()});
          if(fields.action==='company_profile'&&name)return json({company_profile:{state:'available',partner_name:'Task partner',balance:30}});
          if(url.pathname.endsWith('/public-link')&&name)return json({public_link:{state:'available',url:'https://1000.az/task01'}});
          if(!canManage)return json({},403);
          if(fields.operator_search==='task')return json({accounts:[{account:'task-company'}]});
          if(fields.expected_company_id!==binding().company_id)return json({},409);
          if(fields.operator_account==='task-company')names.set(fields.task_id,fields.operator_account);
          else if(fields.company_id===null)names.delete(fields.task_id);
          else return json({},400);
          return json({binding:binding()});
        };
      },{capability,canManage});
      await page.route('**/*',r=>r.request().isNavigationRequest()?r.fulfill({contentType:'text/html',body:html}):r.abort());
      await page.goto('https://fixture.test/app?view=tasks&lang=ru');
      await page.waitForFunction(()=>window.tenantProfileSnapshot?.member?.active===true&&window.tenantTaskFlow?.ready());
      const opened=()=>page.evaluate(source=>window.tenantTaskFlow.open(source,taskFlowFixture[source][0].id),source);
      assert.equal(await opened(),true);
      const task=page.locator('#taskFlowCard');
      const trigger=task.locator('[data-task-operator-company]');
      if(!capability){
        assert.equal(await trigger.count(),0);
        assert.equal(await page.evaluate(()=>companyRequests.length),0);
        continue;
      }
      assert(await trigger.evaluate(button=>{
        const rect=button.getBoundingClientRect();
        const details=document.getElementById('taskFlowCardBody').getBoundingClientRect();
        return rect.height>0&&rect.top>=0&&rect.bottom<=details.top+1&&rect.bottom<=window.innerHeight;
      }));
      await trigger.click();
      const panel=task.getByRole('dialog',{name:'Компания Operator',exact:true});
      const input=panel.locator('#operatorDialogueCompanySearch');
      if(canManage){
        await panel.locator('#operatorDialogueCompanySearch:not([readonly])').waitFor();
        await input.pressSequentially('task');
        await panel.getByRole('button',{name:'@task-company',exact:true}).click();
      }else{
        await input.waitFor();
        assert.equal(await input.getAttribute('readonly'),'');
        assert.equal(await panel.getByRole('button',{name:'Снять связь',exact:true}).count(),0);
      }
      await panel.getByText('Task partner',{exact:true}).waitFor();
      await panel.getByRole('link',{name:'https://1000.az/task01'}).waitFor();
      await page.keyboard.press('Escape');
      await panel.waitFor({state:'detached'});
      assert(await task.isVisible(),'Escape only closes the company panel');
      await task.locator('[data-task-card-close]').click();
      await task.waitFor({state:'hidden'});
      assert.equal(await opened(),true);
      await trigger.filter({hasText:canManage?'task-company':'readonly-account'}).waitFor();
      if(canManage){
        await trigger.click();
        await panel.getByText('Task partner',{exact:true}).waitFor();
        await panel.getByRole('button',{name:'Снять связь',exact:true}).click();
        await panel.getByRole('tab',{name:'Главная',exact:true}).focus();
        await trigger.filter({hasText:/^Компания Operator$/}).waitFor();
        assert(await page.evaluate(()=>companyRequests.some(r=>r.fields?.company_id===null)));
      }
      const identity=await page.evaluate(()=>companyRequests.every(({fields})=>fields.task_id===taskFlowFixture[fields.source][0].id));
      assert(identity,'The task id is source-qualified and retained as a string');
    } finally {await page.close();}
  }
}

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
  const screenshots=process.env.DEAL_CARD_SCREENSHOTS;
  if(screenshots)fs.mkdirSync(screenshots,{recursive:true});
  try{
    const variants=[[false,360,'none',false,'dark',true],
        [false,1440,'empty'],[false,1440,'none'],[true,390,'none'],[true,390,'wrong'],[true,390,'denied',true],[false,390,'none',true]];
    for(const native of [false,true])for(const width of [1440,390])for(const theme of ['light','dark'])variants.push([native,width,'linked',false,theme]);
    for(const [native,width,linking,limited=false,theme='light',longDetails=false] of variants){
      const operatorCompany=theme==='dark'||limited;
      const page=await browser.newPage({...(width<900?devices['Pixel 7']:{}),viewport:{width,height:900},colorScheme:theme}),errors=[];
      page.setDefaultTimeout(7000);
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(installFixture,{});
      await page.addInitScript(installDealResponses,{native,linking,limited,operatorCompany,longDetails});
      await page.route('**/*',r=>r.request().isNavigationRequest()?r.fulfill({contentType:'text/html',body:html}):r.abort());
      await page.goto('https://fixture.test/app?view=deals&lang=ru');
      await page.addStyleTag({content:fs.readFileSync(path.join(docs,'../ui/src/app-theme.css'),'utf8')});
      await page.evaluate(theme=>{document.body.dataset.appUi='kwid';document.documentElement.dataset.appTheme=theme;},theme);
      const counter=await page.evaluate(()=>{
        const styles=id=>getComputedStyle(document.getElementById(id));
        const deal=styles('dealCount'), task=styles('nativeTaskCount');
        return {sameType:deal.fontFamily===task.fontFamily&&deal.fontSize===task.fontSize&&deal.color===task.color,
          background:deal.backgroundColor,border:deal.borderTopWidth,padding:deal.paddingTop};
      });
      assert.deepEqual(counter,{sameType:true,background:'rgba(0, 0, 0, 0)',border:'0px',padding:'0px'});
      const modal=await open(page,native);
      assert(await modal.getByRole('heading',{name:'Подключение кассового модуля'}).isVisible());
      assert(!/Айгюн Мамедова|\+994/.test(await modal.locator('.modal-head').textContent()));
      assert.equal(await modal.getByRole('tab',{name:'Чат',exact:true}).getAttribute('aria-selected'),'true');
      if(width<900)await modal.locator('[data-deal-pane=data]').click();
      const info=modal.locator('.deal-info-panel');
      if(longDetails){
        assert.equal(await info.getByRole('heading',{name:'Контакт',exact:true}).count(),0);
        assert(await info.evaluate(panel=>panel.scrollHeight>panel.clientHeight));
      }else{
        assert(await info.getByText('Araz Retail',{exact:true}).isVisible());
        assert(await info.getByText('Айгюн Мамедова',{exact:true}).isVisible());
        assert(await info.getByText('+994 50 123 45 67',{exact:true}).isVisible());
      }
      if(!native)await info.getByText(limited?'Имя недоступно':'Айдан Алиева',{exact:true}).waitFor();
      if(limited)assert(await modal.locator('.deal-stage-trigger').isDisabled());
      if(operatorCompany){
        const trigger=info.locator('[data-deal-operator-company]');
        const inView=await trigger.evaluate(button=>{
          const bounds=button.getBoundingClientRect(),area=button.closest('.deal-info-panel').getBoundingClientRect();
          return {height:bounds.height, top:bounds.top,bottom:bounds.bottom,panelTop:area.top,panelBottom:area.bottom,display:getComputedStyle(button).display,minHeight:getComputedStyle(button).minHeight};
        });
        assert(inView.height>=40&&inView.top>=inView.panelTop&&inView.bottom<=inView.panelBottom+1,JSON.stringify({native,width,limited,longDetails,...inView}));
        await trigger.click();
        const panel=modal.getByRole('dialog',{name:'Компания Operator',exact:true});
        const search=panel.locator('#operatorDialogueCompanySearch');
        if(!limited){
          await search.focus();
          await search.pressSequentially('deal');
          await panel.getByRole('button',{name:'@deal-company',exact:true}).click();
        }else{
          assert.equal(await search.getAttribute('readonly'),'');
        }
        await panel.getByText('+994 12 123 45 67',{exact:true}).waitFor();
        await panel.getByText('Test partner',{exact:true}).waitFor();
        await panel.getByRole('link',{name:'https://1000.az/test01'}).waitFor();
        await panel.getByRole('tab',{name:'Задачи'}).click();
        await panel.getByText('Operator task',{exact:true}).waitFor();
        await panel.getByRole('tab',{name:'Модули'}).click();
        await panel.getByText('Point of sale',{exact:true}).waitFor();
        await panel.getByRole('tab',{name:'Платежи'}).click();
        await panel.locator('table').waitFor();
        await panel.getByRole('button',{name:'Закрыть',exact:true}).click();
        await panel.waitFor({state:'detached'});
        assert.match(await trigger.textContent(),/deal-company/);
        await trigger.click();
        await panel.getByText('+994 12 123 45 67',{exact:true}).waitFor();
        if(!limited&&width<900){
          page.once('dialog',dialog=>dialog.accept());
          await panel.getByRole('button',{name:'Снять связь',exact:true}).click();
          await panel.getByRole('tab',{name:'Главная',exact:true}).focus();
          await page.waitForFunction(()=>dealProof.requests.some(r=>r.body?.company_id===null));
          await panel.locator('[data-company-profile]').waitFor({state:'hidden'});
          await trigger.filter({hasText:/^Компания Operator$/}).waitFor();
        }
        await panel.getByRole('button',{name:'Закрыть',exact:true}).click();
        await panel.waitFor({state:'detached'});
        assert.equal(await page.evaluate(()=>dealProof.requests.some(r=>r.p.includes('/operator/dialog-company'))),false);
      }else{
        assert.equal(await info.locator('[data-deal-operator-company]').count(),0);
        assert.equal(await page.evaluate(()=>dealProof.requests.some(r=>r.p.includes('/operator/deal-company'))),false);
      }
      if(width<900)await modal.locator('[data-deal-pane=chat]').click();
      assert.equal(await modal.locator('.modal-card').evaluate(n=>n.scrollWidth<=n.clientWidth+1),true);
      const savedCount=()=>page.evaluate(()=>dealProof.requests.filter(r=>r.p==='/api/platform/crm/chat/history').length);
      if(linking==='linked'){
        await modal.getByText('Завтра после 10:00 удобно. Спасибо!',{exact:true}).waitFor();
        assert(await modal.getByRole('button',{name:'Открыть диалог'}).isVisible());
        assert.equal(await modal.locator('#chatRows').count(),1);
        assert.equal(await savedCount(),1);
        assert(await page.evaluate(()=>dealProof.requests.some(r=>r.p==='/api/platform/crm/chat/history'&&r.query.includes('limit=40'))));
        assert.equal(await modal.locator('[data-chat-attachment]').count(),1);
        assert.equal(await page.evaluate(()=>dealProof.sendBound),native?77:7);
        if(screenshots)await page.screenshot({path:path.join(screenshots,`${native?'beintaskbot':'kommo'}-${width}-${theme}.png`)});
        if(width<900){
          await modal.locator('#chatText').focus();
          await page.evaluate(()=>{
            Object.defineProperty(visualViewport,'height',{configurable:true,value:540});
            visualViewport.dispatchEvent(new Event('resize'));
          });
          const box=await modal.locator('#chatText').boundingBox();
          assert(box.y+box.height<=541,'composer stays above the reduced visual viewport');
          await page.evaluate(()=>{delete visualViewport.height;visualViewport.dispatchEvent(new Event('resize'));});
        }
        if(native){
          const stage=modal.getByRole('combobox',{name:'Стадия сделки'});
          await page.evaluate(()=>{dealProof.rejectMove=true;});
          await stage.selectOption('3');
          await modal.getByText('Смена этапа отклонена',{exact:true}).waitFor();
          assert.equal(await stage.inputValue(),'2');
          await page.evaluate(()=>{dealProof.rejectMove=false;});
          await stage.selectOption('3');
          await page.waitForFunction(()=>document.querySelector('.deal-stage-trigger').value==='3'&&!document.querySelector('.deal-stage-trigger').disabled);
          assert.equal(await stage.inputValue(),'3');
          await modal.locator('.modal-head .close').click();
          await open(page,true);
          assert.equal(await stage.inputValue(),'3');
          if(width<900)await modal.locator('[data-deal-pane=data]').click();
          await modal.locator('[data-native-card-edit]').click();
          await page.locator('#nativeDealModal.show').waitFor();
          assert.equal(await page.locator('#nativeDealTitle').inputValue(),'Подключение кассового модуля');
        }else{
          assert.equal(await page.evaluate(()=>dealProof.requests.filter(r=>r.p.endsWith('/deal/notes')).length),0);
          await modal.locator('#chatText').fill('Черновик');
          await modal.getByRole('tab',{name:'Лента'}).click();
          const task=modal.locator('[data-related-task]');
          await task.waitFor();
          assert.match(await task.textContent(),/Согласовать время установки/);
          await modal.getByText('Входящий звонок',{exact:false}).waitFor();
          const events=await modal.locator('.deal-feed-entry').evaluateAll(nodes=>nodes.map(n=>Number(n.dataset.dealWhen)));
          assert.deepEqual(events,events.slice().sort((a,b)=>b-a));
          await modal.locator('[data-deal-pane=chat]').click();
          assert.equal(await modal.locator('#chatText').inputValue(),'Черновик');
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
    await verifyTaskCompany(browser);
    console.log('PASS task company: Chromium Beintaskbot + Kommo, selection, reopening, unlinking, Escape and access gates');
    console.log('PASS deal card: Chromium native + Kommo, responsive layout, stage/edit, lazy related data, authorised and missing routes');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
