// Small, presentation-only building blocks shared by the /app React mounts.
import { Badge, Box, Flex, Text } from "@chakra-ui/react"

export const roles = {
  owner: "Şirkət sahibi", admin: "Administrator", manager: "Vərəq istifadəçisi",
  worker: "İcraçı", master: "Usta",
}

export const permissionLabels = {
  deals:"Sövdələşmələr", tasks:"Tapşırıqlar", customers:"Müştərilər",
  hot_orders:"İsti sifarişlər", finance:"Maliyyə", linear:"Linear",
  settings:"Tənzimləmələr", employees:"Əməkdaşlar", integrations:"İnteqrasiyalar",
}

export const integrationCatalog = [
  { id:"kommo", name:"Kommo", category:"CRM", mark:"K", color:"#635BFF", description:"Sövdələşmələri, müştəriləri, tapşırıqları və mesaj tarixçəsini sinxronlaşdırır.", features:["Sövdələşmə və mərhələlər", "Tapşırıqlar", "Mesaj kanalları"], action:"setup" },
  { id:"linear", name:"Linear", category:"İş idarəetməsi", mark:"L", color:"#5E6AD2", description:"Məhsul tapşırıqlarını, təsdiq axınını və yeniliklərin hazırlanmasını idarə edir.", features:["Kanban və statuslar", "Tapşırıq bildirişləri", "Yenilik axını"], section:"integrations" },
  { id:"openai", name:"OpenAI", category:"Süni intellekt", mark:"AI", color:"#111827", description:"Chat cavabları, xülasə, transkripsiya və mətn redaktəsi üçün şirkət AI bağlantısıdır.", features:["Cavab qaralaması", "Səs transkripsiyası", "Xülasə və redaktə"], section:"ai" },
  { id:"telegram", name:"Telegram", category:"Rabitə", mark:"T", color:"#229ED9", description:"Əməkdaş girişləri, bildirişlər və təsdiqlənmiş yeniliklərin paylaşılması üçün istifadə olunur.", features:["Təhlükəsiz giriş", "Şəxsi bildirişlər", "Kanal nəşrləri"], section:"notifications" },
  { id:"wappi", name:"Wappi", category:"Rabitə", mark:"W", color:"#25D366", description:"WhatsApp kanalları üçün planlaşdırılan əlavə bağlantı.", features:["WhatsApp mesajları", "Kanal uyğunlaşdırması"], pending:true },
  { id:"beinsystems", name:"BeinSystems ERP", category:"CRM / ERP", mark:"B", color:"#0A926E", description:"BEIN ERP daxilində sövdələşmə, müştəri, tapşırıq, vərəq və mərhələ məlumatlarını şirkət üzrə qoşur.", features:["Sövdələşmə və müştərilər", "Tapşırıqlar", "Vərəq və mərhələlər"] },
]

export const formatDate = value => {
  if (!value) return "Tarix yoxdur"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? "Tarix yoxdur" : date.toLocaleString("en-GB", {
    day:"2-digit", month:"2-digit", year:"numeric", hour:"2-digit", minute:"2-digit",
  }).replace(",", "")
}

export const api = async (url, options={}) => {
  const response = await fetch(url, {
    credentials:"same-origin", cache:"no-store", ...options,
    headers:{ "Content-Type":"application/json", ...(options.headers || {}) },
  })
  const result = await response.json().catch(() => ({ success:false, error:"Server cavabı oxunmadı." }))
  if (!response.ok || !result.success) throw new Error(result.error || "Sorğu alınmadı.")
  return result
}

export function Empty({ children }) {
  return <Box py="14" px="5" textAlign="center" color="app.muted" bg="app.surface" borderWidth="1px" borderColor="app.line" borderRadius="xl">{children}</Box>
}

export function StatusBadge({ children, tone="green" }) {
  return <Badge colorPalette={tone} variant="subtle" borderRadius="full" px="2.5" py="1" fontWeight="700">{children}</Badge>
}

