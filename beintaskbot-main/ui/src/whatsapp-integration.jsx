import React, {useEffect, useRef, useState} from 'react';
import {Box, Button, Flex, Input, Stack, Text} from '@chakra-ui/react';

const copy = {
  ru: {
    intro: 'Один QR для переписки и звонков. Connect остаётся отдельной резервной интеграцией.',
    connect: 'Подключить номер', refresh: 'Обновить', name: 'Имя', number: 'Номер', pipeline: 'Воронка', status: 'Статус', actions: 'Действия',
    noNumbers: 'Подключённых номеров пока нет.', messagesReady: 'Переписка подключена', messagesPending: 'Ожидает подключения', messagesUnavailable: 'Переписка недоступна',
    callsReady: 'Звонки подключены', callsUnavailable: 'Звонки недоступны', continue: 'Продолжить подключение', details: 'Подключение',
    formTitle: 'Подключение номера', existing: 'Если номер уже есть в Connect, сначала отключите его там. Здесь укажите тот же номер — источник, история и права сохранятся.',
    phone: 'Номер WhatsApp', sourceName: 'Название источника', prepare: 'Подготовка…', generate: 'Сгенерировать QR',
    scan: 'WhatsApp → Связанные устройства → Привязка устройства. Сканируйте только номером', ready: 'Переписка и звонки подключены.',
    mismatch: 'Отсканирован другой номер. Подключение не активировано.', confirming: 'Подтверждаем подключение…', waiting: 'Ожидаем подключения WhatsApp. Проверка обновляется автоматически.', back: 'К списку номеров',
    permission: 'Подключением номеров управляет владелец компании. Доступ сотрудников к диалогам и звонкам задаётся в правах источника.',
    changed: 'Доступ изменился. Откройте интеграцию заново.', callIconReady: 'Звонки подключены', callIconUnavailable: 'Звонки недоступны',
  },
  az: {
    intro: 'Mesajlar və zənglər üçün bir QR. Connect ayrıca ehtiyat inteqrasiya olaraq qalır.',
    connect: 'Nömrə qoş', refresh: 'Yenilə', name: 'Ad', number: 'Nömrə', pipeline: 'Satış xətti', status: 'Status', actions: 'Əməliyyatlar',
    noNumbers: 'Hələ qoşulmuş nömrə yoxdur.', messagesReady: 'Mesajlar qoşulub', messagesPending: 'Qoşulma gözlənilir', messagesUnavailable: 'Mesajlar əlçatan deyil',
    callsReady: 'Zənglər qoşulub', callsUnavailable: 'Zənglər əlçatan deyil', continue: 'Qoşulmanı davam et', details: 'Qoşulma',
    formTitle: 'Nömrənin qoşulması', existing: 'Nömrə Connect-də varsa, əvvəlcə orada ayırın. Burada eyni nömrəni daxil edin — mənbə, tarixçə və icazələr saxlanacaq.',
    phone: 'WhatsApp nömrəsi', sourceName: 'Mənbənin adı', prepare: 'Hazırlanır…', generate: 'QR yarat',
    scan: 'WhatsApp → Əlaqəli cihazlar → Cihazı əlaqələndir. Yalnız bu nömrə ilə skan edin:', ready: 'Mesajlar və zənglər qoşulub.',
    mismatch: 'Başqa nömrə skan edilib. Qoşulma aktivləşdirilməyib.', confirming: 'Qoşulma təsdiqlənir…', waiting: 'WhatsApp qoşulması gözlənilir. Yoxlama avtomatik yenilənir.', back: 'Nömrələr siyahısına',
    permission: 'Nömrələri şirkət sahibi qoşur. Əməkdaşların dialoq və zənglərə girişi mənbə icazələri ilə idarə olunur.',
    changed: 'Giriş dəyişib. İnteqrasiyanı yenidən açın.', callIconReady: 'Zənglər qoşulub', callIconUnavailable: 'Zənglər əlçatan deyil',
  },
  en: {
    intro: 'One QR for messages and calls. Connect remains a separate reserve integration.',
    connect: 'Connect number', refresh: 'Refresh', name: 'Name', number: 'Number', pipeline: 'Pipeline', status: 'Status', actions: 'Actions',
    noNumbers: 'No connected numbers yet.', messagesReady: 'Messages connected', messagesPending: 'Connection pending', messagesUnavailable: 'Messages unavailable',
    callsReady: 'Calls connected', callsUnavailable: 'Calls unavailable', continue: 'Continue setup', details: 'Connection',
    formTitle: 'Connect number', existing: 'If this number is already in Connect, disconnect it there first. Enter the same number here to keep its source, history, and permissions.',
    phone: 'WhatsApp number', sourceName: 'Source name', prepare: 'Preparing…', generate: 'Generate QR',
    scan: 'WhatsApp → Linked devices → Link a device. Scan only with this number:', ready: 'Messages and calls are connected.',
    mismatch: 'A different number was scanned. The connection was not activated.', confirming: 'Confirming connection…', waiting: 'Waiting for WhatsApp. Status refreshes automatically.', back: 'Back to numbers',
    permission: 'Only the company owner can connect numbers. Employee access to conversations and calls is controlled by source permissions.',
    changed: 'Access changed. Reopen the integration.', callIconReady: 'Calls connected', callIconUnavailable: 'Calls unavailable',
  },
};

