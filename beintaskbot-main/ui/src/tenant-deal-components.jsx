// Read-only deal and task presenters for the tenant workspace.
import { useMemo, useState } from "react"
import { Badge, Box, Button, Card, Flex, Heading, Stack, Table, Text } from "@chakra-ui/react"
import { Empty, StatusBadge, formatDate } from "./tenant-ui-shared.jsx"

export function TasksTable({ rows=[] }) {
  if (!rows.length) return <Empty>Açıq tapşırıq yoxdur.</Empty>
  return <Card.Root borderColor="app.line" borderRadius="xl" overflow="hidden" shadow="xs"><Table.ScrollArea><Table.Root size="sm" variant="line" minW="720px">
    <Table.Header bg="app.raised"><Table.Row><Table.ColumnHeader>Tapşırıq</Table.ColumnHeader><Table.ColumnHeader>Sövdələşmə</Table.ColumnHeader><Table.ColumnHeader>Vəziyyət</Table.ColumnHeader><Table.ColumnHeader>Son tarix</Table.ColumnHeader><Table.ColumnHeader textAlign="end">Əməliyyat</Table.ColumnHeader></Table.Row></Table.Header>
    <Table.Body>{rows.map(task => <Table.Row key={task.kommo_task_id} _hover={{ bg:"app.raised" }}>
      <Table.Cell maxW="440px"><Text fontWeight="700" color="app.ink" lineClamp="2">{String(task.text || "Tapşırıq").replace(/\n?\[CRM:[a-f0-9]{32}\]/g, "")}</Text><Text fontSize="xs" color="app.muted">#{task.kommo_task_id}</Text></Table.Cell>
      <Table.Cell>{task.kommo_lead_id ? `#${task.kommo_lead_id}` : "Sövdələşməsiz"}</Table.Cell>
      <Table.Cell><StatusBadge tone={task.completed ? "gray" : "green"}>{task.completed ? "Tamamlandı" : "Açıq"}</StatusBadge></Table.Cell>
      <Table.Cell whiteSpace="nowrap">{formatDate(task.due_at)}</Table.Cell>
      <Table.Cell textAlign="end">{!task.completed && <Button size="xs" colorPalette="brand" variant="outline" data-task-complete={task.kommo_task_id}>Tamamla</Button>}</Table.Cell>
    </Table.Row>)}</Table.Body>
  </Table.Root></Table.ScrollArea></Card.Root>
}

export function DealTable({ rows=[], customer=false, onOpen }) {
  if (!rows.length) return <Empty>Bu filtr üzrə məlumat tapılmadı.</Empty>
  return <Card.Root borderColor="app.line" borderRadius="xl" overflow="hidden" shadow="xs"><Table.ScrollArea><Table.Root size="sm" minW="760px">
    <Table.Header bg="app.raised"><Table.Row><Table.ColumnHeader>{customer ? "Müştəri" : "Sövdələşmə"}</Table.ColumnHeader><Table.ColumnHeader>Mərhələ</Table.ColumnHeader><Table.ColumnHeader>Son mesaj</Table.ColumnHeader><Table.ColumnHeader>Yenilənib</Table.ColumnHeader></Table.Row></Table.Header>
    <Table.Body>{rows.map(deal => <Table.Row key={deal.kommo_lead_id} cursor="pointer" _hover={{ bg:"app.raised" }} onClick={() => onOpen?.(deal.kommo_lead_id)}>
      <Table.Cell><Text fontWeight="600" color="app.ink">{customer ? (deal.contact_name || deal.name) : deal.name}</Text><Text fontSize="xs" color="app.muted">{deal.phone || deal.contact_name || "Məlumat yoxdur"}</Text></Table.Cell>
      <Table.Cell><StatusBadge>{deal.stage_name || "Mərhələ"}</StatusBadge></Table.Cell>
      <Table.Cell maxW="330px"><Text truncate color="app.muted">{deal.last_message || "Son mesaj yoxdur"}</Text></Table.Cell>
      <Table.Cell whiteSpace="nowrap">{formatDate(deal.source_updated_at || deal.synced_at)}</Table.Cell>
    </Table.Row>)}</Table.Body>
  </Table.Root></Table.ScrollArea></Card.Root>
}

export function DealKanban({ rows=[], onOpen }) {
  const groups = useMemo(() => {
    const result = new Map()
    rows.forEach(row => { const key=row.stage_name || "Mərhələ"; if(!result.has(key))result.set(key,[]); result.get(key).push(row) })
    return [...result.entries()]
  }, [rows])
  if (!groups.length) return <Empty>Kanbanda sövdələşmə yoxdur.</Empty>
  return <Flex gap="4" align="flex-start" overflowX="auto" pb="3">{groups.map(([stage, items]) => <Box key={stage} minW="300px" w="300px" bg="app.raised" borderRadius="xl" p="3">
    <Flex align="center" justify="space-between" mb="3"><Heading size="sm">{stage}</Heading><Badge borderRadius="full">{items.length}</Badge></Flex>
    <Stack gap="3">{items.map(deal => <Card.Root key={deal.kommo_lead_id} borderWidth="1px" borderColor="app.line" shadow="xs" cursor="pointer" _hover={{ shadow:"md", borderColor:"brand.500" }} onClick={() => onOpen?.(deal.kommo_lead_id)}><Card.Body p="4"><Text fontWeight="600">{deal.contact_name || deal.name}</Text><Text fontSize="sm" color="app.muted">{deal.phone || "Telefon yoxdur"}</Text><Text mt="2" fontSize="xs" color="app.muted" lineClamp="2">{deal.last_message || deal.name}</Text><Flex mt="3" justify="space-between" fontSize="xs" color="app.muted"><span>#{deal.kommo_lead_id}</span><span>{formatDate(deal.source_updated_at || deal.synced_at)}</span></Flex></Card.Body></Card.Root>)}</Stack>
  </Box>)}</Flex>
}

export function DealsView({ rows=[], customer=false, onOpen }) {
  const [mode, setMode] = useState(customer ? "table" : (localStorage.getItem("tenant-deal-view") || "kanban"))
  if (customer) return <DealTable rows={rows} customer onOpen={onOpen} />
  return <Stack gap="3"><Flex justify="flex-end" gap="2"><Button size="sm" variant={mode === "table" ? "solid" : "outline"} colorPalette="brand" onClick={() => {setMode("table");localStorage.setItem("tenant-deal-view","table")}}>Cədvəl</Button><Button size="sm" variant={mode === "kanban" ? "solid" : "outline"} colorPalette="brand" onClick={() => {setMode("kanban");localStorage.setItem("tenant-deal-view","kanban")}}>Kanban</Button></Flex>{mode === "kanban" ? <DealKanban rows={rows} onOpen={onOpen} /> : <DealTable rows={rows} onOpen={onOpen} />}</Stack>
}
