<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Activity, Bot, CircleDot, KeyRound, LayoutGrid, Network, Plus, Puzzle,
  RefreshCw, Search, ServerCog, ShieldCheck, TriangleAlert, Wifi,
} from 'lucide-vue-next'
import AgentCard from './components/AgentCard.vue'
import AgentDetailDrawer from './components/AgentDetailDrawer.vue'
import AgentEditor from './components/AgentEditor.vue'
import AdapterRequestDialog from './components/AdapterRequestDialog.vue'
import TokenDialog from './components/TokenDialog.vue'
import {
  deleteAgent, getAdminToken, inspectCard, loadAdapters, loadAgent, loadAgents,
  loadStatuses, saveAgent, setAdminToken, streamTaskEvents, submitAdapterRequest,
} from './api'
import type { AdapterDefinition, AgentDefinition, AgentRecord, RuntimeStatus } from './types'

const agents = ref<AgentRecord[]>([])
const statuses = ref<Record<string, RuntimeStatus>>({})
const adapters = ref<AdapterDefinition[]>([])
const selectedAgent = ref<AgentRecord | null>(null)
const editingAgent = ref<AgentRecord | null | undefined>(undefined)
const loading = ref(true)
const refreshing = ref(false)
const query = ref('')
const filter = ref('all')
const errorMessage = ref('')
const toast = ref('')
const showTokenDialog = ref(false)
const showAdapterRequest = ref(false)
const managedSecrets = ref(false)
let pollTimer: number | undefined
let toastTimer: number | undefined
let eventController: AbortController | undefined
let eventReconnectTimer: number | undefined
let statusRefreshPending = false

const fallbackStatus = (agent: AgentRecord): RuntimeStatus => ({
  agentId: agent.agentId,
  availability: agent.status === 'DISABLED' ? 'DISABLED' : 'UNKNOWN',
  activity: 'IDLE', checkedAt: new Date().toISOString(), latencyMs: null,
  detail: '等待Registry探测', endpoint: agent.definition.backend?.endpoint || null,
  activeTaskCount: 0, workingTaskCount: 0, waitingTaskCount: 0,
  currentTask: null, recentTasks: [],
})

function statusFor(agent: AgentRecord) {
  return statuses.value[agent.agentId] || fallbackStatus(agent)
}

const counts = computed(() => ({
  total: agents.value.length,
  online: agents.value.filter(a => statusFor(a).availability === 'ONLINE').length,
  offline: agents.value.filter(a => ['OFFLINE', 'DEGRADED'].includes(statusFor(a).availability)).length,
  managed: agents.value.filter(a => a.integrationMode === 'managed-adapter').length,
  busy: agents.value.filter(a => statusFor(a).activity !== 'IDLE').length,
  activeTasks: agents.value.reduce((sum, agent) => sum + statusFor(agent).activeTaskCount, 0),
}))

const filteredAgents = computed(() => {
  const term = query.value.trim().toLowerCase()
  return agents.value.filter(agent => {
    const status = statusFor(agent)
    if (filter.value === 'online' && status.availability !== 'ONLINE') return false
    if (filter.value === 'issues' && !['OFFLINE', 'DEGRADED', 'UNKNOWN'].includes(status.availability)) return false
    if (filter.value === 'managed' && agent.integrationMode !== 'managed-adapter') return false
    if (filter.value === 'native' && agent.integrationMode !== 'native-a2a') return false
    if (!term) return true
    const text = [agent.agentId, agent.definition.name, agent.definition.description,
      ...(agent.definition.skills || []).flatMap(skill => [skill.name, skill.description])].join(' ').toLowerCase()
    return text.includes(term)
  })
})

const filters = computed(() => [
  { id: 'all', label: '全部 Agent', count: counts.value.total, icon: LayoutGrid },
  { id: 'online', label: '在线', count: counts.value.online, icon: Wifi },
  { id: 'issues', label: '需要关注', count: counts.value.offline, icon: TriangleAlert },
  { id: 'managed', label: '托管 Adapter', count: counts.value.managed, icon: ServerCog },
  { id: 'native', label: '原生 A2A', count: counts.value.total - counts.value.managed, icon: CircleDot },
])

