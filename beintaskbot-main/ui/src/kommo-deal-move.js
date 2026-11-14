const stageCopy={
  en:{refresh:'Sync and refresh the deal before moving it.',storage:'Command storage unavailable.',saved:'Command could not be saved.',previous:'Check the previous stage move before sending another.',unknown:'Result unknown. Repeat the same move to check; do not create another request.',receipt:'Stage move receipt is invalid. Check the same request.',denied:'This stage is outside your assigned funnel.',changed:'The deal changed. Refresh the list before moving it.'},
  ru:{refresh:'Синхронизируйте и обновите сделку перед переносом.',storage:'Хранилище запроса недоступно.',saved:'Не удалось сохранить запрос.',previous:'Сначала проверьте предыдущий перенос.',unknown:'Результат пока неизвестен. Повторите тот же перенос для проверки, новый запрос не отправится.',receipt:'Ответ о переносе не подтверждён. Проверьте тот же запрос.',denied:'Этот этап вне назначенной вам воронки.',changed:'Сделка изменилась. Обновите список перед переносом.'},
  az:{refresh:'Daşımadan əvvəl sövdələşməni sinxronlaşdırıb yeniləyin.',storage:'Sorğu yaddaşı əlçatan deyil.',saved:'Sorğunu yadda saxlamaq olmadı.',previous:'Əvvəlki yerdəyişmənin nəticəsini yoxlayın.',unknown:'Nəticə hələ məlum deyil. Yoxlama üçün eyni mərhələyə yenidən daşıyın; yeni sorğu göndərilməyəcək.',receipt:'Yerdəyişmə cavabı təsdiqlənmədi. Eyni sorğunu yoxlayın.',denied:'Bu mərhələ sizə təyin edilmiş vərəqə aid deyil.',changed:'Sövdələşmə dəyişib. Daşımadan əvvəl siyahını yeniləyin.'}
};
const message=key=>(stageCopy[globalThis.document?.documentElement?.lang]||stageCopy.en)[key];
// Persist the immutable command before send. An uncertain request is only
// verified under its original ID; never manufacture a retry PATCH.
export function canIdentifyKommoDeal(row){return /^[a-f0-9]{64}$/.test(row.source_revision||'')&&['kommo_lead_id','pipeline_id','status_id'].every(key=>typeof row[key]==='number'?Number.isSafeInteger(row[key])&&row[key]>0:/^[1-9][0-9]{0,18}$/.test(String(row[key])));}
export async function moveKommoDeal(row, target, context) {
  if(!canIdentifyKommoDeal(row))throw Error(message('refresh'));
  const key=`kommo-stage:${context.tenant_id}:${context.user_id}:${row.kommo_lead_id}`;
  const expected=Number(row.raw?.updated_at)||Math.floor(Date.parse(row.source_updated_at)/1000);
  const payload={source_revision:row.source_revision,lead_id:String(row.kommo_lead_id),expected_pipeline_id:String(row.pipeline_id),expected_status_id:String(row.status_id),expected_updated_at:String(expected),stage_id:String(target)};
  if(!Number.isSafeInteger(expected)||expected<=0)throw Error(message('refresh'));
  let saved;try{saved=JSON.parse(sessionStorage.getItem(key)||'null');}catch{throw Error(message('storage'));}
  if(saved&&String(saved.stage_id)!==String(target))throw Error(message('previous'));
  const command=saved||{...payload,request_id:crypto.randomUUID()};
  try{sessionStorage.setItem(key,JSON.stringify(command));}catch{throw Error(message('saved'));}
  let response,result;
  try{response=saved?await fetch('/api/platform/crm/deals/move-stage?'+new URLSearchParams({request_id:command.request_id,expected_tenant_id:context.tenant_id,expected_user_id:context.user_id}),{credentials:'same-origin'}):await fetch('/api/platform/crm/deals/move-stage',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({...command,expected_tenant_id:context.tenant_id,expected_user_id:context.user_id})});result=await response.json();}
  catch{throw Error(message('unknown'));}
  if(!response.ok||!result.success){if(result.keep_request===false)sessionStorage.removeItem(key);throw Error(message(result.error==='kommo_stage_forbidden'?'denied':result.error==='kommo_deal_changed'?'changed':result.error==='board_source_sync_required'?'refresh':'unknown'));}
  if(result.state!=='confirmed'||String(result.lead_id)!==String(row.kommo_lead_id)||String(result.pipeline_id)!==String(row.pipeline_id)||String(result.status_id)!==String(target)||!Number.isSafeInteger(result.updated_at)||!/^[a-f0-9]{64}$/.test(result.source_revision||''))throw Error(message('receipt'));
  sessionStorage.removeItem(key);
  return {...row,source_revision:result.source_revision,status_id:result.status_id,source_updated_at:new Date(result.updated_at*1000).toISOString(),raw:{...row.raw,status_id:result.status_id,updated_at:result.updated_at}};
}
