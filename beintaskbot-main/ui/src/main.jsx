import React, { useEffect, useMemo, useState } from "react"
import { createRoot } from "react-dom/client"
import {
  Badge, Box, Button, Card, ChakraProvider, Checkbox, Dialog, Flex, Grid,
  Heading, Input, Portal, Spinner, Stack, Table, Text, createSystem,
  defaultConfig, defineConfig,
} from "@chakra-ui/react"

const brandConfig = defineConfig({
  theme: {
    tokens: {
      colors: {
        brand: {
          50: { value: "#eefbf6" }, 100: { value: "#d8f5e9" },
          500: { value: "#0a926e" }, 600: { value: "#087a5d" },
          700: { value: "#086048" }, 900: { value: "#102923" },
        },
      },
      fonts: { body: { value: "Inter, ui-sans-serif, system-ui, sans-serif" } },
      radii: { crm: { value: "16px" } },
      shadows: { crm: { value: "0 8px 30px rgba(16,41,35,.08)" } },
    },
  },
})
const system = createSystem(defaultConfig, brandConfig)
const roots = new Map()
const roles = { owner: "Şirkət sahibi", admin: "Administrator", manager: "Vərəq istifadəçisi", worker: "İcraçı", master: "Usta" }
const permissionLabels = { deals:"Sövdələşmələr",tasks:"Tapşırıqlar",customers:"Müştərilər",hot_orders:"İsti sifarişlər",finance:"Maliyyə",linear:"Linear",settings:"Tənzimləmələr",employees:"Əməkdaşlar",integrations:"İnteqrasiyalar" }
const integrationCatalog = [
  { id:"kommo", name:"Kommo", category:"CRM", mark:"K", color:"#635BFF", description:"Sövdələşmələri, müştəriləri, tapşırıqları və mesaj tarixçəsini sinxronlaşdırır.", features:["Sövdələşmə və mərhələlər", "Tapşırıqlar", "Mesaj kanalları"], action:"setup" },
  { id:"linear", name:"Linear", category:"İş idarəetməsi", mark:"L", color:"#5E6AD2", description:"Məhsul tapşırıqlarını, təsdiq axınını və yeniliklərin hazırlanmasını idarə edir.", features:["Kanban və statuslar", "Tapşırıq bildirişləri", "Yenilik axını"], section:"integrations" },
  { id:"openai", name:"OpenAI", category:"Süni intellekt", mark:"AI", color:"#111827", description:"Chat cavabları, xülasə, transkripsiya və mətn redaktəsi üçün şirkət AI bağlantısıdır.", features:["Cavab qaralaması", "Səs transkripsiyası", "Xülasə və redaktə"], section:"ai" },
  { id:"telegram", name:"Telegram", category:"Rabitə", mark:"T", color:"#229ED9", description:"Əməkdaş girişləri, bildirişlər və təsdiqlənmiş yeniliklərin paylaşılması üçün istifadə olunur.", features:["Təhlükəsiz giriş", "Şəxsi bildirişlər", "Kanal nəşrləri"], section:"notifications" },
  { id:"wappi", name:"Wappi", category:"Rabitə", mark:"W", color:"#25D366", description:"WhatsApp kanalları üçün planlaşdırılan əlavə bağlantı.", features:["WhatsApp mesajları", "Kanal uyğunlaşdırması"], pending:true },
  { id:"beinsystems", name:"BeinSystems ERP", category:"CRM / ERP", mark:"B", color:"#0A926E", description:"BEIN ERP daxilində CRM, müştəri, tapşırıq, vərəq və mərhələ məlumatları ilə işləyəcək.", features:["Lead və müştərilər", "Tapşırıqlar", "Vərəq və mərhələlər"], pending:true },
]

function mount(id, node) {
  const host = document.getElementById(id)
  if (!host) return false
  host.classList.remove("table")
  if (!roots.has(id)) roots.set(id, createRoot(host))
  roots.get(id).render(<ChakraProvider value={system}>{node}</ChakraProvider>)
  return true
}
const formatDate = value => {
  if (!value) return "Tarix yoxdur"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? "Tarix yoxdur" : date.toLocaleString("en-GB", { day:"2-digit", month:"2-digit", year:"numeric", hour:"2-digit", minute:"2-digit" }).replace(",", "")
}
const api = async (url, options={}) => {
  const response = await fetch(url, { credentials:"same-origin", cache:"no-store", ...options,
    headers:{ "Content-Type":"application/json", ...(options.headers || {}) } })
  const result = await response.json().catch(() => ({ success:false, error:"Server cavabı oxunmadı." }))
  if (!response.ok || !result.success) throw new Error(result.error || "Sorğu alınmadı.")
  return result
}

