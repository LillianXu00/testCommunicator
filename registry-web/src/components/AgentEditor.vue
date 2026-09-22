<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { CheckCircle2, ChevronDown, Plus, Save, ShieldCheck, Trash2, X } from 'lucide-vue-next'
import type { AdapterDefinition, AgentDefinition, AgentRecord, SkillDefinition } from '../types'

const props = defineProps<{
  agent?: AgentRecord | null
  adapters: AdapterDefinition[]
  managedSecrets: boolean
}>()

const emit = defineEmits<{
  close: []
  save: [definition: AgentDefinition, editingId?: string]
  inspect: [url: string]
}>()

const saving = ref(false)
const cardVerified = ref(false)
const form = reactive<AgentDefinition>(blank())

function blank(): AgentDefinition {
  return {
    agentId: '', integrationMode: 'managed-adapter', status: 'ACTIVE',
    name: '', description: '', version: '1.0.0',
    skills: [{ id: '', name: '', description: '', examples: [] }],
    inputModes: ['text/plain'], outputModes: ['text/plain'],
    capabilities: { pushNotifications: true, streaming: false },
    backend: {
      adapterType: 'declarative-http', endpoint: '', timeoutSeconds: 600,
      requestBody: { messages: [{ role: 'user', content: '{{input}}' }], stream: false },
      responseTextSelectors: ['$.choices[0].message.content'],
    },
  }
}

function jsonClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function editableDefinition(agent: AgentRecord): AgentDefinition {
  const defaults = blank()
  const stored = jsonClone(agent.definition || {}) as Partial<AgentDefinition>
  const card = jsonClone(agent.agentCard || {}) as Record<string, any>
  const integrationMode = stored.integrationMode || agent.integrationMode
  const storedSkills = Array.isArray(stored.skills) ? stored.skills : []
  const cardSkills = Array.isArray(card.skills) ? card.skills : []

  const definition: AgentDefinition = {
    ...defaults,
    ...stored,
    agentId: stored.agentId || agent.agentId,
    integrationMode,
    status: stored.status || agent.status,
    name: stored.name || card.name || '',
    description: stored.description || card.description || '',
    version: stored.version || card.version || defaults.version,
    skills: jsonClone(storedSkills.length ? storedSkills : cardSkills),
    inputModes: jsonClone(
      stored.inputModes?.length
        ? stored.inputModes
        : card.defaultInputModes || defaults.inputModes,
    ),
    outputModes: jsonClone(
      stored.outputModes?.length
        ? stored.outputModes
        : card.defaultOutputModes || defaults.outputModes,
    ),
    capabilities: {
      ...defaults.capabilities,
      ...(card.capabilities || {}),
      ...(stored.capabilities || {}),
    },
  }

  if (integrationMode === 'managed-adapter') {
    definition.backend = {
      ...defaults.backend!,
      ...(stored.backend || {}),
      requestBody: jsonClone(
        stored.backend?.requestBody || defaults.backend!.requestBody!,
      ),
      responseTextSelectors: jsonClone(
        stored.backend?.responseTextSelectors
          || defaults.backend!.responseTextSelectors!,
      ),
    }
    delete definition.sourceAgentCardUrl
  } else {
    definition.sourceAgentCardUrl = stored.sourceAgentCardUrl || ''
    delete definition.backend
  }

  if (!definition.skills?.length && integrationMode === 'managed-adapter') {
    definition.skills = [{ id: '', name: '', description: '', examples: [] }]
  }
  return definition
}

function load() {
  const value = props.agent ? editableDefinition(props.agent) : blank()
  Object.keys(form).forEach((key) => delete (form as any)[key])
  Object.assign(form, value)
  if (form.backend) {
    form.backend.authToken = ''
    form.backend.clearAuthSecret = false
  }
  cardVerified.value = false
}

watch(() => props.agent, load, { immediate: true })

const editing = computed(() => Boolean(props.agent))
const selectedAdapter = computed(() => props.adapters.find(item => item.id === form.backend?.adapterType))
const requestJson = computed({
  get: () => JSON.stringify(form.backend?.requestBody || {}, null, 2),
  set: (value: string) => {
    try { if (form.backend) form.backend.requestBody = JSON.parse(value) } catch { /* validated on submit */ }
  },
})
const selectorsText = computed({
  get: () => (form.backend?.responseTextSelectors || []).join('\n'),
  set: (value: string) => {
    if (form.backend) form.backend.responseTextSelectors = value.split('\n').map(item => item.trim()).filter(Boolean)
  },
})

function selectMode(mode: 'native-a2a' | 'managed-adapter') {
  form.integrationMode = mode
  if (mode === 'managed-adapter' && !form.backend) form.backend = blank().backend
}

