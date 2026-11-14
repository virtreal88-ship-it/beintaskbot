import React, {useEffect, useRef, useState} from 'react';
import {stageStyle} from './deal-stage-style.js';
import {DealStageBadge} from './deal-stage-badge.jsx';

export const dealProfileScope = (data=window.tenantProfileSnapshot||{}) => JSON.stringify([data.member,data.capabilities,data.role_permissions,data.integrations]);

const copy = {
  az:{board:'Kanban',table:'Cədvəl',move:'Mərhələni dəyiş',open:'Aç',edit:'Redaktə et',empty:'Sövdələşmə yoxdur',saving:'Saxlanılır…',error:'Dəyişiklik saxlanmadı. Siyahını yeniləyin.',drag:'Daşımaq üçün tutun'},
  ru:{board:'Канбан',table:'Таблица',move:'Изменить этап',open:'Открыть',edit:'Редактировать',empty:'Нет сделок',saving:'Сохраняем…',error:'Изменение не сохранено. Обновите список.',drag:'Удерживайте для переноса'},
  en:{board:'Kanban',table:'Table',move:'Change stage',open:'Open',edit:'Edit',empty:'No deals',saving:'Saving…',error:'Change not saved. Refresh the list.',drag:'Hold to move'},
};
export function NativeDealBoard({rows=[], settings={}}) {
  const c=copy[document.documentElement.lang]||copy.az;
  const [mode,setMode]=useState('board'), [items,setItems]=useState(rows), [pending,setPending]=useState(''), [error,setError]=useState('');
  const latestRows=useRef(rows); latestRows.current=rows;
  const alive=useRef(true), generation=useRef(0), busy=useRef(false), drag=useRef(null);
  const identity=`${settings.tenantId}:${settings.userId}`;
  useEffect(()=>{setItems(old=>rows.map(row=>pending===String(row.id)?(old.find(item=>item.id===row.id)||row):row));},[rows]);
  useEffect(()=>{alive.current=true;const clear=()=>{generation.current++;busy.current=false;drag.current=null;setPending('');setItems([]);};let scope=dealProfileScope();const changed=event=>{const next=dealProfileScope(event.detail||{});if(!event.detail?.member||next!==scope){scope=next;clear();}};document.addEventListener('tenant-profile',changed);document.addEventListener('tenant-auth-invalid',clear);return()=>{alive.current=false;clear();document.removeEventListener('tenant-profile',changed);document.removeEventListener('tenant-auth-invalid',clear);};},[identity]);
  const workflows=settings.workflows||[];
  const canMove=row=>settings.canUpdate===true && row.actions?.update===true && !!row.updated_at && targets(row).length>0 && !pending;
  const targets=row=>workflows.filter(w=>String(w.pipeline_id)===String(row.pipeline_id));
  const move=async(row,stage)=>{
    if(busy.current||!canMove(row)||String(row.stage_id)===String(stage)||!targets(row).some(w=>String(w.stage_id)===String(stage)))return;
    const token=generation.current, previousItems=items, sourceRows=latestRows.current;busy.current=true;setPending(String(row.id));setError('');
    setItems(value=>value.map(r=>r.id===row.id?{...r,stage_id:stage}:r));
    try {
      const result=await settings.onMove(row,stage);
      if(!alive.current||generation.current!==token)return;
      setItems(value=>value.map(r=>r.id===row.id?{...r,...result}:r));
    } catch(e) {if(alive.current&&generation.current===token){setItems(latestRows.current===sourceRows?previousItems:latestRows.current);setError(e.message||c.error);}}
    finally {if(alive.current&&generation.current===token){busy.current=false;setPending('');}}
  };
  const groups=workflows.map(w=>({...w,rows:items.filter(r=>String(r.pipeline_id)===String(w.pipeline_id)&&String(r.stage_id)===String(w.stage_id))}));
  for(const row of items){const group=groups.find(g=>String(g.pipeline_id)===String(row.pipeline_id)&&String(g.stage_id)===String(row.stage_id));if(!group)groups.push({pipeline_id:row.pipeline_id,stage_id:row.stage_id,pipeline_name:row.pipeline_id,stage_name:row.stage_id,rows:[row]});else if(!group.rows.some(r=>r.id===row.id))group.rows.push(row);}
  const appearance=g=>stageStyle(g.color || g.stage_color, Math.max(0,workflows.filter(w=>String(w.pipeline_id)===String(g.pipeline_id)).findIndex(w=>String(w.stage_id)===String(g.stage_id))));
  const stageFor=row=>groups.find(g=>String(g.pipeline_id)===String(row.pipeline_id)&&String(g.stage_id)===String(row.stage_id));
  const card=row=><article key={row.id} data-native-deal-card={row.id} aria-busy={pending===String(row.id)} draggable={canMove(row)} onDragStart={e=>{if(!canMove(row)){e.preventDefault();return;}drag.current=row;e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',String(row.id));}} onDragEnd={()=>{drag.current=null;}} style={{background:'var(--card,#fff)',border:'1px solid var(--line,#ddd)',borderRadius:12,padding:12,marginTop:8}}>
    <DealStageBadge name={String(stageFor(row)?.stage_name || row.stage_id)} appearance={appearance(stageFor(row) || row)}/><br/>
    <strong>{row.title}</strong><p>{row.contact_name||''}</p>
    <div style={{display:'flex',gap:8,flexWrap:'wrap'}}><button className="outline" onClick={()=>settings.onOpen?.(row.id)}>{c.open}</button>{canMove(row)&&<button className="outline" onClick={()=>settings.onEdit?.(row.id)}>{c.edit}</button>}</div>
    {settings.canUpdate===true&&row.actions?.update===true&&<label style={{display:'block',marginTop:8}}>{c.move}<select aria-label={`${c.move}: ${row.title}`} disabled={!canMove(row)} value={row.stage_id} onChange={e=>move(row,e.target.value)} style={{display:'block',width:'100%',minHeight:44}}>{targets(row).map(w=><option key={w.stage_id} value={w.stage_id}>{w.stage_name}</option>)}</select></label>}
    {canMove(row)&&<button className="outline" aria-label={`${c.drag}: ${row.title}`} style={{touchAction:'none',minHeight:44,marginTop:8}} onPointerDown={e=>{if(e.pointerType==='mouse')return;drag.current=row;e.currentTarget.setPointerCapture(e.pointerId);}} onPointerUp={e=>{const selected=drag.current;drag.current=null;if(!selected||e.pointerType==='mouse')return;const col=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-native-stage]');if(col&&col.dataset.nativePipeline===String(row.pipeline_id))move(row,col.dataset.nativeStage);}} onPointerCancel={()=>{drag.current=null;}}>{c.drag}</button>}
    {pending===String(row.id)&&<p role="status">{c.saving}</p>}
  </article>;
  return <section data-native-deal-board=""><div role="group" style={{display:'flex',gap:8}}>{['board','table'].map(m=><button key={m} className="outline" aria-pressed={mode===m} onClick={()=>setMode(m)}>{c[m]}</button>)}</div>{error&&<p role="alert">{error}</p>}{!items.length&&<p>{c.empty}</p>}{mode==='table'?items.map(card):<div style={{display:'flex',overflowX:'auto',gap:16,alignItems:'flex-start'}}>{groups.map(g=><section key={`${g.pipeline_id}:${g.stage_id}`} data-native-stage={g.stage_id} data-native-pipeline={g.pipeline_id} onDragOver={e=>{if(drag.current&&String(drag.current.pipeline_id)===String(g.pipeline_id)){e.preventDefault();e.dataTransfer.dropEffect='move';}}} onDrop={e=>{e.preventDefault();const row=drag.current;drag.current=null;if(row&&String(row.pipeline_id)===String(g.pipeline_id))move(row,g.stage_id);}} style={{flex:'0 0 280px',maxWidth:'82vw',minHeight:150,padding:8}}><div data-deal-stage-header="" style={{borderBottom:`2px solid ${appearance(g).color}`,paddingBottom:12,marginBottom:12,overflowWrap:"anywhere"}}><small>{g.pipeline_name}</small><h3>{g.stage_name} · {g.rows.length}</h3></div>{g.rows.map(card)}</section>)}</div>}</section>;
}
