/* Agreed AI channel contract v1. Namespaced IDs are UI keys, never runtime scopes. */
(()=>{
 'use strict';
 const categories=Object.freeze([
  {id:'support',name:'Техническая поддержка'}, {id:'sales',name:'Продажи'},
  {id:'service',name:'Обслуживание клиентов'}, {id:'automation',name:'Автоматизация процессов'}
 ]);
 const templates=Object.freeze([Object.freeze({id:'technical-support',category:'support',name:'Оператор технической поддержки',
  description:'Отвечает по базе знаний, уточняет проблему и передаёт сложные обращения сотруднику.',
  providers:['connect','kommo'],ready:true})]);
 function parse(result){
  if(result?.schema_version!==1||!Array.isArray(result.channels))throw new Error('Каталог подключений недоступен. Обновите страницу или обратитесь к администратору.');
  const seen=new Set();
  return result.channels.filter(row=>{
   if(!row||!['connect','kommo'].includes(row.provider)||typeof row.id!=='string'||!row.id||typeof row.channel_id!=='string'||!row.channel_id||seen.has(row.id))return false;
   if(row.scope?.provider!==row.provider||row.scope?.channel_id!==row.channel_id||row.id!==row.provider+':'+row.channel_id)return false;
   if(row.provider==='connect'&&row.type!=='whatsapp')return false;
   seen.add(row.id);return true;
  }).map(row=>({...row,selectable:row.ai?.supported===true&&row.ai?.available===true&&row.capabilities?.text_replies===true}));
 }
 function typeName(row){return row.provider==='kommo'?'Kommo CRM':'WhatsApp'}
 function connectionName(row){return `${typeName(row)} — ${row.name||row.channel_id}`}
 function scopes(rows,ids,pipeline='',stage=''){
  if(!Array.isArray(ids)||!ids.length||ids.length>100)throw new Error('Выберите хотя бы одно доступное подключение.');
  return [...new Set(ids)].map(id=>{
   const row=rows.find(item=>item.id===id&&item.selectable);
   if(!row)throw new Error('Выбранное подключение больше недоступно. Обновите каталог.');
   if(row.provider==='kommo'&&(!String(pipeline).trim()||!String(stage).trim()))throw new Error('Выберите воронку и этап Kommo.');
   return {provider:row.scope.provider,channel_id:row.scope.channel_id,pipeline_id:row.provider==='kommo'?String(pipeline):'',stage_id:row.provider==='kommo'?String(stage):'',instructions:'',allow_group_messages:false};
  });
 }
 const available=(rows,provider='all')=>rows.filter(row=>row.selectable&&(provider==='all'||row.provider===provider));
 function matching(rows,category='all',provider='all'){
  const providers=new Set(available(rows,provider).map(row=>row.provider));
  return templates.filter(item=>item.ready&&(category==='all'||item.category===category)&&item.providers.some(value=>providers.has(value)));
 }
 window.tenantAgentCatalog=Object.freeze({categories,templates,parse,scopes,available,matching,typeName,connectionName});
})();