function selectAdapter(id: string) {
  if (!form.backend) return
  const adapter = props.adapters.find(item => item.id === id)
  const previousAdapter = props.adapters.find(item => item.id === form.backend?.adapterType)
  const inheritedTimeout = !form.backend.timeoutSeconds
    || form.backend.timeoutSeconds === previousAdapter?.defaultTimeoutSeconds
  form.backend.adapterType = id
  if (!form.backend.endpoint && adapter?.defaultEndpoint) form.backend.endpoint = adapter.defaultEndpoint
  if (!form.backend.authEnv && adapter?.defaultAuthEnv) form.backend.authEnv = adapter.defaultAuthEnv
  if (inheritedTimeout) form.backend.timeoutSeconds = adapter?.defaultTimeoutSeconds || 600
  if (adapter?.requiresAgentId && !form.backend.agentId) form.backend.agentId = form.agentId
}

function addSkill() {
  form.skills ||= []
  form.skills.push({ id: '', name: '', description: '', examples: [] })
}

function removeSkill(index: number) {
  if ((form.skills || []).length > 1) form.skills?.splice(index, 1)
}

function onTokenInput() {
  if (!form.backend?.authToken) return
  form.backend.authEnv = ''
  form.backend.clearAuthSecret = false
}

async function submit() {
  saving.value = true
  try {
    const payload = jsonClone(form)
    if (payload.integrationMode === 'native-a2a') {
      delete payload.backend
      delete payload.name
      delete payload.description
      delete payload.version
      delete payload.skills
      delete payload.capabilities
      delete payload.inputModes
      delete payload.outputModes
    } else {
      delete payload.sourceAgentCardUrl
      if (payload.backend?.authToken === '') delete payload.backend.authToken
      if (payload.backend && !payload.backend.clearAuthSecret) {
        delete payload.backend.clearAuthSecret
      }
    }
    emit('save', payload, props.agent?.agentId)
  } finally {
    saving.value = false
  }
}

async function inspect() {
  if (!form.sourceAgentCardUrl) return
  emit('inspect', form.sourceAgentCardUrl)
  cardVerified.value = true
}
</script>

