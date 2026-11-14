import { moveKommoDeal, canIdentifyKommoDeal } from "./kommo-deal-move.js";
import { NativeDealBoard, dealProfileScope } from "./native-deal-board.jsx";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { createPortal } from "react-dom";
import "./app-shell.js";
import {taskColumns, taskBoardLabels} from "./task-columns.js";
import {dealColumns} from "./deal-columns.js";
import {DealStageBadge, DealStageSelect} from "./deal-stage-badge.jsx";
import { AIDashboard } from "./ai-dashboard.jsx";
import {
  Badge,
  Box,
  Button,
  Card,
  ChakraProvider,
  Checkbox,
  Dialog,
  Flex,
  Grid,
  Heading,
  Input,
  Portal,
  Spinner,
  Stack,
  Table,
  Text,
  createSystem,
  defaultConfig,
  defineConfig,
} from "@chakra-ui/react";

const brandConfig = defineConfig({
  theme: {
    tokens: {
      colors: {
        brand: {
          50: { value: "var(--app-accent-soft)" },
          100: { value: "var(--app-accent-soft)" },
          500: { value: "#0284c7" },
          600: { value: "#0369a1" },
          700: { value: "var(--app-accent)" },
          900: { value: "var(--app-ink)" },
        },
        app: {
          surface: { value: "var(--app-surface)" },
          raised: { value: "var(--app-raised)" },
          ink: { value: "var(--app-ink)" },
          muted: { value: "var(--app-muted)" },
          line: { value: "var(--app-line)" },
          accent: { value: "var(--app-accent)" },
        },
      },
      fonts: { body: { value: "Inter, ui-sans-serif, system-ui, sans-serif" } },
      radii: { crm: { value: "12px" } },
      shadows: { crm: { value: "0 1px 2px rgba(0,0,0,.05)" } },
    },
    semanticTokens: {
      colors: {
        bg: {
          DEFAULT: { value: "{colors.app.surface}" },
          subtle: { value: "{colors.app.raised}" },
          muted: { value: "{colors.app.raised}" },
          panel: { value: "{colors.app.surface}" },
        },
        fg: {
          DEFAULT: { value: "{colors.app.ink}" },
          muted: { value: "{colors.app.muted}" },
          subtle: { value: "{colors.app.muted}" },
        },
        border: {
          DEFAULT: { value: "{colors.app.line}" },
          muted: { value: "{colors.app.line}" },
          subtle: { value: "{colors.app.line}" },
        },
        brand: {
          solid: { value: "#0284c7" },
          contrast: { value: "#fff" },
          fg: { value: "{colors.app.accent}" },
          subtle: { value: "{colors.brand.50}" },
          muted: { value: "{colors.brand.100}" },
          emphasized: { value: "{colors.app.accent}" },
          focusRing: { value: "{colors.app.accent}" },
        },
        gray: {
          fg: { value: "{colors.app.ink}" },
          solid: { value: "{colors.app.ink}" },
          contrast: { value: "{colors.app.surface}" },
          subtle: { value: "{colors.app.raised}" },
          muted: { value: "{colors.app.raised}" },
          emphasized: { value: "{colors.app.line}" },
        },
      },
    },
  },
});
const system = createSystem(defaultConfig, brandConfig);
const roots = new Map();
const roles = {
  owner: "Şirkət sahibi",
  admin: "Administrator",
  manager: "Menecer",
  operator: "Operator",
  worker: "Operator",
  master: "Usta",
};
const permissionLabelsBase = {
  night_report: "Gecə növbəsi hesabatı",
  dialogs: "Диалоги",
  support: "Обращения",
  deals: "Sövdələşmələr",
  tasks: "Tapşırıqlar",
  customers: "Müştərilər",
  hot_orders: "İsti sifarişlər",
  finance: "Maliyyə",
  linear: "Linear",
  settings: "Tənzimləmələr",
  employees: "Əməkdaşlar",
  integrations: "İnteqrasiyalar",
  ai_agents: "AI agentləri",
};
const permissionLabels = {
  ...permissionLabelsBase,
  ...Object.fromEntries(
    Object.entries(permissionLabelsBase).flatMap(([key, label]) => [
      [`${key}:view`, `Baxış · ${label}`],
      [`${key}:create`, `Yaratmaq · ${label}`],
      [`${key}:update`, `Dəyişmək · ${label}`],
      [`${key}:delete`, `Silmək · ${label}`],
      [`${key}:assign`, `Təyin etmək · ${label}`],
      [`${key}:manage`, `İdarə etmək · ${label}`],
    ]),
  ),
};
const integrationCatalog = [
  {
    id: "kommo",
    name: "Kommo",
    mark: "K",
    color: "#635BFF",
    action: "setup",
    features: ["Sövdələşmə və mərhələlər", "Tapşırıqlar", "Mesaj kanalları"],
    copy: {
      ru: {
        category: "CRM",
        description: "Синхронизация сделок, клиентов, задач и переписок.",
      },
      az: {
        category: "CRM",
        description:
          "Sövdələşmə, müştəri, tapşırıq və yazışmaların sinxronizasiyası.",
      },
      en: {
        category: "CRM",
        description: "Sync deals, customers, tasks and conversations.",
      },
    },
  },
  {
    id: "linear",
    name: "Linear",
    mark: "L",
    color: "#5E6AD2",
    section: "integrations",
    copy: {
      ru: {
        category: "Управление работой",
        description: "Задачи продукта, согласования и релизы.",
      },
      az: {
        category: "İşin idarə edilməsi",
        description: "Məhsul tapşırıqları, təsdiqlər və buraxılışlar.",
      },
      en: {
        category: "Work management",
        description: "Product tasks, approvals and releases.",
      },
    },
  },
  {
    id: "openai",
    name: "OpenAI",
    mark: "AI",
    color: "#111827",
    section: "ai",
    copy: {
      ru: {
        category: "Искусственный интеллект",
        description: "AI-функции компании с сохранёнными настройками.",
      },
      az: {
        category: "Süni intellekt",
        description: "Şirkətin saxlanmış parametrlərlə AI funksiyaları.",
      },
      en: {
        category: "Artificial intelligence",
        description: "Company AI features using saved settings.",
      },
    },
  },
  {
    id: "telegram",
    name: "Telegram",
    mark: "T",
    color: "#229ED9",
    section: "notifications",
    copy: {
      ru: {
        category: "Связь",
        description: "Вход сотрудников, уведомления и публикации.",
      },
      az: {
        category: "Rabitə",
        description: "Əməkdaş girişi, bildirişlər və paylaşımlar.",
      },
      en: {
        category: "Communication",
        description: "Employee access, notifications and announcements.",
      },
    },
  },
  {
    id: "connect",
    name: "WhatsApp Connect",
    mark: "WA",
    color: "#25D366",
    section: "connect",
    copy: {
      ru: {
        category: "Каналы сообщений",
        description: "Каналы WhatsApp, подключение и диагностика webhook.",
      },
      az: {
        category: "Mesaj kanalları",
        description: "WhatsApp kanalları, qoşulma və webhook diaqnostikası.",
      },
      en: {
        category: "Messaging channels",
        description: "WhatsApp channels, connection and webhook diagnostics.",
      },
    },
  },
  {
    id: "beinsystems",
    name: "BeinSystems ERP",
    mark: "B",
    color: "#0A926E",
    features: [
      "Sövdələşmə və müştərilər",
      "Tapşırıqlar",
      "Vərəq və mərhələlər",
    ],
    copy: {
      ru: {
        category: "CRM / ERP",
        description: "Сделки, клиенты, задачи и этапы BeinSystems.",
      },
      az: {
        category: "CRM / ERP",
        description:
          "BeinSystems sövdələşmələri, müştərilər, tapşırıqlar və mərhələlər.",
      },
      en: {
        category: "CRM / ERP",
        description: "BeinSystems deals, customers, tasks and stages.",
      },
    },
  },
  {
    id: "beinsystems_operator",
    name: "BeinSystems Operator",
    mark: "O",
    color: "#155E75",
    privateProvider: true,
    section: "integrations",
    copy: {
      ru: {
        category: "Поддержка BeinSystems",
        description: "Приватный доступ к Operator для рабочего пространства BeinSystems.",
      },
      az: {
        category: "BeinSystems dəstəyi",
        description: "BeinSystems iş sahəsi üçün Operator-a məxfi giriş.",
      },
      en: {
        category: "BeinSystems support",
        description: "Private Operator access for the BeinSystems workspace.",
      },
    },
  },
];