function Empty({ children }) {
  return <Box py="14" px="5" textAlign="center" color="gray.500" bg="white" borderWidth="1px" borderColor="gray.200" borderRadius="xl">{children}</Box>
}
function StatusBadge({ children, tone="green" }) {
  return <Badge colorPalette={tone} variant="subtle" borderRadius="full" px="2.5" py="1" fontWeight="700">{children}</Badge>
}
function TasksTable({ rows=[] }) {
  if (!rows.length) return <Empty>Açıq tapşırıq yoxdur.</Empty>
  return <Card.Root borderColor="gray.200" borderRadius="2xl" overflow="hidden" shadow="sm"><Table.ScrollArea><Table.Root size="sm" variant="line" minW="720px">
    <Table.Header bg="gray.50"><Table.Row><Table.ColumnHeader>Tapşırıq</Table.ColumnHeader><Table.ColumnHeader>Sövdələşmə</Table.ColumnHeader><Table.ColumnHeader>Vəziyyət</Table.ColumnHeader><Table.ColumnHeader>Son tarix</Table.ColumnHeader><Table.ColumnHeader textAlign="end">Əməliyyat</Table.ColumnHeader></Table.Row></Table.Header>
    <Table.Body>{rows.map(task => <Table.Row key={task.kommo_task_id} _hover={{ bg:"brand.50" }}>
      <Table.Cell maxW="440px"><Text fontWeight="700" color="gray.800" lineClamp="2">{String(task.text || "Tapşırıq").replace(/\n?\[CRM:[a-f0-9]{32}\]/g, "")}</Text><Text fontSize="xs" color="gray.500">#{task.kommo_task_id}</Text></Table.Cell>
      <Table.Cell>{task.kommo_lead_id ? `#${task.kommo_lead_id}` : "Sövdələşməsiz"}</Table.Cell>
      <Table.Cell><StatusBadge tone={task.completed ? "gray" : "green"}>{task.completed ? "Tamamlandı" : "Açıq"}</StatusBadge></Table.Cell>
      <Table.Cell whiteSpace="nowrap">{formatDate(task.due_at)}</Table.Cell>
      <Table.Cell textAlign="end">{!task.completed && <Button size="xs" colorPalette="green" variant="outline" data-task-complete={task.kommo_task_id}>Tamamla</Button>}</Table.Cell>
    </Table.Row>)}</Table.Body>
  </Table.Root></Table.ScrollArea></Card.Root>
}

function DealTable({ rows=[], customer=false, onOpen }) {
  if (!rows.length) return <Empty>Bu filtr üzrə məlumat tapılmadı.</Empty>
  return <Card.Root borderColor="gray.200" borderRadius="2xl" overflow="hidden" shadow="sm"><Table.ScrollArea><Table.Root size="sm" minW="760px">
    <Table.Header bg="gray.50"><Table.Row><Table.ColumnHeader>{customer ? "Müştəri" : "Sövdələşmə"}</Table.ColumnHeader><Table.ColumnHeader>Mərhələ</Table.ColumnHeader><Table.ColumnHeader>Son mesaj</Table.ColumnHeader><Table.ColumnHeader>Yenilənib</Table.ColumnHeader></Table.Row></Table.Header>
    <Table.Body>{rows.map(deal => <Table.Row key={deal.kommo_lead_id} cursor="pointer" _hover={{ bg:"brand.50" }} onClick={() => onOpen?.(deal.kommo_lead_id)}>
      <Table.Cell><Text fontWeight="750" color="gray.800">{customer ? (deal.contact_name || deal.name) : deal.name}</Text><Text fontSize="xs" color="gray.500">{deal.phone || deal.contact_name || "Məlumat yoxdur"}</Text></Table.Cell>
      <Table.Cell><StatusBadge>{deal.stage_name || "Mərhələ"}</StatusBadge></Table.Cell>
      <Table.Cell maxW="330px"><Text truncate color="gray.600">{deal.last_message || "Son mesaj yoxdur"}</Text></Table.Cell>
      <Table.Cell whiteSpace="nowrap">{formatDate(deal.source_updated_at || deal.synced_at)}</Table.Cell>
    </Table.Row>)}</Table.Body>
  </Table.Root></Table.ScrollArea></Card.Root>
}

