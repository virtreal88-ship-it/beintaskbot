(function(){
'use strict';
const query=new URLSearchParams(location.search);
if(query.has('lesson')||query.has('share')||query.has('lesson_new'))return;
const root=document.getElementById('instructions');
const lang=['ru','az','en'].includes(query.get('site_lang'))?query.get('site_lang'):'ru';
const words={
 ru:{brand:'AKUL / ИНСТРУКЦИИ',eyebrow:'BEIN SYSTEMS · СПРАВОЧНЫЙ ЦЕНТР',title:'Инструкции для вашей команды',intro:'Статьи справочника и пошаговые уроки — в одном каталоге.',search:'Поиск по инструкциям',searchHint:'Например: склад, продажа, перевод…',all:'Все инструкции',articles:'База статей',lessons:'Пошаговые уроки',allStatus:'Все статусы',draft:'Черновик',pending_review:'На проверке',approved:'Утверждено',published:'Опубликовано',archived:'В архиве',newArticle:'Новая статья',newLesson:'Новый урок',createAI:'Создать с AI',manageArticles:'Редактор статей и экспорт',open:'Открыть',edit:'Редактировать',section:'Раздел',typeArticle:'Статья справочника',typeLesson:'Пошаговый урок',empty:'Инструкции не найдены.',loading:'Загружаем каталог…',error:'Не удалось загрузить каталог.',signIn:'Войдите в приложение, чтобы просматривать уроки компании.',back:'К каталогу',read:'Просмотр инструкции',noText:'В этой статье пока нет текста.',articlesUnavailable:'Статьи справочника сейчас недоступны.',lessonsUnavailable:'Опубликованные уроки сейчас недоступны.',public:'Опубликовано для сотрудников компании',articleDraft:'Черновик статьи',articlePending:'Есть неопубликованные изменения',unknown:'Статус',connection:'Нет связи с сервером.',editorTitle:'Редактор инструкции',tree:'Разделы',resultCount:'инструкций',images:'Изображения'}, 
 az:{brand:'AKUL / TƏLİMATLAR',eyebrow:'BEIN SYSTEMS · KÖMƏK MƏRKƏZİ',title:'Komandanız üçün təlimatlar',intro:'Bilik bazasının məqalələri və addım-addım dərslər vahid kataloqda.',search:'Təlimatlarda axtarış',searchHint:'Məsələn: anbar, satış, köçürmə…',all:'Bütün təlimatlar',articles:'Məqalələr bazası',lessons:'Addım-addım dərslər',allStatus:'Bütün statuslar',draft:'Qaralama',pending_review:'Yoxlanılır',approved:'Təsdiqlənib',published:'Dərc edilib',archived:'Arxivdə',newArticle:'Yeni məqalə',newLesson:'Yeni dərs',createAI:'AI ilə yarat',manageArticles:'Məqalə redaktoru və ixrac',open:'Aç',edit:'Redaktə et',section:'Bölmə',typeArticle:'Bilik bazası məqaləsi',typeLesson:'Addım-addım dərs',empty:'Təlimat tapılmadı.',loading:'Kataloq yüklənir…',error:'Kataloqu yükləmək mümkün olmadı.',signIn:'Şirkət təlimatlarına baxmaq üçün tətbiqə daxil olun.',back:'Kataloqa qayıt',read:'Təlimata baxış',noText:'Bu məqalədə hələ mətn yoxdur.',articlesUnavailable:'Məqalələr bazası hazırda əlçatan deyil.',lessonsUnavailable:'Dərc edilmiş dərslər hazırda əlçatan deyil.',public:'Şirkət əməkdaşları üçün dərc edilib',articleDraft:'Məqalə qaralaması',articlePending:'Dərc olunmamış dəyişikliklər var',unknown:'Status',connection:'Serverlə əlaqə yoxdur.',editorTitle:'Təlimat redaktoru',tree:'Bölmələr',resultCount:'təlimat',images:'Şəkillər'},
 en:{brand:'AKUL / INSTRUCTIONS',eyebrow:'BEIN SYSTEMS · HELP CENTER',title:'Instructions for your team',intro:'Guide articles and step-by-step lessons in one catalog.',search:'Search instructions',searchHint:'For example: inventory, sales, transfer…',all:'All instructions',articles:'Article library',lessons:'Step-by-step lessons',allStatus:'All statuses',draft:'Draft',pending_review:'In review',approved:'Approved',published:'Published',archived:'Archived',newArticle:'New article',newLesson:'New lesson',createAI:'Create with AI',manageArticles:'Article editor and export',open:'Open',edit:'Edit',section:'Section',typeArticle:'Guide article',typeLesson:'Step-by-step lesson',empty:'No matching instructions.',loading:'Loading catalog…',error:'Could not load the catalog.',signIn:'Sign in to view your company lessons.',back:'Back to catalog',read:'Instruction preview',noText:'This article has no text yet.',articlesUnavailable:'The article library is unavailable.',lessonsUnavailable:'Published lessons are unavailable.',public:'Published for company employees',articleDraft:'Article draft',articlePending:'Unpublished edits exist',unknown:'Status',connection:'Could not reach the server.',editorTitle:'Instruction editor',tree:'Sections',resultCount:'instructions',images:'Images'}
};
Object.assign(words,{
 guideScope:['Общий справочник: статьи видны всем компаниям. Изменения в редакторе влияют на общий справочник.','Ümumi məlumat bazası: məqalələr bütün şirkətlər üçün görünür. Redaktordakı dəyişikliklər ümumi bazaya təsir edir.','Shared guide: articles are visible to every company. Changes in the editor affect the shared guide.'],
 guideGlobalConfirm:['Это общий справочник для всех компаний. Изменения будут видны всем. Открыть редактор?','Bu bütün şirkətlər üçün ümumi məlumat bazasıdır. Dəyişikliklər hamıya görünəcək. Redaktoru açmaq istəyirsiniz?','This guide is shared across all companies. Changes will be visible to everyone. Open the editor?'],
 moreLessons:['Загрузить ещё уроки','Daha çox dərs yüklə','Load more lessons'],
 moreAvailable:['Есть дополнительные уроки. Нажмите, чтобы загрузить следующую ограниченную часть.','Əlavə dərslər var. Növbəti məhdud hissəni yükləmək üçün klikləyin.','More lessons are available. Load the next limited batch.'],
 loadingMore:['Загружаем уроки…','Dərslər yüklənir…','Loading lessons…'],
 moreLoadError:['Не удалось загрузить следующую часть. Попробуйте ещё раз.','Növbəti hissəni yükləmək alınmadı. Yenidən cəhd edin.','Could not load the next batch. Try again.'],
 discardConfirm:['Есть несохранённые изменения. Отбросить их и закрыть редактор?','Yadda saxlanmamış dəyişikliklər var. Onları ləğv edib redaktoru bağlamaq istəyirsiniz?','There are unsaved changes. Discard them and close the editor?'],
 closeError:['Редактор не подтвердил закрытие. Оставил его открытым, чтобы не потерять изменения.','Redaktor bağlanmanı təsdiqləmədi. Dəyişikliklər itməsin deyə onu açıq saxladım.','The editor did not confirm closing. It remains open so changes are not lost.']
});
const t=k=>(words[lang]&&words[lang][k])||k;
const byId=id=>document.getElementById(id);
let member=null,canManageLessons=false,canManageGuides=false,guidePublic=[],guideManaged=[],publishedLessons=[],managedLessons=[],selected=null,scope='all',player=null,epoch=0,searchTimer=null,guideUnavailable=false,lessonsUnavailable=false,guideFeedLoaded=false,publishedNextOffset=null,managedNextOffset=null,loadingMore=false,refreshing=false,loadedQuery='',loadedState='',loadedProfile='',editorDirty=false,editorCloseToken=0;
const MAX_PAGES_PER_BATCH=5;
function make(tag,text,attrs){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(attrs)for(const[k,v]of Object.entries(attrs))e.setAttribute(k,String(v));return e}
function profileKey(profile){return profile?String(profile.tenant_id)+':'+String(profile.telegram_id):''}
function idParams(profile=member){return new URLSearchParams({expected_tenant_id:String(profile.tenant_id),expected_user_id:String(profile.telegram_id)})}
async function responseJson(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});let body={};try{body=await r.json()}catch{}return{r,body}}
function errorBox(text){const n=byId('catalogStatus');if(n)n.textContent=text}
function noteStatus(item){return t(item.status||'unknown')}
function sameArticle(a,b){return ['title','slug','category','body','body_format'].every(k=>(a&&a[k]||'')===(b&&b[k]||''))}
async function loadGuide(profile){
 let publicRows=[],managedRows=[],canManage=false,feedLoaded=false,unavailable=false;
 try{const{r,body}=await responseJson('/api/guides/public');if(r.ok&&body.success&&Array.isArray(body.items)){publicRows=body.items;feedLoaded=true}}catch{}
 try{const{r,body}=await responseJson('/api/guides');if(r.ok&&body.success&&Array.isArray(body.items)){managedRows=body.items;canManage=true}}catch{}
 unavailable=!feedLoaded&&!canManage;
 const publicBySlug=new Map(publicRows.map(x=>[x.slug,x]));
 let normalized;
 if(canManage)normalized=managedRows.flatMap(row=>{const pub=row.published||publicBySlug.get(row.slug)||null,edit=row.draft||pub;if(!edit)return[];return[{kind:'guide',slug:row.slug,title:edit.title||row.slug,category:edit.category||'',body:edit.body||'',body_format:edit.body_format||'text',images:edit.images||[],status:!pub?'draft':sameArticle(edit,pub)?'published':'draft',published:!!pub,updated_at:row.updated_at||'',_pending:!!pub&&!sameArticle(edit,pub)}]});
 else normalized=publicRows.map(row=>({kind:'guide',slug:row.slug,title:row.title||row.slug,category:row.category||'',body:row.body||'',body_format:row.body_format||'text',images:row.images||[],status:'published',published:true}));
 return{guidePublic:normalized,guideManaged:managedRows,canManageGuides:canManage,guideUnavailable:unavailable,guideFeedLoaded:feedLoaded};
}
async function pages(url,offset=0){
 const items=[];let nextOffset=offset;
 if(!Number.isSafeInteger(offset)||offset<0)throw Error('Invalid page offset');
 for(let n=0;n<MAX_PAGES_PER_BATCH&&nextOffset!==null;n++){
  const currentOffset=nextOffset,target=new URL(url,location.origin);target.searchParams.set('offset',String(currentOffset));
  const{r,body}=await responseJson(target.href);if(!r.ok||!body.success)throw Error(body.error||r.status);
  const page=body.result||{};if(!Array.isArray(page.items))throw Error('Invalid catalog page');items.push(...page.items);
  if(page.next_offset==null){nextOffset=null;continue}
  const candidate=Number(page.next_offset);
  if(!Number.isSafeInteger(candidate)||candidate<=currentOffset)throw Error('Catalog pagination did not advance');
  nextOffset=candidate;
 }
 return{items,nextOffset};
}
async function loadLessonBatch(q,state,profile,{publishedOffset=0,managedOffset=0,includePublished=true,includeManaged=true}={}){
 let published={items:[],nextOffset:includePublished?publishedOffset:null},managed={items:[],nextOffset:includeManaged?managedOffset:null},publishedError=null,managedError=null;
 if(includePublished){const params=idParams(profile);params.set('q',q);try{published=await pages('/api/platform/instruction-catalog?'+params.toString(),publishedOffset)}catch(error){publishedError=error}}
 if(includeManaged){const params=idParams(profile);params.set('q',q);if(state)params.set('state',state);try{managed=await pages('/api/platform/instructions?'+params.toString(),managedOffset)}catch(error){managedError=error}}
 return{publishedLessons:published.items,managedLessons:managed.items,canManageLessons:includeManaged&&!managedError,
  lessonsUnavailable:!!publishedError,managedUnavailable:!!managedError,
  publishedNextOffset:publishedError?publishedOffset:published.nextOffset,
  managedNextOffset:managedError?managedOffset:managed.nextOffset};
}
function appendUnique(existing,incoming){
 const byId=new Map(existing.map(item=>[String(item.id),item]));
 for(const item of incoming){const key=String(item.id);byId.set(key,{...(byId.get(key)||{}),...item})}
 return Array.from(byId.values());
}
function catalogItems(state){
 const pubById=new Map(publishedLessons.map(x=>[String(x.id),x]));
 let lessons;
 if(canManageLessons){
  const managerById=new Map(managedLessons.map(x=>[String(x.id),x]));
  lessons=Array.from(new Set([...pubById.keys(),...managerById.keys()])).map(id=>{const pub=pubById.get(id),row=managerById.get(id);if(pub&&(!row||row.state==='published'))return{...pub,kind:'lesson',status:'published',published:true,version:row?.version||0};if(row){const status=row.state||'unknown';return{...row,kind:'lesson',title:row.title||pub?.title||'',operation:row.operation||pub?.operation||'',language:row.language||pub?.language||'',status,published:status==='published'}}return null}).filter(Boolean);
 }else lessons=publishedLessons.map(x=>({...x,kind:'lesson',status:'published',published:true}));
 if(state)lessons=lessons.filter(x=>x.status===state);
 const guides=guidePublic.filter(x=>!state||x.status===state);
 return [...guides,...lessons];
}
function matches(item,q){if(!q)return true;const needle=q.toLocaleLowerCase();return [item.title,item.category,item.slug,item.operation,item.body].some(x=>String(x||'').toLocaleLowerCase().includes(needle))}
function underPath(value,prefix,separator){return value===prefix||value.startsWith(prefix+separator)}
function inScope(item){
 if(scope==='all')return true;
 if(scope==='guide')return item.kind==='guide';
 if(scope==='lesson')return item.kind==='lesson';
 if(scope.startsWith('g:'))return item.kind==='guide'&&underPath(String(item.slug||''),scope.slice(2),'/');
 if(scope.startsWith('l:'))return item.kind==='lesson'&&underPath(String(item.operation||''),scope.slice(2),'.');
 return true;
}
function safeHTML(source){
 const parsed=new DOMParser().parseFromString(String(source||''),'text/html'),out=document.createDocumentFragment();
 const tags=new Set(['P','DIV','BR','H2','STRONG','B','EM','I','UL','OL','LI','A','TABLE','THEAD','TBODY','TR','TH','TD','BLOCKQUOTE']);
 function copy(node,parent){if(node.nodeType===3){parent.append(document.createTextNode(node.textContent));return}if(node.nodeType!==1)return;if(['SCRIPT','STYLE','OBJECT','SVG','MATH','LINK','META'].includes(node.tagName))return;
  if(node.tagName==='IFRAME'){const m=(node.getAttribute('src')||'').match(/^https:\/\/www\.youtube-nocookie\.com\/embed\/([A-Za-z0-9_-]{11})$/);if(m){const f=make('iframe');f.src=m[0];f.title='YouTube';f.loading='lazy';f.referrerPolicy='strict-origin-when-cross-origin';f.allowFullscreen=true;parent.append(f)}return}
  if(!tags.has(node.tagName)){for(const child of node.childNodes)copy(child,parent);return}
  const clean=make(node.tagName.toLowerCase());if(node.tagName==='A'){try{const u=new URL(node.getAttribute('href')||'',location.href);if(['http:','https:','mailto:','tel:'].includes(u.protocol)){clean.href=u.href;clean.rel='noopener noreferrer'}}catch{}}
  for(const child of node.childNodes)copy(child,clean);parent.append(clean)}
 for(const child of parsed.body.childNodes)copy(child,out);return out;
}
function editorFrame(){return byId('instructionEditorFrame')}
function editorHasUnsavedChanges(){
 const frame=editorFrame();try{if(typeof frame?.contentWindow?.tenantInstructionDirty==='function')return !!frame.contentWindow.tenantInstructionDirty()}catch{}
 return editorDirty;
}
function waitForEditorAck(type,ackType,timeout=1500){
 const frame=editorFrame();if(!frame?.contentWindow)return Promise.resolve(false);
 return new Promise(resolve=>{
  let done=false;const finish=value=>{if(done)return;done=true;clearTimeout(timer);window.removeEventListener('message',onMessage);resolve(value)};
  const onMessage=event=>{if(event.origin===location.origin&&event.source===frame.contentWindow&&event.data?.type===ackType)finish(true)};
  const timer=setTimeout(()=>finish(false),timeout);window.addEventListener('message',onMessage);
  try{frame.contentWindow.postMessage({type},location.origin)}catch{finish(false)}
 });
}
function openEditor(url,title){
 const dialog=byId('instructionEditor'),frame=editorFrame();if(!dialog||!frame)return;
 editorDirty=false;frame.title=title||t('editorTitle');frame.src=url;
 if(!dialog.open)dialog.showModal();
}
async function closeEditor(tenantChanged=false){
 const token=++editorCloseToken,dialog=byId('instructionEditor'),frame=editorFrame();
 if(!dialog?.open||!frame)return;
 const dirty=editorHasUnsavedChanges();
 if(tenantChanged){
  dialog.close();
  if(dirty){const ack=await waitForEditorAck('tenant-instruction-tenant-change','tenant-instruction-tenant-changed');if(token!==editorCloseToken)return;if(!ack){const replacement=document.createElement('iframe');replacement.id=frame.id;replacement.title=frame.title;replacement.setAttribute('referrerpolicy','no-referrer');frame.replaceWith(replacement);replacement.src='about:blank';editorDirty=false;return}}
  if(token===editorCloseToken){frame.src='about:blank';editorDirty=false}return;
 }
 if(dirty&&!window.confirm(t('discardConfirm')))return;
 if(dirty){const ack=await waitForEditorAck('tenant-instruction-discard','tenant-instruction-discarded');if(token!==editorCloseToken)return;if(!ack){errorBox(t('closeError'));return}}
 if(token!==editorCloseToken)return;
 dialog.close();frame.src='about:blank';editorDirty=false;await refresh();
}
function editURL(item){
 if(item.kind==='guide')return '/guide-editor?slug='+encodeURIComponent(item.slug);
 return '/app/instructions?lesson='+encodeURIComponent(item.id)+'&site_lang='+encodeURIComponent(lang);
}
function editItem(item){
 if(item.kind==='guide'&&(!canManageGuides||!window.confirm(t('guideGlobalConfirm'))))return;
 if(item.kind==='lesson'&&!canManageLessons)return;
 openEditor(editURL(item),t('editorTitle'));
}
function statusLabel(item){if(item.kind==='guide'&&item._pending)return t('articlePending');return t(item.status||'unknown')}
function makeTree(items){
 const guide={children:new Map(),items:[]},lesson={children:new Map(),items:[]};
 for(const item of items){const rootNode=item.kind==='guide'?guide:lesson,parts=item.kind==='guide'?String(item.slug||'').split('/').filter(Boolean):String(item.operation||'').split('.').filter(Boolean);let node=rootNode,path='';
  for(const part of parts){path=item.kind==='guide'?path+'/'+part:(path?path+'.':'')+part;if(!node.children.has(part))node.children.set(part,{label:part,path,children:new Map(),items:[]});node=node.children.get(part)}
  node.items.push(item)}
 return{guide,lesson}
}
function treeButton(parent,label,count,onClick,depth,selectedItem){const b=make('button',undefined,{type:'button',class:'catalog-tree-choice'+(selectedItem?' is-selected':'')});b.textContent=label+(count?' ('+count+')':'');b.style.paddingInlineStart=(12+depth*13)+'px';b.onclick=onClick;parent.append(b)}
function renderTreeBranch(parent,node,kind,depth,allItems){
 for(const child of node.children.values()){
  const own=allItems.filter(x=>kind==='guide'?underPath(String(x.slug||''),child.path,'/'):underPath(String(x.operation||''),child.path,'.'));
  treeButton(parent,child.label,own.length,()=>{scope=(kind==='guide'?'g:':'l:')+child.path;selected=null;render()},depth,scope===(kind==='guide'?'g:':'l:')+child.path);
  for(const item of child.items)treeButton(parent,item.title,0,()=>openItem(item),depth+1,selected&&selected.kind===item.kind&&String(selected.id||selected.slug)===String(item.id||item.slug));
  renderTreeBranch(parent,child,kind,depth+1,allItems);
 }
}
function renderTree(items){
 const host=byId('catalogTree');host.replaceChildren();
 treeButton(host,t('all'),items.length,()=>{scope='all';selected=null;render()},0,scope==='all');
 treeButton(host,t('articles'),items.filter(x=>x.kind==='guide').length,()=>{scope='guide';selected=null;render()},0,scope==='guide');
 const tree=makeTree(items);renderTreeBranch(host,tree.guide,'guide',1,items.filter(x=>x.kind==='guide'));
 treeButton(host,t('lessons'),items.filter(x=>x.kind==='lesson').length,()=>{scope='lesson';selected=null;render()},0,scope==='lesson');
 renderTreeBranch(host,tree.lesson,'lesson',1,items.filter(x=>x.kind==='lesson'));
}
function imageURL(item,id,profile=member){const p=idParams(profile);return '/api/platform/instruction-catalog/'+encodeURIComponent(item.id)+'/assets/'+encodeURIComponent(id)+'?'+p.toString()}
function openItem(item){
 selected=item;render();
 if(item.kind==='lesson'&&item.status==='published')loadLessonDetail(item).catch(e=>errorBox(e.message));
}
async function loadLessonDetail(item){
 const generation=epoch,profile=member,key=profileKey(profile),p=idParams(profile),url='/api/platform/instruction-catalog/'+encodeURIComponent(item.id)+'?'+p.toString();
 const{r,body}=await responseJson(url);if(generation!==epoch||key!==profileKey(member)||selected!==item)return;if(!r.ok||!body.success)throw Error(body.error||r.status);
 const stage=byId('catalogLessonPlayer');if(!stage||!window.BeinLesson)return;
 try{player?.destroy?.();player=window.BeinLesson.mount(stage,body.result.doc,{subtitles:body.result.doc.subtitles,resolveAsset:id=>imageURL(item,id,profile)})}catch(e){errorBox(e.message)}
}
function editorActions(item){
 const actions=make('div',undefined,{class:'catalog-actions'});
 if((item.kind==='guide'&&canManageGuides)||(item.kind==='lesson'&&canManageLessons))actions.append(makeButton(t('edit'),()=>editItem(item)));
 return actions;
}
function makeButton(label,fn){const b=make('button',label,{type:'button'});b.onclick=()=>{try{fn()}catch(e){errorBox(e.message)}};return b}
function card(item){
 const article=make('article',undefined,{class:'catalog-card','data-kind':item.kind,'data-status':item.status||'unknown'}),badge=make('span',item.kind==='guide'?t('typeArticle'):t('typeLesson'),{class:'catalog-type'});
 article.append(badge,make('span',statusLabel(item),{class:'catalog-status'}));
 const title=make('button',item.title||item.slug||item.id,{type:'button',class:'catalog-card-title'});title.onclick=()=>openItem(item);article.append(title);
 const meta=item.kind==='guide'?(item.category||item.slug):(item.operation||item.language);if(meta)article.append(make('p',meta,{class:'catalog-meta'}));
 const actions=make('div',undefined,{class:'catalog-actions'});actions.append(makeButton(t('open'),()=>openItem(item)));
 if((item.kind==='guide'&&canManageGuides)||(item.kind==='lesson'&&canManageLessons))actions.append(makeButton(t('edit'),()=>editItem(item)));
 article.append(actions);return article;
}
function backButton(){return makeButton(t('back'),()=>{selected=null;render()})}
function articleView(item,host){
 host.append(make('div',undefined,{class:'catalog-detail-tools'}));host.lastChild.append(backButton());
 host.append(make('div',undefined,{class:'catalog-detail-heading'}));const header=host.lastChild;header.append(make('span',item.kind==='guide'?t('typeArticle'):t('typeLesson'),{class:'catalog-type'}),make('span',statusLabel(item),{class:'catalog-status'}),make('h2',item.title||''));
 const metadata=item.category||item.slug;if(metadata)header.append(make('p',metadata,{class:'catalog-meta'}));
 if(item.kind==='guide'){
  const body=make('div',undefined,{class:'catalog-article-body'});
  if(item.body_format==='html')body.append(safeHTML(item.body));else body.textContent=item.body||t('noText');host.append(body);
  if(item.images?.length){const gallery=make('div',undefined,{class:'catalog-images'});for(const img of item.images){let src='';if(img.src){const u=new URL(img.src,location.origin);if(u.origin===location.origin&&u.pathname.startsWith('/api/guides/public/images/'))src=u.href}else if(canManageGuides&&img.data&&['image/jpeg','image/png'].includes(img.mime))src='data:'+img.mime+';base64,'+img.data;if(!src)continue;const figure=make('figure');figure.append(make('img',undefined,{src,alt:img.alt||'',loading:'lazy'}));if(img.alt)figure.append(make('figcaption',img.alt));gallery.append(figure)}host.append(gallery)}
 }else if(item.status==='published'){
  host.append(make('p',t('public'),{class:'catalog-meta'}),make('div',undefined,{id:'catalogLessonPlayer',class:'catalog-lesson-player'}));
 }else{
  host.append(make('p',statusLabel(item),{class:'catalog-read-status'}));
 }
 const actions=editorActions(item);if(actions.childNodes.length)host.append(actions);
}
function render(){
 if(!root)return;player?.destroy?.();player=null;
 const search=byId('catalogSearch'),state=byId('catalogState'),q=(search?.value||'').trim(),status=canManageLessons?(state?.value||''):'';
 const items=catalogItems(status).filter(x=>matches(x,q));
 renderTree(items);const title=byId('catalogHeading');title.textContent=selected?(selected.title||''):scope==='all'?t('all'):scope==='guide'?t('articles'):scope==='lesson'?t('lessons'):scope.startsWith('g:')||scope.startsWith('l:')?scope.slice(2).split(/[/.]/).pop():t('section');
 const results=byId('catalogResults');results.replaceChildren();
 const hasMore=publishedNextOffset!==null||managedNextOffset!==null,more=byId('catalogLoadMore'),moreStatus=byId('catalogMoreStatus');
 more.hidden=refreshing||selected!==null||!hasMore;more.disabled=refreshing||loadingMore;more.textContent=loadingMore?t('loadingMore'):t('moreLessons');
 moreStatus.textContent=refreshing?t('loading'):loadingMore?t('loadingMore'):hasMore?t('moreAvailable'):'';
 let shown=items;if(!q)shown=shown.filter(inScope);
 byId('catalogCount').textContent=shown.length+' '+t('resultCount');
 if(selected){const detail=make('div',undefined,{class:'catalog-detail'});articleView(selected,detail);results.append(detail);return}
 if(!shown.length){results.append(make('p',t('empty'),{class:'catalog-empty'}));return}
 for(const item of shown)results.append(card(item));
}
function warningsText(){const warnings=[];if(guideUnavailable)warnings.push(t('articlesUnavailable'));if(lessonsUnavailable)warnings.push(t('lessonsUnavailable'));return warnings.join(' · ')}
function resetLessonPagination(){publishedNextOffset=null;managedNextOffset=null;loadingMore=false;loadedQuery='';loadedState='';loadedProfile=''}
async function refresh(){
 const request=++epoch,q=(byId('catalogSearch')?.value||'').trim(),state=byId('catalogState')?.value||'';
 refreshing=true;loadingMore=false;errorBox(t('loading'));render();
 try{
  const{r,body}=await responseJson('/api/platform/me');if(request!==epoch)return;
  if(!r.ok||!body.member)throw Error(t('signIn'));
  const profile=body.member,key=profileKey(profile);
  if(member&&profileKey(member)!==key){
   selected=null;player?.destroy?.();player=null;guidePublic=[];guideManaged=[];publishedLessons=[];managedLessons=[];canManageGuides=false;canManageLessons=false;resetLessonPagination();
   void closeEditor(true);render();
  }
  const[guideData,lessonData]=await Promise.all([loadGuide(profile),loadLessonBatch(q,state,profile,{includePublished:!state||state==='published',includeManaged:true})]);
  if(request!==epoch)return;
  member=profile;guidePublic=guideData.guidePublic;guideManaged=guideData.guideManaged;canManageGuides=guideData.canManageGuides;guideUnavailable=guideData.guideUnavailable;guideFeedLoaded=guideData.guideFeedLoaded;
  publishedLessons=lessonData.publishedLessons;managedLessons=lessonData.managedLessons;canManageLessons=lessonData.canManageLessons;lessonsUnavailable=lessonData.lessonsUnavailable;
  publishedNextOffset=lessonData.lessonsUnavailable?null:lessonData.publishedNextOffset;managedNextOffset=lessonData.canManageLessons?lessonData.managedNextOffset:null;loadedQuery=q;loadedState=state;loadedProfile=key;
  refreshing=false;
  byId('newArticle').hidden=!canManageGuides;byId('manageGuides').hidden=!canManageGuides;byId('newLesson').hidden=!canManageLessons;byId('createAI').hidden=!canManageLessons;byId('catalogState').hidden=!canManageLessons;
  errorBox(warningsText());render();
 }catch(e){if(request!==epoch)return;refreshing=false;void closeEditor(true);clearCatalogData();errorBox(e.message||t('error'));render()}
}
async function loadMore(){
 if(!member||refreshing||loadingMore)return;
 const request=epoch,profile=member,key=profileKey(profile),q=(byId('catalogSearch')?.value||'').trim(),state=byId('catalogState')?.value||'';
 if(q!==loadedQuery||state!==loadedState||key!==loadedProfile){await refresh();return}
 const pubOffset=publishedNextOffset,managedOffset=managedNextOffset;
 if(pubOffset===null&&(!canManageLessons||managedOffset===null))return;
 loadingMore=true;render();
 try{
  const batch=await loadLessonBatch(q,state,profile,{publishedOffset:pubOffset??0,managedOffset:managedOffset??0,includePublished:pubOffset!==null,includeManaged:canManageLessons&&managedOffset!==null});
  if(request!==epoch||profileKey(member)!==key||loadedProfile!==key)return;
  if(batch.publishedUnavailable){lessonsUnavailable=true}else{publishedLessons=appendUnique(publishedLessons,batch.publishedLessons);publishedNextOffset=batch.publishedNextOffset}
  if(canManageLessons){
   if(batch.managedUnavailable){canManageLessons=false;managedLessons=[];managedNextOffset=null}
   else{managedLessons=appendUnique(managedLessons,batch.managedLessons);managedNextOffset=batch.managedNextOffset}
  }
  errorBox(warningsText());
 }catch(e){if(request===epoch&&profileKey(member)===key)errorBox(e.message||t('moreLoadError'))}
 finally{if(request===epoch&&profileKey(member)===key){loadingMore=false;render()}}
}
function applyLocale(){
 document.documentElement.lang=lang;
 document.title='BeinSystems · '+t('all');
 byId('catalogBrand').textContent=t('brand');byId('catalogEyebrow').textContent=t('eyebrow');
 byId('catalogTitle').textContent=t('title');byId('catalogIntro').textContent=t('intro');byId('catalogGlobalNotice').textContent=t('guideScope');
 byId('catalogTreeTitle').textContent=t('tree');byId('catalogTree').setAttribute('aria-label',t('tree'));
 byId('catalogSearch').placeholder=t('searchHint');byId('catalogSearch').setAttribute('aria-label',t('search'));
 byId('catalogState').setAttribute('aria-label',t('allStatus'));
 byId('newArticle').textContent=t('newArticle');byId('newLesson').textContent=t('newLesson');
 byId('createAI').textContent=t('createAI');byId('manageGuides').textContent=t('manageArticles');
 byId('closeInstructionEditor').setAttribute('aria-label',t('back'));
}
function boot(){
 applyLocale();
 const status=byId('catalogState');if(status)for(const key of ['','draft','pending_review','approved','published','archived'])status.append(make('option',key?t(key):t('allStatus'),{value:key}));
 const search=byId('catalogSearch');search?.addEventListener('input',()=>{selected=null;clearTimeout(searchTimer);searchTimer=setTimeout(()=>refresh(),220)});
 status?.addEventListener('change',()=>{selected=null;refresh()});
 byId('newArticle')?.addEventListener('click',()=>{if(!canManageGuides||!window.confirm(t('guideGlobalConfirm')))return;openEditor('/guide-editor?new=1&parent='+encodeURIComponent(scope.startsWith('g:')?scope.slice(2):''),t('editorTitle'))});
 byId('newLesson')?.addEventListener('click',()=>openEditor('/app/instructions?lesson_new=1&site_lang='+encodeURIComponent(lang),t('editorTitle')));
 byId('manageGuides')?.addEventListener('click',event=>{if(!canManageGuides||!window.confirm(t('guideGlobalConfirm')))event.preventDefault()});
 byId('catalogLoadMore')?.addEventListener('click',()=>{void loadMore()});
 byId('createAI')?.addEventListener('click',event=>{event.preventDefault();location.href='/app/ai-instructor?site_lang='+encodeURIComponent(lang)});
 byId('closeInstructionEditor')?.addEventListener('click',()=>{void closeEditor(false)});
 byId('instructionEditor')?.addEventListener('cancel',event=>{event.preventDefault();void closeEditor(false)});
 window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==editorFrame()?.contentWindow)return;if(event.data?.type==='tenant-instruction-dirty')editorDirty=!!event.data.dirty});
 document.addEventListener('tenant-profile',()=>{epoch++;void closeEditor(true);player?.destroy?.();player=null;member=null;canManageGuides=false;canManageLessons=false;guidePublic=[];guideManaged=[];publishedLessons=[];managedLessons=[];guideUnavailable=false;lessonsUnavailable=false;selected=null;refreshing=false;resetLessonPagination();errorBox(t('loading'));render();refresh()});
 refresh();
}
boot();
})();