export function WhatsAppIntegration({profile, onStatus}) {
  const [channels,setChannels]=useState([]),[form,setForm]=useState(false);
  const [phone,setPhone]=useState(''),[name,setName]=useState('');
  const [pair,setPair]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [invalid,setInvalid]=useState(false);
  const alive=useRef(false),generation=useRef(0),controllers=useRef(new Set());
  const owner=profile?.member?.role==='owner';
  const tenant=profile?.tenant?.id || profile?.member?.tenant_id;
  const actor=profile?.member?.telegram_id;
  const t=copy[window.tenantI18n?.language]||copy.az;
  const request=async(data,source)=>{
    if(!alive.current)throw new Error(t.changed);
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
  if(invalid)return <Text role="alert">{t.changed}</Text>;
  if(!owner)return <Text>{t.permission}</Text>;
  const number=value=>value?String(value).replace(/^\+?/, '+'):'—';
  return <Stack gap="4" data-whatsapp-integration="true">
    <Text color="app.muted" fontSize="sm">{t.intro}</Text>
    {error && <Box role="alert" p="3" borderWidth="1px" borderRadius="lg">{error}</Box>}
    {!form ? <>
      <Flex gap="2" wrap="wrap">
        <Button className="connect-number-add" onClick={()=>{setPair(null);setPhone('');setName('');setForm(true);setError('');}}>{t.connect}</Button>
        <Button className="outline" variant="outline" disabled={busy} onClick={()=>run(load)}>{t.refresh}</Button>
      </Flex>
      {channels.length===0 ? <Text color="app.muted" fontSize="sm">{t.noNumbers}</Text> : <Box className="connect-numbers-wrap" data-whatsapp-number-list="true">
        <table className="connect-numbers-table"><thead><tr><th scope="col">{t.name}</th><th scope="col">{t.number}</th><th scope="col">{t.pipeline}</th><th scope="col">{t.status}</th><th scope="col">{t.actions}</th></tr></thead><tbody>
          {channels.map(c=>{
            const callLabel=c.calls_ready?t.callIconReady:t.callIconUnavailable;
            return <tr key={c.id}>
              <td>{c.name||c.id}</td><td>{number(c.phone)}</td><td>—</td>
              <td><span className="connect-number-status" data-state={c.messages_ready?'connected':c.activation_pending?'warning':'other'}>{c.messages_ready?t.messagesReady:c.activation_pending?t.messagesPending:t.messagesUnavailable}</span>
                <span className="connect-call-status" data-state={c.calls_ready?'online':'unavailable'} role="img" title={callLabel} aria-label={callLabel}>
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M5 3h4l2 5-3 2c1.5 3 3 4.5 6 6l2-3 5 2v4c0 1-1 2-2 2C10 20 4 14 3 5c0-1 1-2 2-2Z"/></svg>
                </span>
              </td>
              <td className="connect-number-actions"><Button className="outline" size="sm" variant="outline" disabled={busy} onClick={()=>run(async()=>{const value=await request(c.activation_pending?{action:'begin',phone:c.phone,name:c.name}:null,c.activation_pending?null:c.id);if(alive.current){setPair(value);setForm(true);}})}>{c.activation_pending?t.continue:t.details}</Button></td>
            </tr>;
          })}
        </tbody></table>
      </Box>}
    </> : <Stack gap="4" data-whatsapp-setup="true">
      <Text fontWeight="700">{t.formTitle}</Text>
      {!pair && <>
        <Text fontSize="sm" color="app.muted">{t.existing}</Text>
        <label>{t.phone}<Input aria-label={t.phone} type="tel" autoComplete="tel" placeholder="+994…" value={phone} onChange={e=>setPhone(e.target.value)} disabled={busy}/></label>
        <label>{t.sourceName}<Input aria-label={t.sourceName} maxLength={100} value={name} onChange={e=>setName(e.target.value)} disabled={busy}/></label>
        <Button colorPalette="brand" disabled={busy || !/^\+?[1-9][0-9]{5,21}$/.test(phone)} onClick={()=>run(async()=>{const value=await request({action:'begin',phone,name});if(alive.current)setPair(value);})}>{busy?t.prepare:t.generate}</Button>
      </>}
      {pair && <>
        <Text fontWeight="600">{number(pair.phone)}</Text>
        {pair.state==='ready' && !pair.activation_pending ? <Text role="status">{t.ready}</Text> : pair.qr ? <>
          <Text fontSize="sm">{t.scan} {number(pair.phone)}.</Text>
          <img alt="QR подключения WhatsApp" src={'data:image/png;base64,'+pair.qr} style={{width:280,maxWidth:'100%',alignSelf:'center'}}/>
        </> : <Text role="status">{pair.state==='identity_mismatch'?t.mismatch:pair.state==='ready'?t.confirming:t.waiting}</Text>}
      </>}
      <Flex><Button className="outline" variant="outline" disabled={busy} onClick={()=>{generation.current++;setForm(false);setPair(null);run(load);}}>{t.back}</Button></Flex>
    </Stack>}
  </Stack>;
}