<template>
  <div class="drawer-backdrop editor-backdrop" @click.self="$emit('close')">
    <aside class="editor-drawer">
      <header class="drawer-header editor-heading">
        <div><span class="drawer-kicker">{{ editing ? 'EDIT AGENT' : 'NEW CONNECTION' }}</span><h2>{{ editing ? '编辑 Agent' : '注册新 Agent' }}</h2></div>
        <button class="icon-button" type="button" title="关闭" @click="$emit('close')"><X :size="19" /></button>
      </header>
      <form @submit.prevent="submit">
        <section class="form-section">
          <div class="form-section-title"><span>01</span><div><h3>接入方式</h3><p>选择原生 A2A 服务或由平台托管适配。</p></div></div>
          <div class="mode-picker">
            <button type="button" :class="{ active: form.integrationMode === 'managed-adapter' }" @click="selectMode('managed-adapter')"><ShieldCheck :size="20" /><span><b>平台托管 Adapter</b><small>提供普通 Agent Endpoint</small></span></button>
            <button type="button" :class="{ active: form.integrationMode === 'native-a2a' }" @click="selectMode('native-a2a')"><CheckCircle2 :size="20" /><span><b>原生 A2A</b><small>提供已有 Agent Card URL</small></span></button>
          </div>
        </section>

        <section class="form-section">
          <div class="form-section-title"><span>02</span><div><h3>Agent身份</h3><p>用于Manager发现与稳定路由。</p></div></div>
          <div class="form-grid">
            <label><span>Agent ID</span><input v-model.trim="form.agentId" required pattern="[a-z0-9][a-z0-9._-]{0,127}" :disabled="editing" placeholder="qclaw-insight" /></label>
            <label class="toggle-field"><span><b>启用发现</b><small>允许Manager发现和调用</small></span><input v-model="form.status" type="checkbox" true-value="ACTIVE" false-value="DISABLED" /></label>
          </div>
        </section>

        <template v-if="form.integrationMode === 'native-a2a'">
          <section class="form-section">
            <div class="form-section-title"><span>03</span><div><h3>Agent Card</h3><p>Registry会读取并校验标准A2A Card。</p></div></div>
            <div class="inline-action-field"><input v-model.trim="form.sourceAgentCardUrl" type="url" required placeholder="https://agent.example.com/.well-known/agent-card.json" /><button class="button secondary" type="button" @click="inspect">检查Card</button></div>
            <p v-if="cardVerified" class="verified-message"><CheckCircle2 :size="15" />已提交检查，保存时会再次校验</p>
          </section>
        </template>

        <template v-else>
          <section class="form-section">
            <div class="form-section-title"><span>03</span><div><h3>能力描述</h3><p>Manager据此判断是否委派任务。</p></div></div>
            <div class="form-grid">
              <label><span>显示名称</span><input v-model.trim="form.name" required placeholder="QClaw Insight" /></label>
              <label><span>版本</span><input v-model.trim="form.version" required placeholder="1.0.0" /></label>
              <label class="wide"><span>职责描述</span><textarea v-model.trim="form.description" required rows="3" placeholder="清晰说明该Agent最适合处理什么任务"></textarea></label>
            </div>
            <div class="skills-editor">
              <div class="subheading"><h4>Skills</h4><button class="text-button" type="button" @click="addSkill"><Plus :size="15" />添加能力</button></div>
              <div v-for="(skill, index) in form.skills" :key="index" class="skill-editor-row">
                <input v-model.trim="skill.id" required placeholder="skill-id" />
                <input v-model.trim="skill.name" required placeholder="能力名称" />
                <input v-model.trim="skill.description" required placeholder="何时应该调用这个能力" />
                <button class="icon-button danger" type="button" title="删除能力" @click="removeSkill(index)"><Trash2 :size="16" /></button>
              </div>
            </div>
          </section>

          <section class="form-section">
            <div class="form-section-title"><span>04</span><div><h3>Adapter配置</h3><p>选择已审核的协议转换器并配置后端服务。</p></div></div>
            <div class="adapter-picker">
              <button v-for="adapter in adapters" :key="adapter.id" type="button" :class="{ active: form.backend?.adapterType === adapter.id }" @click="selectAdapter(adapter.id)"><b>{{ adapter.name }}</b><small>{{ adapter.bestFor }}</small></button>
            </div>
            <div class="adapter-note" v-if="selectedAdapter"><strong>{{ selectedAdapter.name }}</strong><p>{{ selectedAdapter.description }}</p></div>
            <div class="form-grid">
              <label class="wide"><span>Agent Endpoint</span><input v-model.trim="form.backend!.endpoint" type="url" required placeholder="http://172.20.10.3:57101/v1/chat/completions" /></label>
              <label v-if="selectedAdapter?.requiresAgentId"><span>OpenClaw Agent ID</span><input v-model.trim="form.backend!.agentId" required placeholder="planner" /></label>
              <label><span>执行超时（秒）</span><input v-model.number="form.backend!.timeoutSeconds" type="number" min="1" max="86400" /></label>
              <label><span>认证环境变量</span><input v-model.trim="form.backend!.authEnv" placeholder="可选：AGENT_API_TOKEN" @input="form.backend!.authToken = ''" /></label>
              <label v-if="managedSecrets"><span>Bearer Token（写入后不可查看）</span><input v-model="form.backend!.authToken" type="password" autocomplete="new-password" :placeholder="form.backend?.secretRef ? '已托管，留空保持原值' : '可选：输入真实Token'" @input="onTokenInput" /></label>
              <label v-if="managedSecrets && form.backend?.secretRef" class="toggle-field"><span><b>移除托管Token</b><small>保存后立即生效</small></span><input v-model="form.backend!.clearAuthSecret" type="checkbox" /></label>
              <template v-if="form.backend?.adapterType === 'declarative-http'">
                <label class="wide"><span>请求体模板</span><textarea v-model="requestJson" rows="8" spellcheck="false"></textarea></label>
                <label class="wide"><span>响应文本选择器（每行一个）</span><textarea v-model="selectorsText" rows="4" spellcheck="false"></textarea></label>
              </template>
            </div>
          </section>

          <section class="form-section compact-section">
            <div class="form-section-title"><span>05</span><div><h3>协议能力</h3><p>声明Gateway对外暴露的A2A能力。</p></div></div>
            <div class="toggle-grid">
              <label class="toggle-field"><span><b>Push Notifications</b><small>异步推送任务完成事件</small></span><input v-model="form.capabilities!.pushNotifications" type="checkbox" /></label>
              <label class="toggle-field"><span><b>Streaming</b><small>允许流式任务事件</small></span><input v-model="form.capabilities!.streaming" type="checkbox" /></label>
            </div>
          </section>
        </template>

        <footer class="editor-footer"><button class="button secondary" type="button" @click="$emit('close')">取消</button><button class="button primary" type="submit" :disabled="saving"><Save :size="16" />{{ editing ? '保存修改' : '注册 Agent' }}</button></footer>
      </form>
    </aside>
  </div>
</template>