function mount(id, node) {
  const host = document.getElementById(id);
  if (!host) return false;
  host.classList.remove("table");
  if (!roots.has(id)) roots.set(id, createRoot(host));
  roots.get(id).render(<ChakraProvider value={system}>{node}</ChakraProvider>);
  return true;
}
const formatDate = (value) => {
  if (!value) return "Tarix yoxdur";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Tarix yoxdur"
    : date
        .toLocaleString("en-GB", {
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
        .replace(",", "");
};
const api = async (url, options = {}) => {
  const response = await fetch(url, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const result = await response
    .json()
    .catch(() => ({ success: false, error: "Server cavabı oxunmadı." }));
  if (!response.ok || !result.success)
    throw new Error(result.error || "Sorğu alınmadı.");
  return result;
};

function Empty({ children }) {
  return (
    <Box
      py="14"
      px="5"
      textAlign="center"
      color="app.muted"
      bg="app.surface"
      borderWidth="1px"
      borderColor="app.line"
      borderRadius="xl"
    >
      {children}
    </Box>
  );
}
function StatusBadge({ children, tone = "green" }) {
  return (
    <Badge
      colorPalette={tone}
      variant="subtle"
      borderRadius="full"
      px="2.5"
      py="1"
      fontWeight="700"
    >
      {children}
    </Badge>
  );
}
function IntegrationLogo({ provider, size = "md" }) {
  const item = integrationCatalog.find((row) => row.id === provider) || {};
  const boxSize = size === "lg" ? "64px" : size === "sm" ? "40px" : "52px";
  const iconSize = size === "lg" ? 30 : size === "sm" ? 20 : 25;
  const common = {
    width: iconSize,
    height: iconSize,
    viewBox: "0 0 24 24",
    fill: "none",
    xmlns: "http://www.w3.org/2000/svg",
  };
  let logo = null;
  if (provider === "telegram")
    logo = (
      <svg {...common}>
        <path
          d="M21.4 3.4 18.2 19c-.24 1.1-.88 1.37-1.78.85l-4.9-3.61-2.36 2.27c-.26.26-.48.48-.98.48l.35-5 9.1-8.23c.4-.35-.08-.55-.61-.2L5.78 12.64.94 11.13c-1.05-.33-1.07-1.05.22-1.56L20.08 2.3c.88-.32 1.65.2 1.32 1.1Z"
          fill="currentColor"
        />
      </svg>
    );
  else if (provider === "linear")
    logo = (
      <svg {...common}>
        <path
          d="M4.3 4.3a10.9 10.9 0 0 0-1.9 2.75l14.55 14.54a10.9 10.9 0 0 0 2.75-1.89L4.3 4.3Zm-2.65 4.8a11 11 0 0 0-.5 3.33l10.42 10.42a11 11 0 0 0 3.33-.5L1.65 9.1Zm-.23 6.38a11.02 11.02 0 0 0 7.1 7.1l-7.1-7.1ZM6.35 2.65l15 15a11 11 0 0 0-15-15Z"
          fill="currentColor"
        />
      </svg>
    );
  else if (provider === "openai")
    logo = (
      <svg {...common} viewBox="0 0 32 32">
        <path
          d="M15.9 3.8a7 7 0 0 1 11 5.8 7 7 0 0 1 1 12.3 7 7 0 0 1-11 5.8 7 7 0 0 1-11-5.8 7 7 0 0 1-1-12.3 7 7 0 0 1 11-5.8Zm0 4-5.8 3.4v6.7l5.8 3.4 5.8-3.4v-6.7l-5.8-3.4Zm0 3.2 3 1.8v3.5l-3 1.8-3-1.8v-3.5l3-1.8Z"
          fill="currentColor"
        />
      </svg>
    );
  else if (provider === "connect")
    logo = (
      <svg {...common}>
        <path
          d="M20.2 11.8a8.2 8.2 0 0 1-12.1 7.2L3 20l1.1-4.8A8.2 8.2 0 1 1 20.2 11.8Z"
          stroke="currentColor"
          strokeWidth="1.8"
        />
        <path
          d="M8.7 8.4c.4-.6.9-.5 1.2-.1l.8 1.4c.2.3.1.6-.2.9l-.5.5c.6 1.1 1.4 1.9 2.5 2.4l.5-.6c.2-.3.6-.4.9-.2l1.4.7c.4.2.5.8.1 1.2-.5.6-1.2.9-2 .8-2.7-.5-5.6-3.3-5.9-5.8-.1-.5.3-1 .7-1.2Z"
          fill="currentColor"
        />
      </svg>
    );
  else if (provider === "kommo")
    logo = (
      <Text
        as="span"
        fontSize={size === "lg" ? "lg" : "md"}
        fontWeight="900"
        letterSpacing="-.8px"
        color="inherit"
      >
        kommo
      </Text>
    );
  else
    logo = (
      <Text
        as="span"
        fontSize={size === "lg" ? "lg" : "md"}
        fontWeight="900"
        letterSpacing="-.5px"
        color="inherit"
      >
        BEIN
      </Text>
    );
  return (
    <Flex
      data-integration-logo={provider}
      w={boxSize}
      h={boxSize}
      flex="none"
      borderRadius="14px"
      bg={`${item.color}12`}
      color={item.color}
      borderWidth="1px"
      borderColor={`${item.color}26`}
      align="center"
      justify="center"
    >
      {logo}
    </Flex>
  );
}
function TasksTable({ rows = [], settings = {} }) {
  const labels = settings.labels || {open:'Açıq',completed:'Tamamlandı',view:'Aç',complete:'Tamamla',deal:'Sövdələşmə',status:'Vəziyyət',due:'Son tarix',text:'Tapşırıq',none:'Göstərilməyib'};
  if (!rows.length) return <Empty>{settings.empty || 'Açıq tapşırıq yoxdur.'}</Empty>;
  return <Card.Root borderColor="app.line" borderRadius="xl" overflow="hidden" shadow="xs">
    <Flex direction="column" divideY="1px" borderColor="app.line">
      {rows.map(task => {
        const id = String(task.id || task.kommo_task_id), status = task.status || (task.completed ? 'completed' : 'open');
        const native = settings.source === 'native';
        const clientName = task.deal_title || task.contact_name || '';
        const clientText = [clientName, task.phone && !clientName.includes(task.phone) ? task.phone : ''].filter(Boolean).join(' · ');
        return <Flex key={id} p="4" gap="3" justify="space-between" wrap="wrap" align="start">
          <Box flex="1" minW="180px" display="flex" flexDirection="column" alignItems="flex-start">
            {settings.onOpen ? <Button variant="plain" h="auto" minH="44px" p="0" maxW="100%" whiteSpace="normal" textAlign="start" justifyContent="start" data-task-flow-open={id} onClick={() => settings.onOpen(id)}>
              <Text fontWeight="700" color="app.ink" overflowWrap="anywhere">{String(task.text || '').replace(/\n?\[CRM:[a-f0-9]{32}\]/g, '')}</Text>
            </Button> : <Text fontWeight="700" color="app.ink">{String(task.text || '').replace(/\n?\[CRM:[a-f0-9]{32}\]/g, '')}</Text>}
            {clientText && (settings.canOpenDeal?.(task) && settings.onOpenDeal ?
              <Button data-task-client="" data-task-deal-open={id} type="button" variant="plain" minH="44px" h="auto" p="0" maxW="100%" whiteSpace="normal" textAlign="start" justifyContent="start" style={{color:"var(--app-accent, #0369a1)"}} textDecoration="underline" title={labels.deal} onClick={event=>{event.stopPropagation();settings.onOpenDeal(id);}} onPointerDown={event=>event.stopPropagation()} draggable={false}>
                <Text as="span" fontSize="sm" style={{color:"inherit"}} fontWeight="500" overflowWrap="anywhere">{clientText}</Text>
              </Button> : <Text data-task-client="" fontSize="sm" color="app.ink">{clientText}</Text>)}
            {task.responsible_name && <Text fontSize="sm" color="app.muted">{task.responsible_name}</Text>}
            <Text fontSize="sm" color="app.muted">{labels.due}: {task.due_at ? formatDate(task.due_at) : labels.none}</Text>
          </Box>
          <Flex gap="2" wrap="wrap" align="center">
            <StatusBadge tone={status === 'completed' ? 'gray' : 'green'}>{labels[status]}</StatusBadge>
            {settings.moveNeedsSync?.(task) && <Text fontSize="sm" color="app.muted">{{ru:'Для переноса срока нужна свежая синхронизация Kommo.',az:'Tarixi dəyişmək üçün Kommo yenidən sinxronlaşdırılmalıdır.',en:'A fresh Kommo sync is required to move the deadline.'}[window.tenantI18n?.language] || 'A fresh Kommo sync is required.'}</Text>}
            {settings.pendingMove?.(task) && <Button minH="44px" variant="outline" onClick={()=>settings.onCheckMove?.(id)}>{labels.check || 'Check original request'}</Button>}
            {!native && status === 'open' && settings.complete !== false && Number.isSafeInteger(Number(id)) && <Button minH="44px" colorPalette="brand" variant="outline" data-task-complete={id}>{labels.complete}</Button>}
            {!native && status === 'open' && !Number.isSafeInteger(Number(id)) && <Text fontSize="sm">{labels.unsafeComplete}</Text>}
          </Flex>
        </Flex>;
      })}
    </Flex>
  </Card.Root>;
}

function BoardControlIcon({ kind }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {kind === 'settings' ? <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="var(--chakra-colors-app-surface)"/><circle cx="15" cy="17" r="3" fill="var(--chakra-colors-app-surface)"/></> : kind === 'table' ? <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/></> : <><rect x="3" y="4" width="5" height="13" rx="1"/><rect x="10" y="4" width="5" height="9" rx="1"/><rect x="17" y="4" width="4" height="16" rx="1"/></>}
  </svg>;
}

function TasksView({ rows = [], settings = {} }) {
  const [mode, setMode] = useState('kanban');
  const [now, setNow] = useState(() => new Date());
  const drag = useRef(null), moveOpener = useRef(null);
  const [moving, setMoving] = useState(null), [moveError, setMoveError] = useState('');
  const [deadline, setDeadline] = useState('');
  const [busy, setBusy] = useState(false);
  const copy = {ru:{move:'Перенести срок',date:'Новый срок',save:'Сохранить',cancel:'Отмена',error:'Не удалось изменить срок. Обновите список перед повторной попыткой.'},az:{move:'Son tarixi dəyiş',date:'Yeni son tarix',save:'Saxla',cancel:'Ləğv et',error:'Tarix dəyişmədi. Yenidən cəhddən əvvəl siyahını yeniləyin.'},en:{move:'Move due date',date:'New due date',save:'Save',cancel:'Cancel',error:'Could not change the due date. Refresh before retrying.'}}[window.tenantI18n?.language] || {move:'Move due date',date:'New due date',save:'Save',cancel:'Cancel',error:'Could not change due date.'};
  const editable = task => !busy && task.status !== 'completed' && typeof settings.onMove === 'function' && settings.canMove?.(task) === true;
  const choose = (task, bucket) => {
    if (!editable(task) || !['today','tomorrow','upcoming','later','undated'].includes(bucket) || settings.source === 'kommo' && bucket === 'undated') return;
    const d = new Date(); d.setSeconds(0, 0);
    if (bucket === 'today') {d.setHours(18, 0, 0, 0);if(d.getTime() <= Date.now())d.setTime(Date.now()+60000);}
    if (bucket === 'tomorrow') {d.setDate(d.getDate()+1);d.setHours(10,0,0,0);}
    // Range columns do not imply a unique deadline. Let the user choose it.
    if (bucket === 'upcoming') {d.setDate(d.getDate()+2);d.setHours(10,0,0,0);}
    if (bucket === 'later') {d.setDate(d.getDate()+14);d.setHours(10,0,0,0);}
    moveOpener.current = document.activeElement;
    setDeadline(bucket === 'undated' ? '' : new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16));
    setMoving({id:String(task.id || task.kommo_task_id),bucket,source:settings.source});setMoveError('');
  };
  useEffect(() => {if (moving && (moving.source !== settings.source || !rows.some(row => String(row.id || row.kommo_task_id) === moving.id && settings.canMove?.(row) === true))) {setMoving(null);setBusy(false);} }, [rows, settings.source]);
  const closeMove = () => {const id=moving?.id;setMoving(null);if(moveOpener.current?.isConnected)moveOpener.current.focus();else if(id)setTimeout(()=>document.querySelector(`[data-task-flow-open="${id}"]`)?.focus(),0);};
  const submitMove = async () => {
    if (busy || !moving) return;
    const row = rows.find(row => String(row.id || row.kommo_task_id) === moving.id);
    if (!row || !editable(row)) {setMoving(null);return;}
    const due = moving.bucket === 'undated' ? null : new Date(deadline);
    if (settings.source === 'kommo' && (!due || due.getTime() <= Date.now())) {setMoveError(copy.error);return;}
    if (due && (Number.isNaN(due.getTime()) || taskColumns([{due_at:due.toISOString()}], new Date()).find(g => g.rows.length)?.key !== moving.bucket)) {setMoveError(copy.error);return;}
    setBusy(true);setMoveError('');
    try {await settings.onMove(moving.id,due?.toISOString() || null);closeMove();}
    catch {setMoveError(copy.error);}
    finally {setBusy(false);}
  };
  useEffect(() => {
    let timer;
    const resume = () => {clearInterval(timer);const host=document.getElementById(settings.target || 'tasksList');if(!document.hidden&&host?.closest('.view')?.classList.contains('active'))timer=setInterval(()=>setNow(new Date()),60000);};
    resume();document.addEventListener('tenant-view',resume);document.addEventListener('visibilitychange',resume);
    return () => {clearInterval(timer);document.removeEventListener('tenant-view',resume);document.removeEventListener('visibilitychange',resume);};
  }, [settings.target]);
  const c = taskBoardLabels[window.tenantI18n?.language] || taskBoardLabels.az;
  const groups = taskColumns(rows, now);
  const controls = <Flex role="group" aria-label={c.view} data-task-view-controls="" gap="2" wrap="nowrap">
    {['kanban', 'table'].map(value => <Button key={value} size="sm" h="44px" w="44px" minW="44px" p="0" aria-label={c[value]} title={c[value]} aria-pressed={mode === value} variant={mode === value ? 'solid' : 'outline'} onClick={() => setMode(value)}><BoardControlIcon kind={value}/></Button>)}
  </Flex>;
  const controlsTarget = document.getElementById(settings.source === 'native' ? 'nativeTaskViewControls' : 'kommoTaskViewControls');
  return <Box data-task-view={mode} minW="0">
    {moving && <Box role="region" aria-label={copy.move} borderWidth="1px" borderColor="app.line" p="3" mb="3">
      <Text>{copy.move}: {c[moving.bucket]}</Text>
      {moving.bucket !== 'undated' && <label>{copy.date}<Input autoFocus type="datetime-local" value={deadline} disabled={busy} onChange={e=>setDeadline(e.target.value)} /></label>}
      <Flex gap="2" mt="2"><Button disabled={busy || moving.bucket !== 'undated' && !deadline} onClick={submitMove}>{copy.save}</Button><Button disabled={busy} variant="outline" onClick={closeMove}>{copy.cancel}</Button></Flex>
      {moveError && <Text role="alert" color="red.600">{moveError}</Text>}
    </Box>}
    {controlsTarget ? createPortal(controls, controlsTarget) : <Box mb="4">{controls}</Box>}
    {mode === 'table' ? <TasksTable rows={rows} settings={settings}/> : !rows.length ? <Empty>{settings.empty || c.empty}</Empty> :
      <Flex data-task-kanban="" data-horizontal-scroll="" gap="4" align="flex-start" overflowX="auto" pb="3" scrollSnapType="x proximity">
        {groups.map(group => <Box key={group.key} data-task-column={group.key}
          onDragOver={event=>{if(drag.current && ['today','tomorrow','upcoming','later','undated'].includes(group.key))event.preventDefault();}}
          onDrop={event=>{event.preventDefault();const task=rows.find(row=>String(row.id || row.kommo_task_id)===drag.current);drag.current=null;if(task && !group.rows.includes(task))choose(task,group.key);}} flex="1 0 280px" minW="min(280px, 82vw)" maxW="360px" scrollSnapAlign="start">
          <Box textAlign="center" py="3" mb="3" borderBottomWidth="2px" borderColor={group.key === 'overdue' ? 'red.400' : group.key === 'today' ? 'green.400' : 'app.line'}>
            <Heading size="sm">{c[group.key]}</Heading><Text fontSize="sm" color="app.muted">{c.count}: {group.rows.length}</Text>
          </Box>
          <Stack gap="2">{group.rows.length ? group.rows.map(task => <Box key={String(task.id || task.kommo_task_id)} data-task-card="" draggable={editable(task)} onDragStart={event=>{if(!editable(task)){event.preventDefault();return;}drag.current=String(task.id || task.kommo_task_id);event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',drag.current);}} onDragEnd={()=>{drag.current=null;}}><TasksTable rows={[task]} settings={settings}/>
            {editable(task) && <Button variant="outline" size="sm" minH="44px" aria-label={copy.move} style={{touchAction:'none'}} onPointerDown={event=>{if(event.pointerType==='mouse')return;drag.current=String(task.id || task.kommo_task_id);event.currentTarget.setPointerCapture(event.pointerId);}} onPointerUp={event=>{const id=drag.current;drag.current=null;if(!id||event.pointerType==='mouse')return;const column=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-task-column]');if(column&&column.dataset.taskColumn!==group.key)choose(task,column.dataset.taskColumn);}} onPointerCancel={()=>{drag.current=null;}}>↔ {copy.move}</Button>}
            {editable(task) && <label><Text fontSize="sm">{copy.move}</Text><select aria-label={copy.move} value="" style={{minHeight:44,maxWidth:'100%'}} onChange={event=>choose(task,event.target.value)}><option value="">{copy.move}</option>{['today','tomorrow','upcoming','later','undated'].filter(key=>key!==group.key && !(settings.source==='kommo' && key==='undated')).map(key=><option key={key} value={key}>{c[key]}</option>)}</select></label>}
          </Box>) : <Text textAlign="center" fontSize="sm" color="app.muted" py="4">{c.empty}</Text>}</Stack>
        </Box>)}
      </Flex>}
  </Box>;
}

function DealTable({ rows = [], customer = false, onOpen, catalog = [] }) {
  const stages = new Map(dealColumns(rows, catalog).map(stage => [stage.key, stage]));
  if (!rows.length) return <Empty>Bu filtr üzrə məlumat tapılmadı.</Empty>;
  return (
    <Card.Root
      borderColor="app.line"
      borderRadius="xl"
      overflow="hidden"
      shadow="xs"
    >
      <Table.ScrollArea data-horizontal-scroll="">
        <Table.Root size="sm" minW="760px">
          <Table.Header bg="app.raised">
            <Table.Row>
              <Table.ColumnHeader>
                {customer ? "Müştəri" : "Sövdələşmə"}
              </Table.ColumnHeader>
              <Table.ColumnHeader>Mərhələ</Table.ColumnHeader>
              <Table.ColumnHeader>Son mesaj</Table.ColumnHeader>
              <Table.ColumnHeader>Yenilənib</Table.ColumnHeader>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {rows.map((deal) => (
              <Table.Row
                key={deal.kommo_lead_id}
                cursor="pointer"
                _hover={{ bg: "app.raised" }}
                onClick={() => onOpen?.(deal.kommo_lead_id)}
              >
                <Table.Cell>
                  <Text fontWeight="600" color="app.ink">
                    {customer ? deal.contact_name || deal.name : deal.name}
                  </Text>
                  <Text fontSize="xs" color="app.muted">
                    {deal.phone || deal.contact_name || "Məlumat yoxdur"}
                  </Text>
                </Table.Cell>
                <Table.Cell>
                  <DealStageBadge name={stages.get(`${deal.pipeline_id}:${deal.status_id}`)?.name || deal.stage_name || "Mərhələ"} appearance={stages.get(`${deal.pipeline_id}:${deal.status_id}`).appearance}/>
                </Table.Cell>
                <Table.Cell maxW="330px">
                  <Text truncate color="app.muted">
                    {deal.last_message || "Son mesaj yoxdur"}
                  </Text>
                </Table.Cell>
                <Table.Cell whiteSpace="nowrap">
                  {formatDate(deal.source_updated_at || deal.synced_at)}
                </Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Root>
      </Table.ScrollArea>
    </Card.Root>
  );
}

function DealKanban({ rows = [], catalog = [], hidden = [], onOpen, moveTargets = [], moveSources = [], completionApproval = false, moveContext }) {
  const [displayRows,setDisplayRows]=useState(rows),[pending,setPending]=useState(''),[moveError,setMoveError]=useState('');
  const drag=useRef(null),busy=useRef(false),moveSnapshots=useRef(new Map()),revision=useRef(0),alive=useRef(true);
  useEffect(()=>{setDisplayRows(previous=>rows.map(row=>{const key=String(row.kommo_lead_id),snapshot=moveSnapshots.current.get(key);if(!snapshot)return row;if(snapshot.confirmed&&snapshot.sourceRevision&&row.source_revision===snapshot.sourceRevision){moveSnapshots.current.delete(key);return row;}if(snapshot.confirmed&&snapshot.baseRevision&&row.source_revision&&row.source_revision!==snapshot.baseRevision&&row.source_revision!==snapshot.sourceRevision){moveSnapshots.current.delete(key);return row;}return snapshot.row||previous.find(item=>String(item.kommo_lead_id)===key)||row;}));},[rows]);
  useEffect(()=>{alive.current=true;setDisplayRows(rows);const clear=()=>{revision.current++;drag.current=null;moveSnapshots.current.clear();busy.current=false;setPending('');setDisplayRows([]);};let scope=dealProfileScope();const changed=event=>{const next=dealProfileScope(event.detail||{});if(!event.detail?.member||next!==scope){scope=next;clear();}};const invalid=()=>clear();document.addEventListener('tenant-profile',changed);document.addEventListener('tenant-auth-invalid',invalid);return()=>{alive.current=false;clear();document.removeEventListener('tenant-profile',changed);document.removeEventListener('tenant-auth-invalid',invalid);};},[]);
  const targets=row=>moveSources.some(s=>String(s.pipeline_id)===String(row.pipeline_id)&&String(s.stage_id)===String(row.status_id))?moveTargets.filter(t=>String(t.pipeline_id)===String(row.pipeline_id)):[];
  const movable=row=>!!moveContext&&canIdentifyKommoDeal(row)&&!busy.current&&targets(row).length>0&&!!(row.raw?.updated_at||row.source_updated_at);
  const move=async(row,stage)=>{
    if(!movable(row)||String(row.status_id)===String(stage)||!targets(row).some(t=>String(t.stage_id)===String(stage)))return;
    const serial=revision.current,key=String(row.kommo_lead_id),optimistic={...row,status_id:stage};busy.current=true;moveSnapshots.current.set(key,{row:optimistic,confirmed:false,sourceRevision:''});setPending(key);setMoveError('');
    setDisplayRows(values=>values.map(item=>String(item.kommo_lead_id)===key?optimistic:item));
    try{const result=await moveKommoDeal(row,stage,moveContext);if(alive.current&&serial===revision.current){moveSnapshots.current.set(key,{row:result,confirmed:true,baseRevision:row.source_revision,sourceRevision:result.source_revision});setDisplayRows(values=>values.map(item=>String(item.kommo_lead_id)===key?result:item));}}
    catch(e){if(alive.current&&serial===revision.current){moveSnapshots.current.delete(key);setDisplayRows(values=>values.map(item=>String(item.kommo_lead_id)===key?row:item));setMoveError(e.message);}}
    finally{if(alive.current&&serial===revision.current){busy.current=false;setPending('');}}
  };
  const groups = useMemo(() => dealColumns(displayRows, catalog, hidden), [displayRows, catalog, hidden]);
  if (!groups.length) return <Empty>Kanbanda sövdələşmə yoxdur.</Empty>;
  return (
    <Box>{completionApproval&&<Text fontSize="sm" color="app.muted">{document.documentElement.lang==='ru'?'Для успешного завершения используйте «Завершить сделку»: требуется подтверждение.':document.documentElement.lang==='en'?'Use Complete deal for successful completion: approval is required.':'Uğurlu tamamlamaq üçün «Sövdələşməni tamamla» istifadə edin: təsdiq tələb olunur.'}</Text>}{moveError&&<Text role="alert">{moveError}</Text>}<Flex data-horizontal-scroll="" gap="4" align="flex-start" overflowX="auto" pb="3">
      {groups.map(({key, pipeline, name: stage, appearance, rows: items}) => (
        <Box
          key={key}
          data-kommo-stage-key={key}
          onDragOver={event=>{const row=drag.current;if(row&&targets(row).some(t=>`${t.pipeline_id}:${t.stage_id}`===key)){event.preventDefault();event.dataTransfer.dropEffect='move';}}}
          onDrop={event=>{event.preventDefault();const row=drag.current;drag.current=null;if(row&&key.startsWith(String(row.pipeline_id)+':'))move(row,key.slice(key.indexOf(':')+1));}}
          minW="300px"
          w="300px"
          bg="app.raised"
          borderRadius="xl"
          p="3"
        >
          <Flex data-deal-stage-header="" align="center" justify="space-between" mb="3" pb="3" borderBottomWidth="2px" borderColor={appearance.color} gap="2">
            <Box minW="0"><Text fontSize="xs" color="app.muted">{pipeline}</Text><Heading size="sm" overflowWrap="anywhere">{stage}</Heading></Box>
            <Badge borderRadius="full">{items.length}</Badge>
          </Flex>
          <Stack gap="3">
            {items.map((deal) => (
              <Card.Root
                key={deal.kommo_lead_id}
                draggable={movable(deal)}
                aria-busy={pending===String(deal.kommo_lead_id)}
                onDragStart={event=>{if(!movable(deal)){event.preventDefault();return;}drag.current=deal;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',String(deal.kommo_lead_id));}}
                onDragEnd={()=>{drag.current=null;}}
                borderWidth="1px"
                borderColor="app.line"
                shadow="xs"
                cursor="pointer"
                _hover={{ shadow: "md", borderColor: "brand.500" }}
                onClick={() => onOpen?.(deal.kommo_lead_id)}
              >
                <Card.Body p="4">
                  <Text fontWeight="600">{deal.contact_name || deal.name}</Text>
                  {!(moveContext&&targets(deal).length>0)&&<DealStageBadge name={stage} appearance={appearance}/>}
                  {!!moveContext&&targets(deal).length>0&&<Box onClick={event=>event.stopPropagation()}><DealStageSelect appearance={appearance} aria-label={document.documentElement.lang==='ru'?'Изменить этап':document.documentElement.lang==='en'?'Change stage':'Mərhələni dəyiş'} disabled={!!pending||!canIdentifyKommoDeal(deal)} title={!canIdentifyKommoDeal(deal)?(document.documentElement.lang==='ru'?'Сначала синхронизируйте и обновите сделки':document.documentElement.lang==='en'?'Sync and refresh deals first':'Əvvəlcə sövdələşmələri sinxronlaşdırıb yeniləyin'):undefined} value={deal.status_id} onChange={event=>move(deal,event.target.value)} >{catalog.filter(p=>String(p.pipeline_id)===String(deal.pipeline_id)).flatMap(p=>(p.stages||[]).filter(t=>String(t.stage_id)===String(deal.status_id)||targets(deal).some(a=>String(a.stage_id)===String(t.stage_id))).map(t=><option key={t.stage_id} value={t.stage_id}>{t.name}</option>))}</DealStageSelect>{!canIdentifyKommoDeal(deal)&&<Text fontSize="xs" color="app.muted">{document.documentElement.lang==='ru'?'Сначала синхронизируйте и обновите сделки':document.documentElement.lang==='en'?'Sync and refresh deals first':'Əvvəlcə sövdələşmələri sinxronlaşdırıb yeniləyin'}</Text>}<button type="button" disabled={!movable(deal)} aria-label={document.documentElement.lang==='ru'?'Перенести сделку':document.documentElement.lang==='en'?'Move deal':'Sövdələşməni daşı'} style={{touchAction:'none',minHeight:44}} onPointerDown={event=>{if(event.pointerType==='mouse'||!movable(deal))return;drag.current=deal;event.currentTarget.setPointerCapture(event.pointerId);}} onPointerCancel={()=>{drag.current=null;}} onPointerUp={event=>{const row=drag.current;drag.current=null;if(!row||event.pointerType==='mouse')return;const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-kommo-stage-key]')?.dataset.kommoStageKey;if(target&&target.startsWith(String(row.pipeline_id)+':'))move(row,target.slice(target.indexOf(':')+1));}}>↔</button></Box>}
                  <Text fontSize="sm" color="app.muted">
                    {deal.phone || "Telefon yoxdur"}
                  </Text>
                  <Text mt="2" fontSize="xs" color="app.muted" lineClamp="2">
                    {deal.last_message || deal.name}
                  </Text>
                  <Flex
                    mt="3"
                    justify="space-between"
                    fontSize="xs"
                    color="app.muted"
                  >
                    <span>#{deal.kommo_lead_id}</span>
                    <span>
                      {formatDate(deal.source_updated_at || deal.synced_at)}
                    </span>
                  </Flex>
                </Card.Body>
              </Card.Root>
            ))}
          </Stack>
        </Box>
      ))}
    </Flex></Box>
  );
}

const dealColumnCopy = {
  ru: {columns:"Столбцы", hint:"Выберите этапы. Настройка сохраняется только для вашего аккаунта.", all:"Показать все", save:"Сохранить", cancel:"Отмена", retry:"Повторить", error:"Не удалось загрузить настройки", table:"Таблица"},
  az: {columns:"Sütunlar", hint:"Mərhələləri seçin. Ayarlar yalnız sizin hesabınız üçün saxlanılır.", all:"Hamısını göstər", save:"Saxla", cancel:"Ləğv et", retry:"Yenidən yoxla", error:"Ayarlar yüklənmədi", table:"Cədvəl"},
  en: {columns:"Columns", hint:"Choose stages. Settings are saved only for your account.", all:"Show all", save:"Save", cancel:"Cancel", retry:"Retry", error:"Could not load settings", table:"Table"}
};
function DealsView({ rows = [], customer = false, onOpen }) {
  const [mode, setMode] = useState(() => {try {return localStorage.getItem("tenant-deal-view") === "table" ? "table" : "kanban";} catch {return "kanban";}});
  const changeMode = value => {setMode(value); try {localStorage.setItem("tenant-deal-view", value);} catch {}};
  const [data, setData] = useState(null), [error, setError] = useState("");
  const [open, setOpen] = useState(false), [draft, setDraft] = useState([]), [saving, setSaving] = useState(false);
  const [language, setLanguage] = useState(document.documentElement.lang || "az");
  const generation = useRef(0), alive = useRef(false), columnTrigger = useRef(null);
  const load = async () => {
    const token = ++generation.current;
    setData(null); setError(""); setOpen(false); setSaving(false);
    try { const response = await api("/api/platform/crm/deal-board-preferences");
      if (alive.current && token === generation.current) setData(response);
    } catch (e) { if (alive.current && token === generation.current) setError(e.message); }
  };
  useEffect(() => {
    if (customer) return;
    alive.current = true;
    const scope = profile => JSON.stringify([profile?.member, profile?.capabilities, profile?.role_permissions]);
    let identity = scope(window.tenantProfileSnapshot);
    const changed = event => {const next = scope(event.detail);if (next !== identity) {identity = next;setDraft([]);load();}};
    const invalid = () => {identity = '';generation.current++;setData(null);setDraft([]);setOpen(false);setSaving(false);setError('');};
    const relabel = () => setLanguage(document.documentElement.lang || "az");
    load(); document.addEventListener("tenant-profile", changed); document.addEventListener("tenant-auth-invalid", invalid); document.addEventListener("tenant-language", relabel);
    return () => {alive.current = false; generation.current++; document.removeEventListener("tenant-profile", changed); document.removeEventListener("tenant-auth-invalid", invalid); document.removeEventListener("tenant-language", relabel);};
  }, [customer]);
  const copy = dealColumnCopy[language] || dealColumnCopy.az;
  const save = async () => {
    if (saving) return;
    const token = generation.current; setSaving(true); setError("");
    try { const response = await api("/api/platform/crm/deal-board-preferences", {method:"POST", body:JSON.stringify({hidden_stage_keys:draft})});
      if (alive.current && token === generation.current) {setData(response); setOpen(false);}
    } catch(e) {if (alive.current && token === generation.current) setError(e.message);}
    finally {if (alive.current && token === generation.current) setSaving(false);}
  };
  if (customer) return <DealTable rows={rows} customer onOpen={onOpen} />;
  const catalog = data?.catalog || [];
  const hidden = data?.hidden_stage_keys || [];
  const controls = <Flex data-deal-view-controls="" justify="flex-end" gap="2" wrap="nowrap">
      <Button ref={columnTrigger} size="sm" h="44px" w="44px" minW="44px" p="0" aria-label={copy.columns} title={copy.columns} variant="outline" disabled={!data || saving} onClick={() => {setDraft([...hidden]); setOpen(true);}}><BoardControlIcon kind="settings"/></Button>
      <Button size="sm" h="44px" w="44px" minW="44px" p="0" aria-label={copy.table} title={copy.table} aria-pressed={mode === "table"} variant={mode === "table" ? "solid" : "outline"} onClick={() => changeMode("table")}><BoardControlIcon kind="table"/></Button>
      <Button size="sm" h="44px" w="44px" minW="44px" p="0" aria-label="Kanban" title="Kanban" aria-pressed={mode === "kanban"} variant={mode === "kanban" ? "solid" : "outline"} onClick={() => changeMode("kanban")}><BoardControlIcon kind="kanban"/></Button>
    </Flex>;
  const controlsTarget = document.getElementById("dealViewControls");
  return <Stack gap="3">
    {controlsTarget ? createPortal(controls, controlsTarget) : controls}
    {error && <Box role="alert"><Text color="red.500">{error}</Text>{!open && <Button onClick={load}>{copy.retry}</Button>}</Box>}
    {mode === "kanban" ? <DealKanban rows={rows} catalog={catalog} hidden={hidden} onOpen={onOpen} moveTargets={data?.move_stage_targets || []} moveSources={data?.move_stage_sources || []} completionApproval={data?.move_stage_completion_requires_approval===true} moveContext={data?.move_context}/> : <DealTable rows={rows} onOpen={onOpen} catalog={catalog}/>}
    <Dialog.Root finalFocusEl={() => columnTrigger.current} open={open} onOpenChange={e => {if (!saving) setOpen(e.open);}} placement="center" size="sm">
      <Portal><Dialog.Backdrop/><Dialog.Positioner><Dialog.Content bg="app.surface" color="app.ink">
        <Dialog.Header><Dialog.Title>{copy.columns}</Dialog.Title></Dialog.Header>
        <Dialog.Body><Text fontSize="sm" mb="3">{copy.hint}</Text><Stack maxH="55vh" overflowY="auto">
          {catalog.flatMap(p => (p.stages || []).map(stage => {const key = `${p.pipeline_id}:${stage.stage_id}`; return <PermissionCheckbox key={key} value={key} label={`${p.name} · ${stage.name}`} checked={!draft.includes(key)} disabled={saving} onChange={(key, checked) => setDraft(previous => checked ? previous.filter(x => x !== key) : [...previous, key])}/>;}))}
        </Stack>{error && <Text role="alert" color="red.500">{error}</Text>}</Dialog.Body>
        <Dialog.Footer flexWrap="wrap"><Button variant="outline" disabled={saving} onClick={() => setDraft([])}>{copy.all}</Button><Button variant="outline" disabled={saving} onClick={() => setOpen(false)}>{copy.cancel}</Button><Button colorPalette="brand" loading={saving} onClick={save}>{copy.save}</Button></Dialog.Footer>
      </Dialog.Content></Dialog.Positioner></Portal>
    </Dialog.Root>
  </Stack>;
}

function LegacyIntegrationMarketplace() {
  const [profile, setProfile] = useState(null),
    [selected, setSelected] = useState(null),
    [query, setQuery] = useState(""),
    [category, setCategory] = useState("Hamısı"),
    [details, setDetails] = useState({}),
    [error, setError] = useState("");
  const [beinForm, setBeinForm] = useState({
      base_url: "",
      branch_id: "",
      token: "",
    }),
    [busy, setBusy] = useState(""),
    [notice, setNotice] = useState("");
  const load = async () => {
    try {
      const me = await api("/api/platform/me");
      setProfile(me);
      const status = {};
      (me.integrations || []).forEach((row) => {
        const key = row.provider === "chat_ai" ? "openai" : row.provider;
        status[key] = {
          connected: ["connected", "configured"].includes(row.status),
          label: row.status,
          primary: !!row.metadata?.primary,
          base_url: row.account_domain || "",
          branch_id: row.metadata?.branch_id || "",
        };
      });
      status.telegram = {
        connected: !!me.member?.telegram_id,
        label: "connected",
      };
      if (me.member?.role === "owner") {
        const checks = await Promise.allSettled([
          api("/api/platform/linear/settings"),
          api("/api/platform/chat/ai/settings"),
          api("/api/platform/news/telegram/settings"),
          api("/api/platform/integrations/beinsystems"),
        ]);
        if (checks[0].status === "fulfilled")
          status.linear = {
            connected: checks[0].value.status === "connected",
            label: checks[0].value.status,
          };
        if (checks[1].status === "fulfilled")
          status.openai = {
            connected:
              !!checks[1].value.configured && !!checks[1].value.enabled,
            label: checks[1].value.configured ? "configured" : "not_connected",
          };
        if (checks[2].status === "fulfilled")
          status.telegram = {
            connected: true,
            channel: !!checks[2].value.configured,
            label: checks[2].value.configured
              ? "channel_connected"
              : "connected",
          };
        if (checks[3].status === "fulfilled") {
          const value = checks[3].value.integration;
          status.beinsystems = {
            connected: value.status === "connected",
            label: value.status,
            primary: value.primary,
            base_url: value.base_url,
            branch_id: value.branch_id,
            capabilities: value.capabilities,
          };
        }
      }
      setDetails(status);
      return status;
    } catch (e) {
      setError(e.message);
      return {};
    }
  };
  useEffect(() => {
    load();
  }, []);
  const categories = [
    "Hamısı",
    ...new Set(integrationCatalog.map((item) => item.category)),
  ];
  const rows = integrationCatalog.filter(
    (item) =>
      (category === "Hamısı" || item.category === category) &&
      [item.name, item.category, item.description]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  const choose = (item) => {
    setNotice("");
    setError("");
    setSelected(item);
    if (item.id === "beinsystems") {
      const current = details.beinsystems || {};
      setBeinForm({
        base_url: current.base_url || "",
        branch_id: current.branch_id || "",
        token: "",
      });
    }
  };
  const openConfiguration = (item) => {
    setSelected(null);
    if (item.action === "setup") location.href = "/setup";
    else window.tenantOpenSettingsSection?.(item.section || "integrations");
  };
  const beinCommand = async (action, extra = {}) => {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      const result = await api("/api/platform/integrations/beinsystems", {
        method: "POST",
        body: JSON.stringify({ action, ...extra }),
      });
      setNotice(result.message || "Dəyişiklik saxlanıldı.");
      const next = await load();
      const current = next.beinsystems || {};
      setBeinForm((form) => ({
        ...form,
        token: "",
        base_url: current.base_url || form.base_url,
        branch_id: current.branch_id || form.branch_id,
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };
  const disconnectBein = async () => {
    if (!confirm("BeinSystems ERP bağlantısı ayrılsın?")) return;
    setBusy("disconnect");
    setError("");
    try {
      await api("/api/platform/integrations/disconnect", {
        method: "POST",
        body: JSON.stringify({ provider: "beinsystems" }),
      });
      setNotice("BeinSystems ERP bağlantısı ayrıldı.");
      await load();
      setBeinForm({ base_url: "", branch_id: "", token: "" });
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };
  if (!profile && !error)
    return (
      <Flex py="16" justify="center">
        <Spinner color="brand.500" />
      </Flex>
    );
  const owner = profile?.member?.role === "owner";
  return (
    <Stack gap="6">
      <Card.Root borderRadius="xl" borderColor="app.line" shadow="xs">
        <Card.Body p={{ base: "4", md: "5" }}>
          <Flex
            direction={{ base: "column", md: "row" }}
            gap="3"
            align={{ md: "center" }}
            justify="space-between"
          >
            <Box>
              <Heading size="md">Tətbiq marketi</Heading>
              <Text mt="1" color="app.muted" fontSize="sm">
                CRM Assistant imkanlarını şirkətinizə uyğun xidmətlərlə
                genişləndirin.
              </Text>
            </Box>
            <Input
              maxW={{ md: "360px" }}
              bg="app.surface"
              placeholder="İnteqrasiya axtarın"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Flex>
          <Flex gap="2" mt="4" overflowX="auto" pb="1">
            {categories.map((item) => (
              <Button
                key={item}
                flex="none"
                size="sm"
                colorPalette="brand"
                variant={category === item ? "solid" : "outline"}
                onClick={() => setCategory(item)}
              >
                {item}
              </Button>
            ))}
          </Flex>
        </Card.Body>
      </Card.Root>
      {error && (
        <Box bg="red.50" color="red.700" p="4" borderRadius="xl">
          {error}
        </Box>
      )}
      <Grid
        templateColumns={{
          base: "1fr",
          md: "repeat(2,minmax(0,1fr))",
          xl: "repeat(3,minmax(0,1fr))",
        }}
        gap="4"
      >
        {rows.map((item) => {
          const state = details[item.id] || {};
          return (
            <Card.Root
              key={item.id}
              borderRadius="xl"
              borderColor="app.line"
              shadow="xs"
              transition=".18s ease"
              _hover={{
                shadow: "md",
                transform: "translateY(-2px)",
                borderColor: "brand.500",
              }}
            >
              <Card.Header pb="3">
                <Flex justify="space-between" gap="3">
                  <Flex gap="3" align="center">
                    <Flex
                      w="12"
                      h="12"
                      borderRadius="xl"
                      align="center"
                      justify="center"
                      bg={item.color}
                      color="white"
                      fontWeight="900"
                    >
                      {item.mark}
                    </Flex>
                    <Box>
                      <Card.Title>{item.name}</Card.Title>
                      <Text color="app.muted" fontSize="xs">
                        {item.category}
                      </Text>
                    </Box>
                  </Flex>
                  <StatusBadge
                    tone={
                      item.pending
                        ? "gray"
                        : state.connected
                          ? "green"
                          : "orange"
                    }
                  >
                    {item.pending
                      ? "Tezliklə"
                      : state.connected
                        ? "Qoşulub"
                        : "Qoşulmayıb"}
                  </StatusBadge>
                </Flex>
              </Card.Header>
              <Card.Body pt="0">
                <Text color="app.muted" fontSize="sm" lineHeight="1.65">
                  {item.description}
                </Text>
                <Flex mt="4" gap="2" wrap="wrap">
                  {item.features.map((feature) => (
                    <Badge
                      key={feature}
                      variant="subtle"
                      borderRadius="full"
                      px="2.5"
                    >
                      {feature}
                    </Badge>
                  ))}
                </Flex>
              </Card.Body>
              <Card.Footer>
                <Button
                  w="full"
                  colorPalette="brand"
                  variant={item.pending ? "outline" : "solid"}
                  disabled={item.pending}
                  onClick={() => choose(item)}
                >
                  {item.pending ? "Gözləmə siyahısı" : "Ətraflı və ayarlar"}
                </Button>
              </Card.Footer>
            </Card.Root>
          );
        })}
      </Grid>
      {!rows.length && <Empty>Axtarışa uyğun inteqrasiya tapılmadı.</Empty>}
      <Dialog.Root
        open={!!selected}
        onOpenChange={(event) => !event.open && setSelected(null)}
        closeOnInteractOutside={false}
        size={{ mdDown: "full", md: "lg" }}
        placement="center"
      >
        <Portal>
          <Dialog.Backdrop />
          <Dialog.Positioner>
            <Dialog.Content borderRadius={{ mdDown: 0, md: "2xl" }}>
              {selected && (
                <>
                  <Dialog.Header borderBottomWidth="1px">
                    <Flex align="center" gap="3">
                      <Flex
                        w="11"
                        h="11"
                        borderRadius="xl"
                        bg={selected.color}
                        color="white"
                        align="center"
                        justify="center"
                        fontWeight="900"
                      >
                        {selected.mark}
                      </Flex>
                      <Box>
                        <Dialog.Title>{selected.name}</Dialog.Title>
                        <Dialog.Description>
                          {selected.category}
                        </Dialog.Description>
                      </Box>
                    </Flex>
                  </Dialog.Header>
                  <Dialog.Body py="5">
                    <Text color="app.ink">{selected.description}</Text>
                    {selected.id === "beinsystems" && owner ? (
                      <Stack mt="5" gap="4">
                        <Box>
                          <Text fontSize="sm" fontWeight="700" mb="1">
                            Production API ünvanı
                          </Text>
                          <Input
                            placeholder="https://erp.example.az/api/v1"
                            value={beinForm.base_url}
                            onChange={(e) =>
                              setBeinForm({
                                ...beinForm,
                                base_url: e.target.value,
                              })
                            }
                          />
                        </Box>
                        <Box>
                          <Text fontSize="sm" fontWeight="700" mb="1">
                            Integration token
                          </Text>
                          <Input
                            type="password"
                            autoComplete="new-password"
                            placeholder={
                              details.beinsystems?.connected
                                ? "Dəyişmək üçün yeni token daxil edin"
                                : "bei_int_..."
                            }
                            value={beinForm.token}
                            onChange={(e) =>
                              setBeinForm({
                                ...beinForm,
                                token: e.target.value,
                              })
                            }
                          />
                          <Text fontSize="xs" color="app.muted" mt="1">
                            Token yalnız serverdə şifrələnmiş saxlanılır və
                            yenidən göstərilmir.
                          </Text>
                        </Box>
                        <Box>
                          <Text fontSize="sm" fontWeight="700" mb="1">
                            Filial ID
                          </Text>
                          <Input
                            placeholder="Filialın UUID-si"
                            value={beinForm.branch_id}
                            onChange={(e) =>
                              setBeinForm({
                                ...beinForm,
                                branch_id: e.target.value,
                              })
                            }
                          />
                        </Box>
                        <Checkbox.Root
                          checked={beinForm.primary}
                          onCheckedChange={(event) =>
                            setBeinForm({
                              ...beinForm,
                              primary: event.checked === true,
                            })
                          }
                          colorPalette="brand"
                        >
                          <Checkbox.HiddenInput />
                          <Checkbox.Control>
                            <Checkbox.Indicator />
                          </Checkbox.Control>
                          <Checkbox.Label>
                            BeinSystems ERP-ni bu şirkət üçün əsas CRM et
                          </Checkbox.Label>
                        </Checkbox.Root>
                        <Box
                          p="3"
                          bg="orange.50"
                          color="orange.800"
                          borderRadius="lg"
                          fontSize="sm"
                        >
                          Sövdələşmə, müştəri, tapşırıq, vərəq və mərhələlər
                          dəstəklənir. Mesaj tarixçəsi və CRM qeydləri rəsmi
                          API-də olmadığı üçün bu kanallar Kommo bağlantısından
                          işləməyə davam edir.
                        </Box>
                        {notice && (
                          <Box
                            p="3"
                            bg="app.raised"
                            color="app.ink"
                            borderColor="app.line"
                            borderWidth="1px"
                            borderRadius="lg"
                          >
                            {notice}
                          </Box>
                        )}
                        {error && (
                          <Box
                            p="3"
                            bg="app.raised"
                            color="app.ink"
                            borderColor="app.line"
                            borderWidth="1px"
                            borderRadius="lg"
                          >
                            {error}
                          </Box>
                        )}
                      </Stack>
                    ) : selected.id === "beinsystems" ? (
                      <Box
                        mt="5"
                        p="3"
                        bg="orange.50"
                        color="orange.800"
                        borderRadius="lg"
                      >
                        Bağlantını yalnız şirkət sahibi idarə edə bilər.
                      </Box>
                    ) : (
                      <Stack mt="5" gap="2">
                        {selected.features.map((feature) => (
                          <Flex key={feature} gap="2" align="center">
                            <Box
                              w="2"
                              h="2"
                              borderRadius="full"
                              bg="brand.500"
                            />
                            <Text>{feature}</Text>
                          </Flex>
                        ))}
                      </Stack>
                    )}
                  </Dialog.Body>
                  <Dialog.Footer borderTopWidth="1px" flexWrap="wrap">
                    <Button variant="outline" onClick={() => setSelected(null)}>
                      Bağla
                    </Button>
                    {selected.id === "beinsystems" && owner ? (
                      <>
                        {details.beinsystems?.connected && (
                          <Button
                            variant="outline"
                            onClick={() => beinCommand("test")}
                            loading={busy === "test"}
                          >
                            Yoxla
                          </Button>
                        )}
                        {details.beinsystems?.connected &&
                          !details.beinsystems?.primary && (
                            <Button
                              variant="outline"
                              colorPalette="brand"
                              onClick={() =>
                                beinCommand("primary", {
                                  provider: "beinsystems",
                                })
                              }
                              loading={busy === "primary"}
                            >
                              Əsas CRM et
                            </Button>
                          )}
                        {details.beinsystems?.connected && (
                          <Button
                            variant="ghost"
                            colorPalette="red"
                            onClick={disconnectBein}
                            loading={busy === "disconnect"}
                          >
                            Ayır
                          </Button>
                        )}
                        <Button
                          colorPalette="brand"
                          onClick={() => beinCommand("connect", beinForm)}
                          loading={busy === "connect"}
                          disabled={
                            !beinForm.base_url ||
                            !beinForm.branch_id ||
                            !beinForm.token
                          }
                        >
                          Yoxla və qoş
                        </Button>
                      </>
                    ) : (
                      selected.id !== "beinsystems" && (
                        <Button
                          colorPalette="brand"
                          onClick={() => openConfiguration(selected)}
                        >
                          Ayarları aç
                        </Button>
                      )
                    )}
                  </Dialog.Footer>
                </>
              )}
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>
    </Stack>
  );
}

function IntegrationMarketplace() {
  const [language, setLanguage] = useState(window.tenantI18n?.language || "az");
  const [profile, setProfile] = useState(null);
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("connection");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("Hamısı");
  const [details, setDetails] = useState({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [beinForm, setBeinForm] = useState({
    base_url: "",
    branch_id: "",
    token: "",
  });
  const owner = ["owner", "admin"].includes(profile?.member?.role);
  useEffect(() => {
    const update = () => setLanguage(window.tenantI18n?.language || "az");
    document.addEventListener("tenant-language", update);
    return () => document.removeEventListener("tenant-language", update);
  }, []);
  useEffect(() => {
    setCategory({ ru: "Все", az: "Hamısı", en: "All" }[language] || "All");
  }, [language]);

  const load = async () => {
    setError("");
    try {
      const me = await api("/api/platform/me");
      setProfile(me);
      const status = {};
      (me.integrations || []).forEach((row) => {
        const key = row.provider === "chat_ai" ? "openai" : row.provider;
        status[key] = {
          connected: ["connected", "configured"].includes(row.status),
          label: row.status,
          base_url: row.account_domain || "",
          branch_id: row.metadata?.branch_id || "",
        };
      });
      status.telegram = {
        connected: !!me.member?.telegram_id,
        label: "connected",
      };
      if (!(me.integrations || []).some(row => row.management_actions) && ["owner", "admin"].includes(me.member?.role)) {
        const checks = await Promise.allSettled([
          api("/api/platform/linear/settings"),
          api("/api/platform/chat/ai/settings"),
          api("/api/platform/news/telegram/settings"),
          api("/api/platform/integrations/beinsystems"),
          api("/api/platform/integrations/connect"),
        ]);
        if (checks[0].status === "fulfilled")
          status.linear = {
            connected: checks[0].value.status === "connected",
            label: checks[0].value.status,
          };
        if (checks[1].status === "fulfilled")
          status.openai = {
            connected: !!checks[1].value.configured,
            label: checks[1].value.configured ? "configured" : "not_connected",
          };
        if (checks[2].status === "fulfilled")
          status.telegram = {
            connected: true,
            channel: !!checks[2].value.configured,
            label: checks[2].value.configured
              ? "channel_connected"
              : "connected",
          };
        if (checks[3].status === "fulfilled") {
          const value = checks[3].value.integration;
          status.beinsystems = {
            connected: value.status === "connected",
            label: value.status,
            base_url: value.base_url,
            branch_id: value.branch_id,
            api_ready: value.api_ready,
            capabilities: value.capabilities,
          };
        }
        if (checks[4].status === "fulfilled")
          status.connect = {
            connected: !!checks[4].value.connected,
            channelCount: (checks[4].value.channels || []).filter(
              (channel) => channel.active,
            ).length,
          };
      }
      setDetails(status);
      return status;
    } catch (e) {
      setError(e.message);
      return {};
    }
  };

  useEffect(() => {
    load();
  }, []);
  const managementTab = tab === "sync" ? "settings" : tab;
  useEffect(() => {
    if (
      !selected ||
      (managementTab !== "settings" && !window.tenantSharedIntegrations?.scoped(profile, selected.id)) ||
      (selected.id === "connect" && !owner && !window.tenantSharedIntegrations?.scoped(profile, selected.id))
    )
      return;
    const detail = {
      provider: selected.id,
      hostId: "integrationSettingsHost",
      open: true,
    };
    const frame = requestAnimationFrame(() =>
      document.dispatchEvent(
        new CustomEvent("tenant-integration-panel", { detail }),
      ),
    );
    return () => {
      cancelAnimationFrame(frame);
      document.dispatchEvent(
        new CustomEvent("tenant-integration-panel", {
          detail: { ...detail, open: false },
        }),
      );
    };
  }, [selected?.id, managementTab, owner]);

  const localeCopy = {
    ru: {
      all: "Все",
      market: "Интеграции",
      intro: "Подключения компании в одном месте.",
      search: "Найти интеграцию",
      connected: "Подключено",
      disconnected: "Не подключено",
      manage: "Управлять",
      connect: "Подключить",
      noData: "Нет данных",
      access: "Управление доступно владельцу или администратору компании.",
      close: "Закрыть",
      connection: "Подключение",
      settings: "Настройки",
      kommoOwnerOnly: "Ручное подключение Kommo доступно только владельцу компании.",
      empty: "Интеграции не найдены.",
    },
    az: {
      all: "Hamısı",
      market: "İnteqrasiyalar",
      intro: "Şirkət bağlantılarını bir yerdən idarə edin.",
      search: "İnteqrasiya axtarın",
      connected: "Qoşulub",
      disconnected: "Qoşulmayıb",
      manage: "İdarə et",
      connect: "Qoş",
      noData: "Məlumat yoxdur",
      access:
        "Bu bağlantını yalnız şirkət sahibi və ya administrator idarə edə bilər.",
      close: "Bağla",
      connection: "Qoşulma",
      settings: "Ayarlar",
      kommoOwnerOnly: "Əl ilə Kommo bağlantısı yalnız şirkət sahibinə açıqdır.",
      empty: "İnteqrasiya tapılmadı.",
    },
    en: {
      all: "All",
      market: "Integrations",
      intro: "Manage company connections in one place.",
      search: "Search integrations",
      connected: "Connected",
      disconnected: "Not connected",
      manage: "Manage",
      connect: "Connect",
      noData: "No data",
      access:
        "Only a company owner or administrator can manage this connection.",
      close: "Close",
      connection: "Connection",
      settings: "Settings",
      kommoOwnerOnly: "Manual Kommo connection is available only to the company owner.",
      empty: "No integrations found.",
    },
  };
  const tx = (key) => localeCopy[language]?.[key] || localeCopy.en[key];
  const visibleProviders = new Set((profile?.integrations || []).map((row) => row.provider));
  const items = integrationCatalog
    .filter((item) => !item.privateProvider || visibleProviders.has(item.id))
    .map((item) => ({
      ...item,
      ...(item.copy?.[language] || item.copy?.en),
    }));
  const categories = [
    tx("all"),
    ...new Set(items.map((item) => item.category)),
  ];
  const rows = items.filter(
    (item) =>
      (category === tx("all") || item.category === category) &&
      [item.name, item.category, item.description]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  const choose = (item) => {
    setSelected(item);
    setTab(item.id === "connect" || (item.id === "beinsystems_operator" && profile?.member?.role === "owner")
      ? "settings" : "connection");
    setError("");
    setNotice("");
    if (item.id === "beinsystems") {
      const current = details.beinsystems || {};
      setBeinForm({
        base_url: current.base_url || "",
        branch_id: current.branch_id || "",
        token: "",
      });
    }
  };
  const command = async (provider, action, body = {}) => {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      if (provider === "beinsystems") {
        const result = await api("/api/platform/integrations/beinsystems", {
          method: "POST",
          body: JSON.stringify({ action, ...body }),
        });
        setNotice(result.message || "Dəyişiklik saxlanıldı.");
      } else if (provider === "kommo" && action === "test") {
        const result = await api("/api/platform/integrations/kommo/test");
        setNotice(
          `Bağlantı işləyir: ${result.account?.name || "Kommo hesabı"}`,
        );
      } else if (action === "disconnect") {
        await api("/api/platform/integrations/disconnect", {
          method: "POST",
          body: JSON.stringify({ provider }),
        });
        setNotice("Bağlantı ayrıldı.");
      }
      const next = await load();
      if (provider === "beinsystems")
        setBeinForm((form) => ({
          ...form,
          token: "",
          base_url: next.beinsystems?.base_url || form.base_url,
          branch_id: next.beinsystems?.branch_id || form.branch_id,
        }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };
  const connectKommo = () => {
    location.href = "/setup";
  };

  if (!profile && !error)
    return (
      <Flex py="16" justify="center">
        <Spinner color="brand.500" />
      </Flex>
    );
  const state = selected ? details[selected.id] || {} : {};
  return (
    <Stack gap="6">
      <Card.Root
        borderRadius="xl"
        borderColor="app.line"
        shadow="xs"
        overflow="hidden"
        bg="app.surface"
      >
        <Card.Body p={{ base: "5", md: "6" }}>
          <Flex
            direction={{ base: "column", md: "row" }}
            gap="4"
            align={{ md: "center" }}
            justify="space-between"
          >
            <Box>
              <Heading size="lg">{tx("market")}</Heading>
              <Text mt="1" color="app.muted" fontSize="sm">
                {tx("intro")}
              </Text>
            </Box>
            <Input
              maxW={{ md: "360px" }}
              h="10"
              bg="app.surface"
              borderRadius="xl"
              placeholder={tx("search")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </Flex>
          <Flex gap="2" mt="5" overflowX="auto" pb="1">
            {categories.map((item) => (
              <Button
                key={item}
                flex="none"
                size="sm"
                borderRadius="full"
                colorPalette="brand"
                variant={category === item ? "solid" : "outline"}
                onClick={() => setCategory(item)}
              >
                {item}
              </Button>
            ))}
          </Flex>
        </Card.Body>
      </Card.Root>
      {error && !selected && (
        <Box bg="red.50" color="red.700" p="4" borderRadius="xl">
          {error}
        </Box>
      )}
      <Grid
        templateColumns={{
          base: "1fr",
          sm: "repeat(2,minmax(0,1fr))",
          lg: "repeat(3,minmax(0,1fr))",
        }}
        gap="4"
      >
        {rows.map((item) => {
          const itemState = details[item.id] || {};
          return (
            <Card.Root
              key={item.id}
              minW="0"
              bg="app.surface"
              borderColor="app.line"
              borderRadius="xl"
              shadow="xs"
              overflow="hidden"
              transition="border-color .18s ease, box-shadow .18s ease, transform .18s ease"
              _hover={{
                borderColor: "gray.300",
                shadow: "md",
                transform: "translateY(-2px)",
              }}
            >
              <Card.Body p="3">
                <Flex align="center" gap="3">
                  <IntegrationLogo provider={item.id} size="sm" />
                  <Box minW="0" flex="1">
                    <Text
                      color="app.ink"
                      fontSize="md"
                      fontWeight="650"
                      lineHeight="1.3"
                    >
                      {item.name}
                    </Text>
                    <Text
                      mt="1"
                      color="app.muted"
                      fontSize="sm"
                      lineHeight="1.4"
                      lineClamp="2"
                    >
                      {item.description}
                    </Text>
                  </Box>
                </Flex>
              </Card.Body>
              <Card.Footer
                px="3"
                py="2"
                borderTopWidth="1px"
                borderColor="app.line"
                bg="app.raised"
              >
                <Flex w="full" align="center" justify="space-between" gap="3">
                  <Flex minW="0" align="center" gap="2">
                    <Box
                      w="2"
                      h="2"
                      flex="none"
                      borderRadius="full"
                      bg={itemState.connected ? "green.500" : "orange.400"}
                    />
                    <Text color="app.muted" fontSize="xs" fontWeight="650">
                      {itemState.connected
                        ? tx("connected")
                        : item.id === "connect" && !owner
                          ? tx("noData")
                          : tx("disconnected")}
                    </Text>
                  </Flex>
                  <Button
                    size="sm"
                    borderRadius="lg"
                    colorPalette="brand"
                    variant={itemState.connected ? "outline" : "solid"}
                    onClick={() => choose(item)}
                  >
                    {itemState.connected || item.id === "connect"
                      ? tx("manage")
                      : tx("connect")}
                  </Button>
                </Flex>
              </Card.Footer>
            </Card.Root>
          );
        })}
      </Grid>
      {!rows.length && <Empty>{tx("empty")}</Empty>}
      <Dialog.Root
        open={!!selected}
        onOpenChange={(event) => !event.open && setSelected(null)}
        closeOnInteractOutside={false}
        size={{ mdDown: "full", md: "xl" }}
        placement="center"
      >
        <Portal>
          <Dialog.Backdrop />
          <Dialog.Positioner>
            <Dialog.Content
              id="integrationMarketplaceDialog"
              maxH={{ mdDown: "100dvh", md: "88vh" }}
              borderRadius={{ mdDown: 0, md: "3xl" }}
              overflow="hidden"
            >
              {selected && (
                <>
                  <Dialog.Header
                    p={{ base: "5", md: "6" }}
                    bg="app.surface"
                    borderBottomWidth="1px"
                  >
                    <Flex align="center" gap="4">
                      <IntegrationLogo provider={selected.id} size="lg" />
                      <Box flex="1" minW="0">
                        <Dialog.Title color="app.ink">
                          {selected.name}
                        </Dialog.Title>
                        <Dialog.Description color="app.muted">
                          {selected.description}
                        </Dialog.Description>
                      </Box>
                      <StatusBadge tone={state.connected ? "green" : "orange"}>
                        {state.connected
                          ? tx("connected")
                          : selected.id === "connect" && !owner
                            ? tx("noData")
                            : tx("disconnected")}
                      </StatusBadge>
                    </Flex>
                  </Dialog.Header>
                  {selected.id !== "connect" && (
                    <Box px={{ base: "5", md: "6" }} pt="4">
                      <Flex
                        className="app-integration-tabs"
                        role="tablist"
                        aria-label={tx("market")}
                      >
                        <Button
                          role="tab"
                          aria-selected={tab === "connection"}
                          variant="ghost"
                          onClick={() => setTab("connection")}
                        >
                          {tx("connection")}
                        </Button>
                        <Button
                          role="tab"
                          aria-selected={tab === "settings"}
                          variant="ghost"
                          onClick={() => setTab("settings")}
                        >
                          {tx("settings")}
                        </Button>
                        {selected.id === "kommo" && <Button role="tab" aria-selected={tab === "sync"} variant="ghost" onClick={() => setTab("sync")}>
                          {({ru:"Синхронизация",az:"Sinxronizasiya",en:"Synchronization"})[window.tenantI18n?.language] || "Sinxronizasiya"}
                        </Button>}
                      </Flex>
                    </Box>
                  )}
                  <Dialog.Body data-kommo-pane={selected.id === "kommo" ? tab : undefined} p={{ base: "5", md: "6" }} overflowY="auto">
                    {window.tenantSharedIntegrations?.scoped(profile, selected.id) ? (<Box><Text>Şirkətin ortaq bağlantısı · {state.label || "not_connected"}</Text>{!Object.values(window.tenantSharedIntegrations.row(profile, selected.id).management_actions).some(Boolean) && <Text>Yalnız bağlantı statusu görünür.</Text>}<Box id="integrationSettingsHost" className="integration-settings-host" /></Box>) : tab === "connection" && (
                      <Stack gap="4">
                        {!owner && (
                          <Box
                            p="4"
                            bg="app.raised"
                            color="app.ink"
                            borderRadius="xl"
                          >
                            {tx("access")}
                          </Box>
                        )}
                        {selected.id === "beinsystems" && owner && (
                          <>
                            <Box
                              p="4"
                              bg="app.raised"
                              color="app.ink"
                              borderRadius="xl"
                              fontSize="sm"
                            >
                              BeinSystems-in açıq API və Webhook ünvanları hazır
                              olana qədər yalnız integration tokenini qoşmaq
                              kifayətdir. API aktivləşəndə əlavə ünvan tələb
                              olunmadan bağlantı tamamlanacaq.
                            </Box>
                            <Box>
                              <Text fontSize="sm" fontWeight="700" mb="1">
                                Integration token
                              </Text>
                              <Input
                                type="password"
                                autoComplete="new-password"
                                placeholder={
                                  state.connected
                                    ? "Tokeni dəyişmək üçün yenisini daxil edin"
                                    : "bei_int_..."
                                }
                                value={beinForm.token}
                                onChange={(e) =>
                                  setBeinForm({
                                    ...beinForm,
                                    token: e.target.value,
                                  })
                                }
                              />
                              <Text fontSize="xs" color="app.muted" mt="1">
                                Token serverdə şifrələnir və heç bir
                                istifadəçiyə geri göstərilmir.
                              </Text>
                            </Box>
                            <Flex gap="2" wrap="wrap">
                              <Button
                                colorPalette="brand"
                                onClick={() =>
                                  command("beinsystems", "connect", beinForm)
                                }
                                loading={busy === "connect"}
                                disabled={!beinForm.token}
                              >
                                Tokeni qoş
                              </Button>
                              {state.connected && state.api_ready && (
                                <Button
                                  variant="outline"
                                  onClick={() => command("beinsystems", "test")}
                                  loading={busy === "test"}
                                >
                                  Bağlantını yoxla
                                </Button>
                              )}
                              {state.connected && (
                                <Button
                                  colorPalette="red"
                                  variant="ghost"
                                  onClick={() =>
                                    confirm("Bağlantı ayrılsın?") &&
                                    command("beinsystems", "disconnect")
                                  }
                                  loading={busy === "disconnect"}
                                >
                                  Ayır
                                </Button>
                              )}
                            </Flex>
                          </>
                        )}
                        {selected.id === "kommo" && owner && (
                          <>
                            <Box
                              p="4"
                              bg="app.raised"
                              color="app.ink"
                              borderRadius="xl"
                            >
                              <Text fontWeight="600">
                                {state.connected
                                  ? `Kommo qoşulub: ${state.base_url || "hesab"}`
                                  : "Kommo OAuth ilə qoşulmayıb"}
                              </Text>
                              <Text mt="1" color="app.muted" fontSize="sm">
                                OAuth pəncərəsində Kommo hesabını seçərək
                                təhlükəsiz giriş edin.
                              </Text>
                            </Box>
                            <Flex gap="2" wrap="wrap">
                              <Button
                                colorPalette="brand"
                                onClick={connectKommo}
                              >
                                {state.connected
                                  ? "Yenidən qoş"
                                  : "Kommo ilə qoşul"}
                              </Button>
                              {state.connected && (
                                <Button
                                  variant="outline"
                                  onClick={() => command("kommo", "test")}
                                  loading={busy === "test"}
                                >
                                  Yoxla
                                </Button>
                              )}
                              {state.connected && (
                                <Button
                                  colorPalette="red"
                                  variant="ghost"
                                  onClick={() =>
                                    confirm("Kommo bağlantısı ayrılsın?") &&
                                    command("kommo", "disconnect")
                                  }
                                  loading={busy === "disconnect"}
                                >
                                  Ayır
                                </Button>
                              )}
                            </Flex>
                          </>
                        )}
                        {!["beinsystems", "kommo", "connect"].includes(
                          selected.id,
                        ) && (
                          <Box
                            p="4"
                            bg="app.raised"
                            color="app.ink"
                            borderRadius="xl"
                          >
                            <Text fontWeight="600">
                              {state.connected
                                ? tx("connected")
                                : tx("disconnected")}
                            </Text>
                            {owner && (
                              <Button
                                mt="4"
                                colorPalette="brand"
                                onClick={() => setTab("settings")}
                              >
                                {tx("settings")}
                              </Button>
                            )}
                          </Box>
                        )}
                        {notice && (
                          <Box
                            p="3"
                            bg="app.raised"
                            color="app.ink"
                            borderColor="app.line"
                            borderWidth="1px"
                            borderRadius="lg"
                          >
                            {notice}
                          </Box>
                        )}
                        {error && (
                          <Box
                            p="3"
                            bg="app.raised"
                            color="app.ink"
                            borderColor="app.line"
                            borderWidth="1px"
                            borderRadius="lg"
                          >
                            {error}
                          </Box>
                        )}
                      </Stack>
                    )}
                    {managementTab === "settings" && !window.tenantSharedIntegrations?.scoped(profile, selected.id) && (
                      <>
                        {selected.id === "connect" ? (
                          owner ? (
                            <Box
                              id="integrationSettingsHost"
                              className="integration-settings-host"
                            />
                          ) : (
                            <Box
                              p="4"
                              bg="app.raised"
                              color="app.ink"
                              borderRadius="xl"
                            >
                              {tx("access")}
                            </Box>
                          )
                        ) : selected.id === "beinsystems" ? (
                          <Stack gap="3">
                            {selected.features.map((feature) => (
                              <Flex
                                key={feature}
                                gap="3"
                                align="center"
                                p="3"
                                borderWidth="1px"
                                borderRadius="xl"
                              >
                                <Box
                                  w="2.5"
                                  h="2.5"
                                  bg={
                                    state.api_ready ? "brand.500" : "gray.300"
                                  }
                                  borderRadius="full"
                                />
                                <Text>{feature}</Text>
                              </Flex>
                            ))}
                            <Box
                              p="4"
                              bg="app.raised"
                              color="app.ink"
                              borderRadius="xl"
                              fontSize="sm"
                            >
                              {state.api_ready
                                ? "API bağlantısı aktivdir."
                                : "Token qoşulub. BeinSystems API və Webhook-u istifadəyə verdikdən sonra resurs sinxronizasiyası aktivləşdiriləcək."}
                            </Box>
                          </Stack>
                        ) : selected.id === "kommo" ? (
                          <Stack gap="3">
                            <Box hidden={tab === "sync"}>
                            {selected.features.map((feature) => (
                              <Flex
                                key={feature}
                                gap="3"
                                align="center"
                                p="3"
                                borderWidth="1px"
                                borderRadius="xl"
                              >
                                <Box
                                  w="2.5"
                                  h="2.5"
                                  bg="brand.500"
                                  borderRadius="full"
                                />
                                <Text>{feature}</Text>
                              </Flex>
                            ))}
                            {owner && (
                              <Button
                                alignSelf="start"
                                colorPalette="brand"
                                onClick={connectKommo}
                              >
                                Vərəq və mərhələləri seç
                              </Button>
                            )}
                            </Box>
                            {profile?.member?.role === "owner" ? (
                              <Box
                                id="integrationSettingsHost"
                                className="integration-settings-host"
                              />
                            ) : (
                              <Box
                                p="4"
                                bg="app.raised"
                                color="app.ink"
                                borderRadius="xl"
                              >
                                {tx("kommoOwnerOnly")}
                              </Box>
                            )}
                          </Stack>
                        ) : (
                          <Box
                            id="integrationSettingsHost"
                            className="integration-settings-host"
                          />
                        )}
                      </>
                    )}
                  </Dialog.Body>
                  <Dialog.Footer borderTopWidth="1px">
                    <Button variant="outline" onClick={() => setSelected(null)}>
                      {tx("close")}
                    </Button>
                  </Dialog.Footer>
                </>
              )}
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>
    </Stack>
  );
}

function PermissionCheckbox({ value, label, checked, onChange, disabled }) {
  return (
    <Checkbox.Root
      checked={checked}
      disabled={disabled}
      onCheckedChange={(event) => onChange(value, event.checked === true)}
      colorPalette="brand"
    >
      <Checkbox.HiddenInput />
      <Checkbox.Control>
        <Checkbox.Indicator />
      </Checkbox.Control>
      <Checkbox.Label fontSize="sm">{label}</Checkbox.Label>
    </Checkbox.Root>
  );
}

function MemberDialog({
  member,
  defaults = {},
  open,
  onClose,
  onSaved,
  actorRole = "owner",
}) {
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open && member)
      setDraft({ ...member, permissions: [...(member.permissions || [])] });
  }, [open, member]);
  if (!draft) return null;
  const roleKeys = Object.keys(defaults).filter(
    (role) =>
      (role !== "owner" || draft.role === "owner") &&
      (actorRole === "owner" || role !== "admin"),
  );
  const changeRole = (role) =>
    setDraft({ ...draft, role, permissions: [...(defaults[role] || [])] });
  const toggle = (key, checked) =>
    setDraft({
      ...draft,
      permissions: checked
        ? [...new Set([...draft.permissions, key])]
        : draft.permissions.filter((item) => item !== key),
    });
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await api("/api/platform/members", {
        method: "POST",
        body: JSON.stringify({
          action: "upsert",
          telegram_id: draft.telegram_id,
          display_name: draft.display_name,
          role: draft.role,
          permissions: draft.permissions,
        }),
      });
      await onSaved();
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(event) => !event.open && onClose()}
      closeOnInteractOutside={false}
      size={{ mdDown: "full", md: "lg" }}
      placement="center"
    >
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content
            maxH={{ mdDown: "100dvh", md: "88vh" }}
            borderRadius={{ mdDown: 0, md: "2xl" }}
            overflow="hidden"
          >
            <Dialog.Header borderBottomWidth="1px">
              <Dialog.Title>Əməkdaş hüquqları</Dialog.Title>
            </Dialog.Header>
            <Dialog.Body py="5" overflowY="auto">
              <Stack gap="5">
                <Box>
                  <Text fontSize="sm" fontWeight="700" mb="2">
                    Ad və soyad
                  </Text>
                  <Input
                    value={draft.display_name || ""}
                    onChange={(e) =>
                      setDraft({ ...draft, display_name: e.target.value })
                    }
                  />
                </Box>
                <Box>
                  <Text fontSize="sm" fontWeight="700" mb="2">
                    Əməkdaş növü
                  </Text>
                  <Box
                    as="select"
                    w="full"
                    borderWidth="1px"
                    borderColor="app.line"
                    borderRadius="lg"
                    p="2.5"
                    value={draft.role}
                    disabled={draft.role === "owner"}
                    onChange={(e) => changeRole(e.target.value)}
                  >
                    {roleKeys.map((role) => (
                      <option key={role} value={role}>
                        {roles[role] || role}
                      </option>
                    ))}
                  </Box>
                  <Text fontSize="xs" color="app.muted" mt="2">
                    Növ standart hüquqları seçir. Aşağıda istisnaları ayrıca
                    dəyişə bilərsiniz.
                  </Text>
                </Box>
                <Box>
                  <Text fontSize="sm" fontWeight="700" mb="1">
                    Bölmə hüquqları
                  </Text>
                  <Text fontSize="xs" color="app.muted" mb="3">
                    İşarələnmiş bölmələrdə əməkdaş məlumatları görə və ona
                    verilmiş əməliyyatları yerinə yetirə bilər.
                  </Text>
                  <Grid
                    templateColumns={{ base: "1fr", md: "1fr 1fr" }}
                    gap="3"
                  >
                    {Object.entries(permissionLabels).map(([key, label]) => (
                      <PermissionCheckbox
                        key={key}
                        value={key}
                        label={window.tenantI18n?.t(key) || label}
                        checked={draft.permissions.includes(key)}
                        disabled={draft.role === "owner"}
                        onChange={toggle}
                      />
                    ))}
                  </Grid>
                </Box>
                {error && (
                  <Box bg="app.raised" color="red.500" borderRadius="lg" p="3">
                    {error}
                  </Box>
                )}
              </Stack>
            </Dialog.Body>
            <Dialog.Footer borderTopWidth="1px">
              <Button variant="outline" onClick={onClose}>
                Bağla
              </Button>
              <Button colorPalette="brand" onClick={save} loading={saving}>
                Hüquqları saxla
              </Button>
            </Dialog.Footer>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
}

function SettingsHub() {
  const [data, setData] = useState(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [language, setLanguage] = useState(window.tenantI18n?.language || "az");
  const [account, setAccount] = useState({
      first_name: "",
      last_name: "",
      phone: "",
      login: "",
      avatar_url: "",
      email: "",
    }),
    [sessions, setSessions] = useState([]),
    [actionError, setActionError] = useState(""),
    [workspace, setWorkspace] = useState("");
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const me = await api("/api/platform/me");
      setData(me);
      setWorkspace(me.member.tenant_id);
      setAccount({ ...account, ...(me.account || {}) });
      try {
        const s = await api("/api/platform/sessions");
        setSessions(s.sessions || []);
      } catch (_e) {}
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    const relabel = () => setLanguage(window.tenantI18n?.language || "az");
    document.addEventListener("tenant-language", relabel);
    return () => document.removeEventListener("tenant-language", relabel);
  }, []);
  useEffect(() => {
    load();
  }, []);
  const copy = ({
    ru: {
      title: "Личный профиль", description: "Вход через Telegram и email в одной учётной записи.", first: "Имя", last: "Фамилия", phone: "Телефон", login: "Логин", avatar: "Ссылка на аватар HTTPS", emailMissing: "Email не подключён", verified: "подтверждён", save: "Сохранить профиль", addEmail: "Подключить email", emailPrompt: "Введите email", emailSent: "Ссылка для подтверждения email отправлена.", workspace: "Рабочее пространство компании", workspaceDescription: "Выбирайте только среди компаний, к которым у вас есть доступ.", switchWorkspace: "Сменить компанию", sessions: "Активные сеансы", sessionsDescription: "Завершайте сеансы, которыми больше не пользуетесь.", thisDevice: "Это устройство", otherDevice: "Другое устройство", close: "Завершить", noSessions: "Сохранённых сеансов нет.", roles: {owner:"Владелец",admin:"Администратор",manager:"Менеджер",worker:"Сотрудник"}
    },
    en: {
      title: "Personal profile", description: "Telegram and email sign-in can use the same account.", first: "First name", last: "Last name", phone: "Phone", login: "Login", avatar: "Avatar HTTPS URL", emailMissing: "Email is not connected", verified: "verified", save: "Save profile", addEmail: "Connect email", emailPrompt: "Enter your email address", emailSent: "Email verification link sent.", workspace: "Company workspace", workspaceDescription: "Choose only from companies where you are a member.", switchWorkspace: "Switch company", sessions: "Active sessions", sessionsDescription: "End sign-ins you no longer use.", thisDevice: "This device", otherDevice: "Other device", close: "End session", noSessions: "No saved sessions.", roles: {owner:"Owner",admin:"Administrator",manager:"Manager",worker:"Employee"}
    },
    az: {
      title: "Şəxsi hesab", description: "Telegram və email girişi eyni hesabda işləyə bilər.", first: "Ad", last: "Soyad", phone: "Telefon", login: "Login", avatar: "Avatar HTTPS URL", emailMissing: "Email qoşulmayıb", verified: "təsdiqlənib", save: "Profili saxla", addEmail: "Email qoş", emailPrompt: "Email ünvanınızı daxil edin", emailSent: "Email təsdiq keçidi göndərildi.", workspace: "Şirkət iş sahəsi", workspaceDescription: "Seçim yalnız sizin aktiv üzvlükləriniz arasından edilir.", switchWorkspace: "İş sahəsini dəyiş", sessions: "Aktiv sessiyalar", sessionsDescription: "İstifadə edilməyən girişləri bağlayın.", thisDevice: "Bu cihaz", otherDevice: "Digər cihaz", close: "Bağla", noSessions: "Saxlanmış sessiya tapılmadı.", roles: {owner:"Sahib",admin:"Administrator",manager:"Menecer",worker:"İcraçı"}
    }
  })[language] || {title:"Personal profile",description:"",first:"First name",last:"Last name",phone:"Phone",login:"Login",avatar:"Avatar HTTPS URL",emailMissing:"Email is not connected",verified:"verified",save:"Save profile",addEmail:"Connect email",emailPrompt:"Enter your email address",emailSent:"Verification link sent.",workspace:"Company workspace",workspaceDescription:"",switchWorkspace:"Switch company",sessions:"Active sessions",sessionsDescription:"",thisDevice:"This device",otherDevice:"Other device",close:"End session",noSessions:"No saved sessions.",roles:{}};
  if (loading)
    return (
      <Flex py="14" justify="center">
        <Spinner color="brand.500" />
      </Flex>
    );
  if (error)
    return (
      <Box p="4" bg="app.raised" color="red.500" borderRadius="xl">
        {error}
      </Box>
    );
  const saveAccount = async () => {
    setActionError("");
    try {
      const result = await api("/api/platform/account", {
        method: "POST",
        body: JSON.stringify(account),
      });
      setAccount({ ...account, ...result.account });
    } catch (e) {
      setActionError(e.message);
    }
  };
  const revokeSession = async (session) => {
    if (!confirm("Bu sessiyanı bağlamaq istəyirsiniz?")) return;
    try {
      await api("/api/platform/sessions", {
        method: "POST",
        body: JSON.stringify({ session_id: session.id }),
      });
      setSessions((rows) => rows.filter((row) => row.id !== session.id));
    } catch (e) {
      setActionError(e.message);
    }
  };
  const switchWorkspace = async () => {
    if (!workspace || workspace === data.member.tenant_id) return;
    try {
      await api("/api/platform/workspace/switch", {
        method: "POST",
        body: JSON.stringify({ tenant_id: workspace }),
      });
      location.href = "/app";
    } catch (e) {
      setActionError(e.message);
    }
  };
  return (
    <Stack gap="6" mt="5">
      <Card.Root borderRadius="xl" borderColor="app.line" shadow="xs">
        <Card.Header>
          <Card.Title>{copy.title}</Card.Title>
          <Card.Description>{copy.description}</Card.Description>
        </Card.Header>
        <Card.Body>
          <Grid templateColumns={{ base: "1fr", md: "1fr 1fr" }} gap="3">
            <Input
              placeholder={copy.first}
              value={account.first_name || ""}
              onChange={(e) =>
                setAccount({ ...account, first_name: e.target.value })
              }
            />
            <Input
              placeholder={copy.last}
              value={account.last_name || ""}
              onChange={(e) =>
                setAccount({ ...account, last_name: e.target.value })
              }
            />
            <Input
              placeholder={copy.phone}
              value={account.phone || ""}
              onChange={(e) =>
                setAccount({ ...account, phone: e.target.value })
              }
            />
            <Input
              placeholder={copy.login}
              value={account.login || ""}
              onChange={(e) =>
                setAccount({ ...account, login: e.target.value })
              }
            />
            <Input
              placeholder={copy.avatar}
              value={account.avatar_url || ""}
              onChange={(e) =>
                setAccount({ ...account, avatar_url: e.target.value })
              }
            />
            <Text alignSelf="center" fontSize="sm" color="app.muted">
              {account.email
                ? `${account.email}${account.verified ? ` · ${copy.verified}` : ""}`
                : copy.emailMissing}
            </Text>
          </Grid>
          <Flex mt="3" gap="2" wrap="wrap" minW="0">
            <Button colorPalette="brand" onClick={saveAccount}>
              {copy.save}
            </Button>
            {!account.email && (
              <Button
                variant="outline"
                onClick={async () => {
                  const email = prompt(copy.emailPrompt);
                  if (!email) return;
                  try {
                    await api("/api/platform/auth/email/link/request", {
                      method: "POST",
                      body: JSON.stringify({ email }),
                    });
                    setActionError(copy.emailSent);
                  } catch (e) {
                    setActionError(e.message);
                  }
                }}
              >
              {copy.addEmail}
              </Button>
            )}
          </Flex>
        </Card.Body>
      </Card.Root>
      {(data.companies || []).length > 1 && (
        <Card.Root borderRadius="xl" borderColor="app.line" shadow="xs">
          <Card.Header>
            <Card.Title>{copy.workspace}</Card.Title>
            <Card.Description>{copy.workspaceDescription}</Card.Description>
          </Card.Header>
          <Card.Body>
            <Flex gap="3" wrap="wrap">
              <Box
                as="select"
                minW={{ base: "full", md: "320px" }}
                borderWidth="1px"
                borderColor="app.line"
                borderRadius="lg"
                p="2.5"
                value={workspace}
                onChange={(e) => setWorkspace(e.target.value)}
              >
                {data.companies.map((company) => (
                  <option key={company.id} value={company.id}>
                    {company.name} · {copy.roles[company.role] || company.role}
                  </option>
                ))}
              </Box>
              <Button variant="outline" onClick={switchWorkspace}>
                {copy.switchWorkspace}
              </Button>
            </Flex>
          </Card.Body>
        </Card.Root>
      )}
      <Card.Root borderRadius="xl" borderColor="app.line" shadow="xs">
        <Card.Header>
          <Card.Title>{copy.sessions}</Card.Title>
          <Card.Description>{copy.sessionsDescription}</Card.Description>
        </Card.Header>
        <Card.Body>
          <Stack gap="2">
            {sessions.map((session) => (
              <Flex
                key={session.id}
                justify="space-between"
                align="center"
                gap="3"
              >
                <Text fontSize="sm">
                  {session.current ? copy.thisDevice : copy.otherDevice} ·{" "}
                  {session.tenant_name} ·{" "}
                  {new Date(session.created_at).toLocaleString()}
                </Text>
                {!session.current && (
                  <Button
                    size="xs"
                    variant="outline"
                    onClick={() => revokeSession(session)}
                  >
                    {copy.close}
                  </Button>
                )}
              </Flex>
            ))}
            {!sessions.length && (
              <Text fontSize="sm" color="app.muted">
                {copy.noSessions}
              </Text>
            )}
          </Stack>
          {actionError && (
            <Text mt="3" color="red.500" fontSize="sm">
              {actionError}
            </Text>
          )}
        </Card.Body>
      </Card.Root>
    </Stack>
  );
}

function NotificationPreferencesHub() {
  const [language, setLanguage] = useState(window.tenantI18n?.language || "az");
  useEffect(() => {const relabel=()=>setLanguage(window.tenantI18n?.language||"az");document.addEventListener("tenant-language",relabel);return()=>document.removeEventListener("tenant-language",relabel);},[]);
  const copy = ({ru:{title:"Уведомления",description:"Выберите, где получать уведомления о событиях.",internal:"В кабинете",save:"Сохранить уведомления",retry:"Повторить",empty:"Для вашей роли нет доступных уведомлений.",loadError:"Не удалось загрузить настройки уведомлений.",saveError:"Не удалось сохранить настройки уведомлений."},en:{title:"Notifications",description:"Choose where to receive event notifications.",internal:"In app",save:"Save notifications",retry:"Retry",empty:"No notifications are available for your role.",loadError:"Could not load notification settings.",saveError:"Could not save notification settings."},az:{title:"Bildirişlər",description:"Hadisələr barədə bildirişləri harada alacağınızı seçin.",internal:"Tətbiq daxilində",save:"Bildirişləri saxla",retry:"Yenidən yoxla",empty:"Bu rola uyğun bildiriş yoxdur.",loadError:"Bildiriş ayarları yüklənmədi.",saveError:"Bildiriş ayarları saxlanmadı."}})[language] || {title:"Notifications",description:"",internal:"In app",save:"Save",retry:"Retry",empty:"No notifications",loadError:"Could not load",saveError:"Could not save"};
  const eventNames = {ru:{deal_completion_requested:"Сделка ожидает подтверждения",new_lead:"Новая сделка",incoming_message:"Новое сообщение",task_assigned:"Новая задача",task_overdue:"Просроченная задача",task_approval_requested:"Задача ожидает подтверждения создания",task_completion_requested:"Задача ожидает подтверждения выполнения",task_approval_decided:"Результат подтверждения задачи",hot_order_available:"Новый заказ",hot_order_completion_requested:"Заказ ожидает подтверждения выполнения",hot_order_completion_decided:"Результат подтверждения заказа",linear_done:"Задача Linear завершена",linear_status_changed:"Статус задачи Linear изменён",proactive_risk:"Требуется проверка риска",ai_developer_consultation:"Ответ по консультации разработчика"},en:{deal_completion_requested:"Deal awaiting approval",new_lead:"New deal",incoming_message:"New message",task_assigned:"New task",task_overdue:"Overdue task",task_approval_requested:"Task creation awaiting approval",task_completion_requested:"Task completion awaiting approval",task_approval_decided:"Task approval result",hot_order_available:"New order",hot_order_completion_requested:"Order completion awaiting approval",hot_order_completion_decided:"Order approval result",linear_done:"Linear task completed",linear_status_changed:"Linear task status changed",proactive_risk:"Risk review required",ai_developer_consultation:"Developer consultation reply"}};
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(false);
  const requestGeneration = useRef(0);
  const load = async () => {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError("");
    try {
      const result = await api("/api/platform/notification-preferences");
      if (!mounted.current || generation !== requestGeneration.current) return;
      setData(result);
      window.tenantViewDemand?.clean("#chakra-notification-settings-root");
    } catch (e) {
      if (!mounted.current || generation !== requestGeneration.current) return;
      setError(e.message || copy.loadError);
    } finally {
      if (mounted.current && generation === requestGeneration.current) setLoading(false);
    }
  };
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      requestGeneration.current += 1;
    };
  }, []);
  const save = async () => {
    if (!data || saving) return;
    const generation = ++requestGeneration.current;
    setSaving(true);
    setError("");
    try {
      const result = await api("/api/platform/notification-preferences", {
        method: "POST",
        body: JSON.stringify({ preferences: data.preferences || {} }),
      });
      if (!mounted.current || generation !== requestGeneration.current) return;
      setData(result);
      window.tenantViewDemand?.clean("#chakra-notification-settings-root");
    } catch (e) {
      if (!mounted.current || generation !== requestGeneration.current) return;
      setError(e.message || copy.saveError);
    } finally {
      if (mounted.current && generation === requestGeneration.current) setSaving(false);
    }
  };
  return (
    <Card.Root className="notification-preferences-card" minW="0" maxW="100%" borderRadius="xl" borderColor="app.line" shadow="xs">
      <Card.Header>
        <Card.Title>{copy.title}</Card.Title>
        <Card.Description>
          {copy.description}
        </Card.Description>
      </Card.Header>
      <Card.Body>
        {loading ? (
          <Flex py="6" justify="center">
            <Spinner color="brand.500" />
          </Flex>
        ) : (
          <Stack gap="3">
            {(data?.events || []).map((event) => (
              <Flex
                key={event.key}
                className="notification-preference-row"
                minW="0"
                justify="space-between"
                align="center"
                gap="3"
                wrap="wrap"
              >
                <Text className="notification-event-label" fontSize="sm">{eventNames[language]?.[event.key] || event.label}</Text>
                <Flex className="notification-channels" gap="4" wrap="wrap" minW="0" maxW="100%">
                  {(data?.channels_available || [])
                    .filter((channel) =>
                      (event.channels || data?.channels_available || []).includes(channel),
                    )
                    .map((channel) => (
                      <PermissionCheckbox
                        key={`${event.key}:${channel}`}
                        value={channel}
                        label={
                          channel === "push"
                            ? "Web Push"
                            : channel === "internal"
                              ? copy.internal
                              : "Telegram"
                        }
                        checked={data?.preferences?.[event.key]?.[channel] === true}
                        disabled={saving}
                        onChange={(key, checked) =>
                          setData((previous) => ({
                            ...previous,
                            preferences: {
                              ...(previous?.preferences || {}),
                              [event.key]: {
                                ...(previous?.preferences?.[event.key] || {}),
                                [key]: checked,
                              },
                            },
                          }))
                        }
                      />
                    ))}
                </Flex>
              </Flex>
            ))}
            {!data?.events?.length && (
              <Text fontSize="sm" color="app.muted">
                {copy.empty}
              </Text>
            )}
            {error && (
              <Text role="alert" color="red.500" fontSize="sm">
                {error}
              </Text>
            )}
            <Flex gap="2" align="center" wrap="wrap">
              <Button
                alignSelf="start"
                colorPalette="brand"
                onClick={save}
                loading={saving}
                disabled={!data || saving}
              >
                {copy.save}
              </Button>
              {error && (
                <Button variant="outline" onClick={() => void load()} disabled={loading || saving}>
                  {copy.retry}
                </Button>
              )}
            </Flex>
          </Stack>
        )}
      </Card.Body>
    </Card.Root>
  );
}

window.tenantChakraCRM = {
  renderNativeDeals: (rows, settings = {}) => mount("nativeDealsList", <NativeDealBoard rows={rows} settings={settings} />),
  renderTasks: (rows, settings = {}) => mount(settings.target || "tasksList", <TasksView rows={rows} settings={settings} />),
  renderDeals: (target, rows, customer, onOpen) =>
    mount(
      target,
      <DealsView rows={rows} customer={customer} onOpen={onOpen} />,
    ),
};
mount("aiDashboard", <AIDashboard />);
for (const [view, host, component] of [
  ["profile", "chakra-notification-settings-root", <NotificationPreferencesHub />],
  ["profile", "chakra-settings-root", <SettingsHub />],
  ["integrations", "chakra-integrations-root", <IntegrationMarketplace />],
]) {
  window.tenantViewDemand.watch({
    view, host, editable: host === "chakra-notification-settings-root" ? "#chakra-notification-settings-root" : host === "chakra-settings-root" ? "#chakra-settings-root" : "#integrationMarketplaceDialog",
    excludeEditable: ".integration-modal-panel", preserveProviderDrafts: view === "integrations",
    activate: () => mount(host, component),
    reset: () => { roots.get(host)?.unmount(); roots.delete(host); },
  });
}
document.documentElement.dataset.chakraUi = "ready";
document.dispatchEvent(new CustomEvent("tenant-chakra-ready"));