export function IntegrationLogo({ provider, size="md" }) {
  const item=integrationCatalog.find(row=>row.id===provider)||{}
  const boxSize=size==="lg"?"64px":"52px"
  const iconSize=size==="lg"?30:25
  const common={width:iconSize,height:iconSize,viewBox:"0 0 24 24",fill:"none",xmlns:"http://www.w3.org/2000/svg"}
  let logo=null
  if(provider==="telegram")logo=<svg {...common}><path d="M21.4 3.4 18.2 19c-.24 1.1-.88 1.37-1.78.85l-4.9-3.61-2.36 2.27c-.26.26-.48.48-.98.48l.35-5 9.1-8.23c.4-.35-.08-.55-.61-.2L5.78 12.64.94 11.13c-1.05-.33-1.07-1.05.22-1.56L20.08 2.3c.88-.32 1.65.2 1.32 1.1Z" fill="currentColor"/></svg>
  else if(provider==="linear")logo=<svg {...common}><path d="M4.3 4.3a10.9 10.9 0 0 0-1.9 2.75l14.55 14.54a10.9 10.9 0 0 0 2.75-1.89L4.3 4.3Zm-2.65 4.8a11 11 0 0 0-.5 3.33l10.42 10.42a11 11 0 0 0 3.33-.5L1.65 9.1Zm-.23 6.38a11.02 11.02 0 0 0 7.1 7.1l-7.1-7.1ZM6.35 2.65l15 15a11 11 0 0 0-15-15Z" fill="currentColor"/></svg>
  else if(provider==="openai")logo=<svg {...common} viewBox="0 0 32 32"><path d="M15.9 3.8a7 7 0 0 1 11 5.8 7 7 0 0 1 1 12.3 7 7 0 0 1-11 5.8 7 7 0 0 1-11-5.8 7 7 0 0 1-1-12.3 7 7 0 0 1 11-5.8Zm0 4-5.8 3.4v6.7l5.8 3.4 5.8-3.4v-6.7l-5.8-3.4Zm0 3.2 3 1.8v3.5l-3 1.8-3-1.8v-3.5l3-1.8Z" fill="currentColor"/></svg>
  else if(provider==="wappi")logo=<svg {...common}><path d="M12 2a9.5 9.5 0 0 0-8.2 14.3L2.4 21.6l5.4-1.4A9.5 9.5 0 1 0 12 2Zm5.3 13.6c-.22.62-1.3 1.18-1.8 1.25-.48.08-1.1.1-1.77-.11-.4-.13-.93-.3-1.6-.59-2.82-1.22-4.66-4.07-4.8-4.25-.14-.19-1.15-1.53-1.15-2.91 0-1.39.73-2.07.99-2.35.25-.28.56-.35.74-.35h.54c.17 0 .4-.07.63.48.23.56.8 1.94.87 2.08.07.14.12.3.02.49-.09.18-.14.3-.28.46-.14.16-.3.35-.42.47-.14.14-.29.3-.13.58.16.28.71 1.17 1.53 1.9 1.05.94 1.94 1.23 2.22 1.37.28.14.44.12.6-.07.16-.18.7-.81.88-1.09.19-.28.37-.23.63-.14.25.1 1.62.77 1.9.9.28.15.46.22.53.33.07.12.07.65-.16 1.27Z" fill="currentColor"/></svg>
  else if(provider==="kommo")logo=<Text as="span" fontSize={size==="lg"?"lg":"md"} fontWeight="900" letterSpacing="-.8px" color="inherit">kommo</Text>
  else logo=<Text as="span" fontSize={size==="lg"?"lg":"md"} fontWeight="900" letterSpacing="-.5px" color="inherit">BEIN</Text>
  return <Flex data-integration-logo={provider} w={boxSize} h={boxSize} flex="none" borderRadius="14px" bg={`${item.color}12`} color={item.color} borderWidth="1px" borderColor={`${item.color}26`} align="center" justify="center">{logo}</Flex>
}
