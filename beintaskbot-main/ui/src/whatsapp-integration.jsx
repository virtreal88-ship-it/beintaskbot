import React, {useEffect, useRef, useState} from 'react';
import {Box, Button, Flex, Input, Stack, Text} from '@chakra-ui/react';

export function WhatsAppIntegration({profile, onStatus}) {
  const [channels,setChannels]=useState([]),[form,setForm]=useState(false);
  const [phone,setPhone]=useState(''),[name,setName]=useState('');
  const [pair,setPair]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [invalid,setInvalid]=useState(false);
  const alive=useRef(false),generation=useRef(0),controllers=useRef(new Set());
  const owner=profile?.member?.role==='owner';
  const tenant=profile?.tenant?.id || profile?.member?.tenant_id;
  const actor=profile?.member?.telegram_id;
  const request=async(data,source)=>{
    if(!alive.current)throw new Error('Откройте интеграцию заново.');
    const controller=new AbortController();controllers.current.add(controller);
    try {
      const query=new URLSearchParams({expected_tenant_id:tenant,expected_user_id:actor});
      if(source)query.set('source',source);
      const response=await fetch('/api/platform/integrations/whatsapp?'+query,{
        method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json'},...(data?{body:JSON.stringify(data)}:{})});
      if(response.status===401 || response.status===403){
        alive.current=false;generation.current++;setPair(null);setChannels([]);setInvalid(true);
        for(const pending of controllers.current)if(pending!==controller)pending.abort();
      }
      const value=await response.json();
      if(!response.ok || !value.success)throw new Error(value.error || 'Не удалось проверить подключение.');
      return value.result;
    } finally {controllers.current.delete(controller);}
  };
  const load=async()=>{
    const value=await request();
    if(!alive.current)return;
    setChannels(value.channels);onStatus?.(value.channels.some(c=>c.messages_ready));
  };
  useEffect(()=>{
    alive.current=true;setInvalid(false);
    const invalidate=()=>{alive.current=false;generation.current++;setPair(null);setChannels([]);setInvalid(true);for(const c of controllers.current)c.abort();};
    const profileChanged=()=>{const fresh=window.tenantProfileSnapshot?.member;if(fresh && (String(fresh.tenant_id)!==String(tenant)||String(fresh.telegram_id)!==String(actor)||fresh.role!=='owner'))invalidate();};
    document.addEventListener('tenant-auth-invalid',invalidate);document.addEventListener('tenant-auth-pending',invalidate);document.addEventListener('tenant-profile',profileChanged);
    if(owner)load().catch(e=>{if(alive.current)setError(e.message);});
    return()=>{alive.current=false;generation.current++;for(const c of controllers.current)c.abort();controllers.current.clear();document.removeEventListener('tenant-auth-invalid',invalidate);document.removeEventListener('tenant-auth-pending',invalidate);document.removeEventListener('tenant-profile',profileChanged);};
  },[tenant,actor,owner]);
  useEffect(()=>{
    if(!pair?.channel_id || !form)return;
    let stopped=false,timer;
    const current=++generation.current;
    const poll=async()=>{
      try {
        let value=await request(null,pair.channel_id);
        if(stopped || !alive.current || current!==generation.current)return;
        if(value.state==='ready' && value.activation_pending){
          value=await request({action:'activate',source:pair.channel_id});
          if(stopped || !alive.current || current!==generation.current)return;
        }
        setPair(value);setError('');
        if(value.state==='ready' && !value.activation_pending){await load();return;}
      } catch(e){if(!stopped && alive.current)setError(e.message);}
      if(!stopped)timer=setTimeout(poll,5000);
    };
    timer=setTimeout(poll,1000);
    return()=>{stopped=true;clearTimeout(timer);};
  },[pair?.channel_id,form]);
  const run=async(action)=>{
    if(busy)return;setBusy(true);setError('');
    try{await action();}catch(e){if(alive.current)setError(e.message);}
    finally{if(alive.current)setBusy(false);}
  };
  if(invalid)return <Text role="alert">Доступ изменился. Откройте интеграцию заново.</Text>;
  if(!owner)return <Text>Подключением номеров управляет владелец компании. Доступ сотрудников к диалогам и звонкам задаётся в правах источника.</Text>;
  return <Stack gap="4" data-whatsapp-integration="true">
    <Text color="app.muted">Один QR для переписки и звонков. Connect остаётся отдельной резервной интеграцией.</Text>
    {error && <Box role="alert" p="3" borderWidth="1px" borderRadius="lg">{error}</Box>}
    {!form ? <>
      <Flex gap="2" wrap="wrap">
        <Button onClick={()=>{setPair(null);setPhone('');setName('');setForm(true);setError('');}}>Подключить номер</Button>
        <Button variant="outline" disabled={busy} onClick={()=>run(load)}>Обновить</Button>
      </Flex>
      {channels.length===0 && <Text color="app.muted">Подключённых номеров пока нет.</Text>}
      {channels.map(c=><Box key={c.id} p="4" borderWidth="1px" borderRadius="xl">
        <Flex justify="space-between" gap="3" wrap="wrap"><Box><Text fontWeight="700">{c.name}</Text><Text color="app.muted">+{c.phone}</Text></Box>
          <Button size="sm" variant="outline" disabled={busy} onClick={()=>run(async()=>{const value=await request(c.activation_pending?{action:'begin',phone:c.phone,name:c.name}:null,c.activation_pending?null:c.id);if(alive.current){setPair(value);setForm(true);}})}>{c.activation_pending?'Продолжить подключение':'Подключение'}</Button>
        </Flex>
        <Flex gap="4" mt="2" wrap="wrap"><Text fontSize="sm">Переписка: {c.messages_ready?'подключена':c.activation_pending?'ожидает подключения':'недоступна'}</Text><Text fontSize="sm">Звонки: {c.calls_ready?'подключены':'недоступны'}</Text></Flex>
      </Box>)}
    </> : <Box p="4" borderWidth="1px" borderRadius="xl">
      <Stack gap="3">
        <Text fontWeight="700">Подключение номера</Text>
        {!pair && <>
          <Text fontSize="sm">Если номер уже есть в Connect, сначала отключите его там. Здесь укажите тот же номер — источник, история и права сохранятся.</Text>
          <label>Номер WhatsApp<Input aria-label="Номер WhatsApp" type="tel" autoComplete="tel" placeholder="+994…" value={phone} onChange={e=>setPhone(e.target.value)} disabled={busy}/></label>
          <label>Название источника<Input aria-label="Название источника" maxLength={100} value={name} onChange={e=>setName(e.target.value)} disabled={busy}/></label>
          <Button disabled={busy || !/^\+?[1-9][0-9]{5,21}$/.test(phone)} onClick={()=>run(async()=>{const value=await request({action:'begin',phone,name});if(alive.current)setPair(value);})}>{busy?'Подготовка…':'Сгенерировать QR'}</Button>
        </>}
        {pair && <>
          <Text>+{pair.phone}</Text>
          {pair.state==='ready' && !pair.activation_pending ? <Text role="status">Переписка и звонки подключены.</Text> : pair.qr ? <>
            <Text>WhatsApp → Связанные устройства → Привязка устройства. Сканируйте только номером +{pair.phone}.</Text>
            <img alt="QR подключения WhatsApp" src={'data:image/png;base64,'+pair.qr} style={{width:280,maxWidth:'100%',alignSelf:'center'}}/>
          </> : <Text role="status">{pair.state==='identity_mismatch'?'Отсканирован другой номер. Подключение не активировано.':pair.state==='ready'?'Подтверждаем подключение…':'Ожидаем подключение WhatsApp. Проверка обновляется автоматически.'}</Text>}
        </>}
        <Button variant="outline" disabled={busy} onClick={()=>{generation.current++;setForm(false);setPair(null);run(load);}}>К списку номеров</Button>
      </Stack>
    </Box>}
  </Stack>;
}