function DealKanban({ rows=[], onOpen }) {
  const groups = useMemo(() => {
    const result = new Map()
    rows.forEach(row => { const key=row.stage_name || "Mərhələ"; if(!result.has(key))result.set(key,[]); result.get(key).push(row) })
    return [...result.entries()]
  }, [rows])
  if (!groups.length) return <Empty>Kanbanda sövdələşmə yoxdur.</Empty>
  return <Flex gap="4" align="flex-start" overflowX="auto" pb="3">{groups.map(([stage, items]) => <Box key={stage} minW="300px" w="300px" bg="gray.100" borderRadius="2xl" p="3">
    <Flex align="center" justify="space-between" mb="3"><Heading size="sm">{stage}</Heading><Badge borderRadius="full">{items.length}</Badge></Flex>
    <Stack gap="3">{items.map(deal => <Card.Root key={deal.kommo_lead_id} borderWidth="1px" borderColor="gray.200" shadow="xs" cursor="pointer" _hover={{ shadow:"md", borderColor:"brand.500" }} onClick={() => onOpen?.(deal.kommo_lead_id)}><Card.Body p="4"><Text fontWeight="800">{deal.contact_name || deal.name}</Text><Text fontSize="sm" color="gray.600">{deal.phone || "Telefon yoxdur"}</Text><Text mt="2" fontSize="xs" color="gray.500" lineClamp="2">{deal.last_message || deal.name}</Text><Flex mt="3" justify="space-between" fontSize="xs" color="gray.500"><span>#{deal.kommo_lead_id}</span><span>{formatDate(deal.source_updated_at || deal.synced_at)}</span></Flex></Card.Body></Card.Root>)}</Stack>
  </Box>)}</Flex>
}

function DealsView({ rows=[], customer=false, onOpen }) {
  const [mode, setMode] = useState(customer ? "table" : (localStorage.getItem("tenant-deal-view") || "kanban"))
  if (customer) return <DealTable rows={rows} customer onOpen={onOpen} />
  return <Stack gap="3"><Flex justify="flex-end" gap="2"><Button size="sm" variant={mode === "table" ? "solid" : "outline"} colorPalette="green" onClick={() => {setMode("table");localStorage.setItem("tenant-deal-view","table")}}>Cədvəl</Button><Button size="sm" variant={mode === "kanban" ? "solid" : "outline"} colorPalette="green" onClick={() => {setMode("kanban");localStorage.setItem("tenant-deal-view","kanban")}}>Kanban</Button></Flex>{mode === "kanban" ? <DealKanban rows={rows} onOpen={onOpen} /> : <DealTable rows={rows} onOpen={onOpen} />}</Stack>
}

function IntegrationMarketplace() {
  const [profile,setProfile]=useState(null), [selected,setSelected]=useState(null), [query,setQuery]=useState(""), [category,setCategory]=useState("Hamısı"), [details,setDetails]=useState({}), [error,setError]=useState("")
  useEffect(() => { (async()=>{try{const me=await api("/api/platform/me");setProfile(me);const status={};(me.integrations||[]).forEach(row=>{const key=row.provider==="chat_ai"?"openai":row.provider;status[key]={connected:["connected","configured"].includes(row.status),label:row.status}});status.telegram={connected:!!me.member?.telegram_id,label:"connected"};if(me.member?.role==="owner"){const checks=await Promise.allSettled([api("/api/platform/linear/settings"),api("/api/platform/chat/ai/settings"),api("/api/platform/news/telegram/settings")]);if(checks[0].status==="fulfilled")status.linear={connected:checks[0].value.status==="connected",label:checks[0].value.status};if(checks[1].status==="fulfilled")status.openai={connected:!!checks[1].value.configured&&!!checks[1].value.enabled,label:checks[1].value.configured?"configured":"not_connected"};if(checks[2].status==="fulfilled")status.telegram={connected:true,channel:!!checks[2].value.configured,label:checks[2].value.configured?"channel_connected":"connected"}}setDetails(status)}catch(e){setError(e.message)}})() }, [])
  const categories=["Hamısı",...new Set(integrationCatalog.map(item=>item.category))]
  const rows=integrationCatalog.filter(item=>(category==="Hamısı"||item.category===category)&&[item.name,item.category,item.description].join(" ").toLocaleLowerCase().includes(query.toLocaleLowerCase()))
  const openConfiguration = item => { setSelected(null); if(item.action==="setup")location.href="/setup";else window.tenantOpenSettingsSection?.(item.section || "integrations") }
  if (!profile && !error) return <Flex py="16" justify="center"><Spinner color="brand.500"/></Flex>
  return <Stack gap="6">
    <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Body p={{base:"4",md:"5"}}><Flex direction={{base:"column",md:"row"}} gap="3" align={{md:"center"}} justify="space-between"><Box><Heading size="md">Tətbiq marketi</Heading><Text mt="1" color="gray.600" fontSize="sm">CRM Assistant imkanlarını şirkətinizə uyğun xidmətlərlə genişləndirin.</Text></Box><Input maxW={{md:"360px"}} bg="white" placeholder="İnteqrasiya axtarın" value={query} onChange={event=>setQuery(event.target.value)}/></Flex><Flex gap="2" mt="4" overflowX="auto" pb="1">{categories.map(item=><Button key={item} flex="none" size="sm" colorPalette="green" variant={category===item?"solid":"outline"} onClick={()=>setCategory(item)}>{item}</Button>)}</Flex></Card.Body></Card.Root>
    {error && <Box bg="red.50" color="red.700" p="4" borderRadius="xl">{error}</Box>}
    <Grid templateColumns={{base:"1fr",md:"repeat(2,minmax(0,1fr))",xl:"repeat(3,minmax(0,1fr))"}} gap="4">{rows.map(item=>{const state=details[item.id]||{};return <Card.Root key={item.id} borderRadius="2xl" borderColor="gray.200" shadow="sm" transition=".18s ease" _hover={{shadow:"md",transform:"translateY(-2px)",borderColor:"brand.500"}}><Card.Header pb="3"><Flex justify="space-between" gap="3"><Flex gap="3" align="center"><Flex w="12" h="12" borderRadius="xl" align="center" justify="center" bg={item.color} color="white" fontWeight="900">{item.mark}</Flex><Box><Card.Title>{item.name}</Card.Title><Text color="gray.500" fontSize="xs">{item.category}</Text></Box></Flex><StatusBadge tone={item.pending?"gray":state.connected?"green":"orange"}>{item.pending?"Tezliklə":state.connected?"Qoşulub":"Qoşulmayıb"}</StatusBadge></Flex></Card.Header><Card.Body pt="0"><Text color="gray.600" fontSize="sm" lineHeight="1.65">{item.description}</Text><Flex mt="4" gap="2" wrap="wrap">{item.features.map(feature=><Badge key={feature} variant="subtle" borderRadius="full" px="2.5">{feature}</Badge>)}</Flex></Card.Body><Card.Footer><Button w="full" colorPalette="green" variant={item.pending?"outline":"solid"} disabled={item.pending} onClick={()=>setSelected(item)}>{item.pending?"Gözləmə siyahısı":"Ətraflı və ayarlar"}</Button></Card.Footer></Card.Root>})}</Grid>
    {!rows.length && <Empty>Axtarışa uyğun inteqrasiya tapılmadı.</Empty>}
    <Dialog.Root open={!!selected} onOpenChange={event=>!event.open&&setSelected(null)} closeOnInteractOutside={false} size={{mdDown:"full",md:"lg"}} placement="center"><Portal><Dialog.Backdrop/><Dialog.Positioner><Dialog.Content borderRadius={{mdDown:0,md:"2xl"}}>{selected&&<><Dialog.Header borderBottomWidth="1px"><Flex align="center" gap="3"><Flex w="11" h="11" borderRadius="xl" bg={selected.color} color="white" align="center" justify="center" fontWeight="900">{selected.mark}</Flex><Box><Dialog.Title>{selected.name}</Dialog.Title><Dialog.Description>{selected.category}</Dialog.Description></Box></Flex></Dialog.Header><Dialog.Body py="5"><Text color="gray.700">{selected.description}</Text><Stack mt="5" gap="2">{selected.features.map(feature=><Flex key={feature} gap="2" align="center"><Box w="2" h="2" borderRadius="full" bg="brand.500"/><Text>{feature}</Text></Flex>)}</Stack>{selected.id==="beinsystems"&&<Box mt="5" p="3" bg="brand.50" color="brand.700" borderRadius="lg" fontSize="sm">API uyğunluğu təsdiqlənib. Təhlükəsiz qoşulma üçün integration token və production API host tələb olunur.</Box>}</Dialog.Body><Dialog.Footer borderTopWidth="1px"><Button variant="outline" onClick={()=>setSelected(null)}>Bağla</Button><Button colorPalette="green" onClick={()=>openConfiguration(selected)}>Ayarları aç</Button></Dialog.Footer></>}</Dialog.Content></Dialog.Positioner></Portal></Dialog.Root>
  </Stack>
}

function PermissionCheckbox({ value, label, checked, onChange, disabled }) {
  return <Checkbox.Root checked={checked} disabled={disabled} onCheckedChange={event => onChange(value, event.checked === true)} colorPalette="green"><Checkbox.HiddenInput/><Checkbox.Control><Checkbox.Indicator/></Checkbox.Control><Checkbox.Label fontSize="sm">{label}</Checkbox.Label></Checkbox.Root>
}

function MemberDialog({ member, defaults={}, open, onClose, onSaved }) {
  const [draft, setDraft] = useState(null); const [saving,setSaving]=useState(false); const [error,setError]=useState("")
  useEffect(() => { if(open && member)setDraft({ ...member, permissions:[...(member.permissions || [])] }) }, [open, member])
  if (!draft) return null
  const roleKeys = Object.keys(defaults).filter(role => role !== "owner" || draft.role === "owner")
  const changeRole = role => setDraft({ ...draft, role, permissions:[...(defaults[role] || [])] })
  const toggle = (key, checked) => setDraft({ ...draft, permissions:checked ? [...new Set([...draft.permissions,key])] : draft.permissions.filter(item => item !== key) })
  const save = async () => { setSaving(true);setError("");try{await api("/api/platform/members",{method:"POST",body:JSON.stringify({action:"upsert",telegram_id:draft.telegram_id,display_name:draft.display_name,role:draft.role,permissions:draft.permissions})});await onSaved();onClose()}catch(e){setError(e.message)}finally{setSaving(false)} }
  return <Dialog.Root open={open} onOpenChange={event => !event.open && onClose()} closeOnInteractOutside={false} size={{ mdDown:"full", md:"lg" }} placement="center"><Portal><Dialog.Backdrop/><Dialog.Positioner><Dialog.Content borderRadius={{ mdDown:0, md:"2xl" }}>
    <Dialog.Header borderBottomWidth="1px"><Dialog.Title>Əməkdaş hüquqları</Dialog.Title></Dialog.Header><Dialog.Body py="5"><Stack gap="5">
      <Box><Text fontSize="sm" fontWeight="700" mb="2">Ad və soyad</Text><Input value={draft.display_name || ""} onChange={e => setDraft({...draft,display_name:e.target.value})}/></Box>
      <Box><Text fontSize="sm" fontWeight="700" mb="2">Əməkdaş növü</Text><Box as="select" w="full" borderWidth="1px" borderColor="gray.200" borderRadius="lg" p="2.5" value={draft.role} disabled={draft.role === "owner"} onChange={e => changeRole(e.target.value)}>{roleKeys.map(role => <option key={role} value={role}>{roles[role] || role}</option>)}</Box><Text fontSize="xs" color="gray.500" mt="2">Növ standart hüquqları seçir. Aşağıda istisnaları ayrıca dəyişə bilərsiniz.</Text></Box>
      <Box><Text fontSize="sm" fontWeight="700" mb="1">Bölmə hüquqları</Text><Text fontSize="xs" color="gray.500" mb="3">İşarələnmiş bölmələrdə əməkdaş məlumatları görə və ona verilmiş əməliyyatları yerinə yetirə bilər.</Text><Grid templateColumns={{base:"1fr",md:"1fr 1fr"}} gap="3">{Object.entries(permissionLabels).map(([key,label]) => <PermissionCheckbox key={key} value={key} label={label} checked={draft.permissions.includes(key)} disabled={draft.role === "owner"} onChange={toggle}/>)}</Grid></Box>
      {error && <Box bg="red.50" color="red.700" borderRadius="lg" p="3">{error}</Box>}
    </Stack></Dialog.Body><Dialog.Footer borderTopWidth="1px"><Button variant="outline" onClick={onClose}>Bağla</Button><Button colorPalette="green" onClick={save} loading={saving}>Hüquqları saxla</Button></Dialog.Footer>
  </Dialog.Content></Dialog.Positioner></Portal></Dialog.Root>
}

function SettingsHub() {
  const [data,setData]=useState(null), [members,setMembers]=useState([]), [requests,setRequests]=useState([]), [selected,setSelected]=useState(null), [loading,setLoading]=useState(true), [error,setError]=useState("")
  const [invite,setInvite]=useState({name:"",role:"worker"}), [inviteUrl,setInviteUrl]=useState("")
  const load = async () => { setLoading(true);setError("");try{const me=await api("/api/platform/me");setData(me);if(me.member.role === "owner"){setMembers(me.members || []);const access=await api("/api/platform/access-requests");setRequests((access.requests || []).filter(row => row.status === "pending"))}}catch(e){setError(e.message)}finally{setLoading(false)} }
  useEffect(() => { load() }, [])
  if (loading) return <Flex py="14" justify="center"><Spinner color="brand.500"/></Flex>
  if (error) return <Box p="4" bg="red.50" color="red.700" borderRadius="xl">{error}</Box>
  const owner=data.member.role === "owner"
  const decide = async (row, action) => { await api("/api/platform/access-requests",{method:"POST",body:JSON.stringify({request_id:row.id,action})});load() }
  const createInvite = async () => { if(!invite.name.trim())return;const result=await api("/api/platform/invites",{method:"POST",body:JSON.stringify({display_name:invite.name.trim(),role:invite.role,permissions:data.role_permissions?.[invite.role] || []})});setInviteUrl(result.invite.url) }
  const deactivate = async member => { if(!confirm(`${member.display_name} üçün girişi bağlamaq istəyirsiniz?`))return;await api("/api/platform/members",{method:"POST",body:JSON.stringify({action:"deactivate",telegram_id:member.telegram_id})});load() }
  return <Stack gap="6" mt="5">
    <Grid templateColumns={{base:"1fr",lg:"1.1fr .9fr"}} gap="5">
      {owner ? <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Card.Title>Yeni əməkdaş</Card.Title><Card.Description>Telegram ID lazım deyil — şəxsi dəvət linki yaradın</Card.Description></Card.Header><Card.Body><Stack gap="3"><Input placeholder="Əməkdaşın adı" value={invite.name} onChange={e=>setInvite({...invite,name:e.target.value})}/><Box as="select" borderWidth="1px" borderColor="gray.200" borderRadius="lg" p="2.5" value={invite.role} onChange={e=>setInvite({...invite,role:e.target.value})}>{Object.keys(data.role_permissions || {}).filter(role=>role!=="owner").map(role=><option key={role} value={role}>{roles[role] || role}</option>)}</Box>{inviteUrl && <Box p="3" bg="brand.50" color="brand.700" borderRadius="lg" fontSize="sm" wordBreak="break-all">{inviteUrl}</Box>}</Stack></Card.Body><Card.Footer><Button colorPalette="green" onClick={createInvite}>Dəvət linki yarat</Button>{inviteUrl && <Button variant="outline" onClick={()=>navigator.clipboard.writeText(inviteUrl)}>Kopyala</Button>}</Card.Footer></Card.Root> : <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Card.Title>Sizin girişiniz</Card.Title><Card.Description>Bu şirkətdə sizə verilmiş rol və imkanlar</Card.Description></Card.Header><Card.Body><Text fontWeight="700">{roles[data.member.role] || data.member.role}</Text><Text mt="2" fontSize="sm" color="gray.600">{(data.member.permissions || []).map(key=>permissionLabels[key] || key).join(", ") || "Əlavə bölmə hüququ verilməyib"}</Text></Card.Body></Card.Root>}
      <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Card.Title>İnteqrasiya marketi</Card.Title><Card.Description>Kommo, Linear, OpenAI, Telegram və gələcək bağlantılar</Card.Description></Card.Header><Card.Body><Text color="gray.600" fontSize="sm">Bağlantılar artıq ayrıca market bölməsindədir. Orada statusu görün, xidməti açın və uyğun ayarlara keçin.</Text></Card.Body><Card.Footer><Button colorPalette="green" onClick={()=>document.querySelector('.nav [data-view="integrations"]')?.click()}>Marketi aç</Button></Card.Footer></Card.Root>
    </Grid>
    {owner && <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Flex justify="space-between" align="center"><Box><Card.Title>Əməkdaşlar və hüquqlar</Card.Title><Card.Description>Rol, bölmə girişi və hesab vəziyyəti bir yerdə</Card.Description></Box><Badge borderRadius="full" px="3">{members.filter(m=>m.active).length} aktiv</Badge></Flex></Card.Header><Card.Body p="0"><Table.ScrollArea><Table.Root minW="680px"><Table.Header bg="gray.50"><Table.Row><Table.ColumnHeader>Əməkdaş</Table.ColumnHeader><Table.ColumnHeader>Növ</Table.ColumnHeader><Table.ColumnHeader>Hüquqlar</Table.ColumnHeader><Table.ColumnHeader>Vəziyyət</Table.ColumnHeader><Table.ColumnHeader textAlign="end">Əməliyyatlar</Table.ColumnHeader></Table.Row></Table.Header><Table.Body>{members.map(member=><Table.Row key={member.telegram_id}><Table.Cell><Text fontWeight="750">{member.display_name || "Adsız"}</Text><Text fontSize="xs" color="gray.500">Telegram qoşulub</Text></Table.Cell><Table.Cell>{roles[member.role] || member.role}</Table.Cell><Table.Cell><Text maxW="280px" fontSize="sm" color="gray.600" lineClamp="2">{(member.permissions || []).map(key=>permissionLabels[key] || key).join(", ") || "Hüquq yoxdur"}</Text></Table.Cell><Table.Cell><StatusBadge tone={member.active ? "green" : "gray"}>{member.active ? "Aktiv" : "Deaktiv"}</StatusBadge></Table.Cell><Table.Cell><Flex justify="flex-end" gap="2"><Button size="xs" variant="outline" onClick={()=>setSelected(member)}>Hüquqları aç</Button>{member.role!=="owner"&&member.active&&<Button size="xs" colorPalette="red" variant="ghost" onClick={()=>deactivate(member)}>Deaktiv et</Button>}</Flex></Table.Cell></Table.Row>)}</Table.Body></Table.Root></Table.ScrollArea></Card.Body></Card.Root>}
    {owner && <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Card.Title>Giriş sorğuları</Card.Title><Card.Description>Telegram ilə daxil olan əməkdaşları təsdiqləyin</Card.Description></Card.Header><Card.Body>{requests.length ? <Stack gap="3">{requests.map(row=><Flex key={row.id} p="3" borderWidth="1px" borderRadius="xl" align="center" justify="space-between" gap="3"><Box><Text fontWeight="750">{row.display_name || row.card_name}</Text><Text fontSize="sm" color="gray.500">{roles[row.role] || row.role}</Text></Box><Flex gap="2"><Button size="sm" colorPalette="green" onClick={()=>decide(row,"approve")}>Təsdiqlə</Button><Button size="sm" colorPalette="red" variant="outline" onClick={()=>decide(row,"reject")}>Rədd et</Button></Flex></Flex>)}</Stack> : <Text color="gray.500">Yeni giriş sorğusu yoxdur.</Text>}</Card.Body></Card.Root>}
    <MemberDialog member={selected} defaults={data.role_permissions || {}} open={!!selected} onClose={()=>setSelected(null)} onSaved={load}/>
  </Stack>
}

window.tenantChakraCRM = {
  renderTasks: rows => mount("tasksList", <TasksTable rows={rows}/>),
  renderDeals: (target, rows, customer, onOpen) => mount(target, <DealsView rows={rows} customer={customer} onOpen={onOpen}/>),
}
mount("chakra-settings-root", <SettingsHub/>)
mount("chakra-integrations-root", <IntegrationMarketplace/>)
document.documentElement.dataset.chakraUi = "ready"
document.dispatchEvent(new CustomEvent("tenant-chakra-ready"))