function notify(message: string) {
  toast.value = message
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 3200)
}

async function bootstrap() {
  loading.value = true
  errorMessage.value = ''
  try {
    const health = await fetch('/health').then(res => res.json())
    managedSecrets.value = health.capabilities?.managedSecrets === true
    adapters.value = await loadAdapters()
    await refreshData(false)
  } catch (error: any) {
    if (error.status === 401) showTokenDialog.value = true
    else errorMessage.value = error.message || 'Registry连接失败'
  } finally {
    loading.value = false
  }
}

async function refreshData(force: boolean) {
  if (force) refreshing.value = true
  try {
    const [agentList, statusList] = await Promise.all([loadAgents(), loadStatuses(force)])
    agents.value = agentList
    statuses.value = Object.fromEntries(statusList.map(status => [status.agentId, status]))
    if (selectedAgent.value) selectedAgent.value = agentList.find(a => a.agentId === selectedAgent.value?.agentId) || null
    errorMessage.value = ''
  } catch (error: any) {
    if (error.status === 401) showTokenDialog.value = true
    else errorMessage.value = error.message || '刷新失败'
  } finally {
    refreshing.value = false
  }
}

async function refreshRuntimeStatuses() {
  if (statusRefreshPending) return
  statusRefreshPending = true
  try {
    const statusList = await loadStatuses(false)
    statuses.value = Object.fromEntries(statusList.map(status => [status.agentId, status]))
  } catch (error: any) {
    if (error.status === 401) showTokenDialog.value = true
  } finally {
    statusRefreshPending = false
  }
}

function connectTaskEventStream() {
  eventController?.abort()
  eventController = new AbortController()
  streamTaskEvents(() => { void refreshRuntimeStatuses() }, eventController.signal)
    .catch((error: any) => {
      if (eventController?.signal.aborted) return
      if (error.status === 401) showTokenDialog.value = true
      window.clearTimeout(eventReconnectTimer)
      eventReconnectTimer = window.setTimeout(connectTaskEventStream, 3000)
    })
}

function openCreate() {
  selectedAgent.value = null
  editingAgent.value = null
}

async function openEdit(agent: AgentRecord) {
  selectedAgent.value = null
  editingAgent.value = undefined
  try {
    editingAgent.value = await loadAgent(agent.agentId)
  } catch (error: any) {
    selectedAgent.value = agent
    notify(`加载 Agent 配置失败：${error.message}`)
  }
}

async function handleSave(definition: AgentDefinition, editingId?: string) {
  try {
    const result = await saveAgent(definition, editingId)
    editingAgent.value = undefined
    await refreshData(true)
    selectedAgent.value = agents.value.find(a => a.agentId === result.agentId) || null
    notify(`${result.agentId} 已保存`)
  } catch (error: any) {
    notify(`保存失败：${error.message}`)
  }
}

async function handleDelete(agent: AgentRecord) {
  if (!window.confirm(`确定删除 ${agent.definition.name || agent.agentId}？此操作不能撤销。`)) return
  try {
    await deleteAgent(agent.agentId)
    selectedAgent.value = null
    await refreshData(true)
    notify(`${agent.agentId} 已删除`)
  } catch (error: any) { notify(`删除失败：${error.message}`) }
}

async function handleInspect(url: string) {
  try {
    const result = await inspectCard(url)
    notify(`Agent Card可用：${result.agentCard.name}`)
  } catch (error: any) { notify(`Card校验失败：${error.message}`) }
}

async function handleAdapterRequest(payload: Record<string, string>) {
  try {
    const result = await submitAdapterRequest(payload)
    showAdapterRequest.value = false
    notify(`Adapter申请已提交：${result.requestId}`)
  } catch (error: any) { notify(`提交失败：${error.message}`) }
}

function applyAdminToken(token: string) {
  setAdminToken(token)
  showTokenDialog.value = false
  bootstrap()
  connectTaskEventStream()
}

