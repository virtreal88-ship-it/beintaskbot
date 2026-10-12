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
  const kommo=(data.integrations || []).find(row => row.provider === "kommo"), owner=data.member.role === "owner"
  const decide = async (row, action) => { await api("/api/platform/access-requests",{method:"POST",body:JSON.stringify({request_id:row.id,action})});load() }
  const createInvite = async () => { if(!invite.name.trim())return;const result=await api("/api/platform/invites",{method:"POST",body:JSON.stringify({display_name:invite.name.trim(),role:invite.role,permissions:data.role_permissions?.[invite.role] || []})});setInviteUrl(result.invite.url) }
  const deactivate = async member => { if(!confirm(`${member.display_name} üçün girişi bağlamaq istəyirsiniz?`))return;await api("/api/platform/members",{method:"POST",body:JSON.stringify({action:"deactivate",telegram_id:member.telegram_id})});load() }
  return <Stack gap="6" mt="5">
    <Grid templateColumns={{base:"1fr",lg:"1.1fr .9fr"}} gap="5">
      <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Flex justify="space-between" align="start"><Box><Card.Title>İnteqrasiyalar</Card.Title><Card.Description>CRM və mesaj kanallarının vəziyyəti</Card.Description></Box><StatusBadge tone={kommo?.status === "connected" ? "green" : "orange"}>{kommo?.status === "connected" ? "Qoşulub" : "Qoşulmayıb"}</StatusBadge></Flex></Card.Header><Card.Body><Text fontWeight="700">Kommo</Text><Text color="gray.600" fontSize="sm">{kommo?.account_domain || "Hesab hələ qoşulmayıb"}</Text></Card.Body>{owner && <Card.Footer gap="2"><Button asChild colorPalette="green"><a href="/setup">Bağlantını idarə et</a></Button><Button variant="outline" onClick={async()=>{try{await api("/api/platform/integrations/kommo/test");alert("Kommo bağlantısı işləyir.")}catch(e){alert(e.message)}}}>Yoxla</Button></Card.Footer>}</Card.Root>
      {owner ? <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Card.Title>Sürətli başlanğıc</Card.Title><Card.Description>Yeni əməkdaş üçün Telegram ID lazım deyil</Card.Description></Card.Header><Card.Body><Stack gap="3"><Input placeholder="Əməkdaşın adı" value={invite.name} onChange={e=>setInvite({...invite,name:e.target.value})}/><Box as="select" borderWidth="1px" borderColor="gray.200" borderRadius="lg" p="2.5" value={invite.role} onChange={e=>setInvite({...invite,role:e.target.value})}>{Object.keys(data.role_permissions || {}).filter(role=>role!=="owner").map(role=><option key={role} value={role}>{roles[role] || role}</option>)}</Box>{inviteUrl && <Box p="3" bg="brand.50" color="brand.700" borderRadius="lg" fontSize="sm" wordBreak="break-all">{inviteUrl}</Box>}</Stack></Card.Body><Card.Footer><Button colorPalette="green" onClick={createInvite}>Dəvət linki yarat</Button>{inviteUrl && <Button variant="outline" onClick={()=>navigator.clipboard.writeText(inviteUrl)}>Kopyala</Button>}</Card.Footer></Card.Root> : <Card.Root borderRadius="2xl" borderColor="gray.200" shadow="sm"><Card.Header><Card.Title>Sizin girişiniz</Card.Title><Card.Description>Bu şirkətdə sizə verilmiş rol və imkanlar</Card.Description></Card.Header><Card.Body><Text fontWeight="700">{roles[data.member.role] || data.member.role}</Text><Text mt="2" fontSize="sm" color="gray.600">{(data.member.permissions || []).map(key=>permissionLabels[key] || key).join(", ") || "Əlavə bölmə hüququ verilməyib"}</Text></Card.Body></Card.Root>}
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
document.documentElement.dataset.chakraUi = "ready"
document.dispatchEvent(new CustomEvent("tenant-chakra-ready"))