onMounted(() => {
  bootstrap()
  connectTaskEventStream()
  pollTimer = window.setInterval(() => refreshData(false), 30000)
})
onBeforeUnmount(() => {
  eventController?.abort()
  window.clearTimeout(eventReconnectTimer)
  window.clearInterval(pollTimer)
  window.clearTimeout(toastTimer)
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark"><Network :size="20" /></div><div><strong>Agent Center</strong><span>A2A REGISTRY</span></div></div>
      <nav class="main-nav"><button class="active"><LayoutGrid :size="17" />运行大厅</button><button @click="showAdapterRequest = true"><Puzzle :size="17" />Adapter申请</button></nav>
      <div class="nav-section"><span>视图</span><button v-for="item in filters" :key="item.id" :class="{ active: filter === item.id }" @click="filter = item.id"><component :is="item.icon" :size="16" />{{ item.label }}<b>{{ item.count }}</b></button></div>
      <div class="sidebar-footer"><div class="registry-health"><i></i><div><strong>Registry运行中</strong><span>任务事件实时推送</span></div></div><button class="icon-button dark" title="管理令牌" @click="showTokenDialog = true"><KeyRound :size="17" /></button></div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div><span class="page-kicker">LIVE OPERATIONS</span><h1>Agent运行大厅</h1><p>发现、观察并管理接入A2A网络的智能体。</p></div>
        <div class="topbar-actions"><button class="icon-button" title="刷新状态" :class="{ spinning: refreshing }" @click="refreshData(true)"><RefreshCw :size="18" /></button><button class="button primary" @click="openCreate"><Plus :size="17" />注册Agent</button></div>
      </header>

      <section class="overview-strip">
        <div><span>已注册</span><strong>{{ counts.total }}</strong><small>个Agent</small></div>
        <div class="online-metric"><span>当前在线</span><strong>{{ counts.online }}</strong><small>{{ counts.total ? Math.round(counts.online / counts.total * 100) : 0 }}%可用</small></div>
        <div class="issue-metric"><span>需要关注</span><strong>{{ counts.offline }}</strong><small>离线或异常</small></div>
        <div class="busy-metric"><span>正在执行</span><strong>{{ counts.activeTasks }}</strong><small>{{ counts.busy }}个Agent忙碌</small></div>
      </section>

      <div class="content-toolbar"><div><h2>{{ filters.find(item => item.id === filter)?.label }}</h2><span>{{ filteredAgents.length }}个结果</span></div><label class="search-box"><Search :size="17" /><input v-model="query" placeholder="搜索名称、ID或能力" /></label></div>
      <div v-if="errorMessage" class="error-banner"><TriangleAlert :size="17" />{{ errorMessage }}<button @click="bootstrap">重试</button></div>
      <section v-if="loading" class="agent-grid loading-grid"><div v-for="i in 3" :key="i" class="agent-skeleton"></div></section>
      <section v-else-if="filteredAgents.length" class="agent-grid"><AgentCard v-for="agent in filteredAgents" :key="agent.agentId" :agent="agent" :status="statusFor(agent)" @select="selectedAgent = $event" /></section>
      <section v-else class="empty-state"><div><Bot :size="34" /></div><h2>没有匹配的Agent</h2><p>调整筛选条件，或注册一个新的Agent。</p><button class="button primary" @click="openCreate"><Plus :size="16" />注册Agent</button></section>
    </main>

    <AgentDetailDrawer v-if="selectedAgent" :agent="selectedAgent" :status="statusFor(selectedAgent)" @close="selectedAgent = null" @edit="openEdit" @remove="handleDelete" />
    <AgentEditor v-if="editingAgent !== undefined" :agent="editingAgent" :adapters="adapters" :managed-secrets="managedSecrets" @close="editingAgent = undefined" @save="handleSave" @inspect="handleInspect" />
    <TokenDialog v-if="showTokenDialog" @close="showTokenDialog = false" @save="applyAdminToken" />
    <AdapterRequestDialog v-if="showAdapterRequest" @close="showAdapterRequest = false" @submit="handleAdapterRequest" />
    <Transition name="toast"><div v-if="toast" class="toast"><ShieldCheck :size="17" />{{ toast }}</div></Transition>
  </div>
</template>
