<script setup lang="ts">
import {
  Activity, CheckCircle2, ChevronDown, ChevronRight, CircleDot,
  ClipboardCopy, ExternalLink, FileJson, KeyRound, LoaderCircle, Pencil,
  Server, Trash2, TriangleAlert, X,
} from 'lucide-vue-next'
import { ref } from 'vue'
import AgentAvatar from './AgentAvatar.vue'
import { loadTaskEvents } from '../api'
import type { AgentRecord, RuntimeStatus, TaskEvent } from '../types'

const props = defineProps<{
  agent: AgentRecord
  status: RuntimeStatus
}>()

const expandedTask = ref('')
const eventCache = ref<Record<string, TaskEvent[]>>({})
const loadingTask = ref('')

defineEmits<{
  close: []
  edit: [agent: AgentRecord]
  remove: [agent: AgentRecord]
}>()

function copy(value: string) {
  navigator.clipboard.writeText(value)
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value))
}

const stateLabel: Record<string, string> = {
  TASK_STATE_SUBMITTED: '已提交', TASK_STATE_WORKING: '执行中',
  TASK_STATE_INPUT_REQUIRED: '等待输入', TASK_STATE_AUTH_REQUIRED: '等待认证',
  TASK_STATE_COMPLETED: '已完成', TASK_STATE_FAILED: '失败',
  TASK_STATE_CANCELED: '已取消', TASK_STATE_REJECTED: '已拒绝',
  TASK_STATE_UNSPECIFIED: '未知',
}

function stateTone(state: string) {
  if (state === 'TASK_STATE_COMPLETED') return 'success'
  if (['TASK_STATE_FAILED', 'TASK_STATE_REJECTED', 'TASK_STATE_CANCELED'].includes(state)) return 'danger'
  if (['TASK_STATE_INPUT_REQUIRED', 'TASK_STATE_AUTH_REQUIRED'].includes(state)) return 'warning'
  if (state === 'TASK_STATE_WORKING') return 'working'
  return 'neutral'
}

async function toggleTask(taskId: string) {
  if (expandedTask.value === taskId) {
    expandedTask.value = ''
    return
  }
  expandedTask.value = taskId
  if (eventCache.value[taskId]) return
  loadingTask.value = taskId
  try {
    eventCache.value[taskId] = await loadTaskEvents(props.agent.agentId, taskId)
  } finally {
    loadingTask.value = ''
  }
}
</script>

<template>
  <div class="drawer-backdrop" @click.self="$emit('close')">
    <aside class="detail-drawer">
      <header class="drawer-header">
        <span class="drawer-kicker">AGENT PROFILE</span>
        <button class="icon-button" type="button" title="关闭" @click="$emit('close')"><X :size="19" /></button>
      </header>
      <div class="profile-hero">
        <AgentAvatar
          :agent-id="agent.agentId"
          :profile="`${agent.definition.name || ''} ${agent.definition.description || ''}`"
          :skills="agent.definition.skills"
          :availability="status.availability"
          :activity="status.activity"
        />
        <div>
          <span class="availability large" :class="status.availability.toLowerCase()"><i></i>{{ status.availability }}</span>
          <h2>{{ agent.definition.name || agent.agentCard.name }}</h2>
          <code>{{ agent.agentId }}</code>
        </div>
      </div>
      <p class="profile-description">{{ agent.definition.description || agent.agentCard.description }}</p>

      <section class="detail-section status-panel">
        <div class="section-heading"><Activity :size="16" /><h3>实时状态</h3></div>
        <div class="metric-grid">
          <div><span>可用性</span><strong>{{ status.availability }}</strong></div>
          <div><span>活动</span><strong>{{ status.activity }}</strong></div>
          <div><span>网络延迟</span><strong>{{ status.latencyMs == null ? '—' : `${status.latencyMs} ms` }}</strong></div>
          <div><span>最近探测</span><strong>{{ formatDate(status.checkedAt) }}</strong></div>
        </div>
        <p class="status-detail">{{ status.detail }}</p>
      </section>

      <section class="detail-section">
        <div class="section-heading">
          <CircleDot :size="16" /><h3>任务活动</h3>
          <span class="section-count">{{ status.activeTaskCount }}个进行中</span>
        </div>
        <div v-if="status.recentTasks.length" class="task-list">
          <article v-for="task in status.recentTasks" :key="task.taskId" class="task-item" :class="stateTone(task.state)">
            <button type="button" class="task-summary" @click="toggleTask(task.taskId)">
              <component :is="expandedTask === task.taskId ? ChevronDown : ChevronRight" :size="15" />
              <span class="task-state-icon">
                <CheckCircle2 v-if="task.state === 'TASK_STATE_COMPLETED'" :size="15" />
                <TriangleAlert v-else-if="stateTone(task.state) === 'danger' || stateTone(task.state) === 'warning'" :size="15" />
                <LoaderCircle v-else-if="task.state === 'TASK_STATE_WORKING'" :size="15" />
                <CircleDot v-else :size="15" />
              </span>
              <span class="task-copy"><strong>{{ stateLabel[task.state] }}</strong><small>{{ task.message || task.taskId }}</small></span>
              <time>{{ formatDate(task.updatedAt) }}</time>
            </button>
            <div v-if="expandedTask === task.taskId" class="task-events">
              <div v-if="loadingTask === task.taskId" class="task-events-loading"><LoaderCircle :size="14" />加载事件</div>
              <ol v-else>
                <li v-for="event in eventCache[task.taskId] || []" :key="event.eventId" :class="stateTone(event.state)">
                  <i></i>
                  <div><strong>{{ stateLabel[event.state] }}</strong><p>{{ event.message }}</p><time>{{ formatDate(event.timestamp) }} · {{ event.source }}</time></div>
                </li>
              </ol>
            </div>
          </article>
        </div>
        <div v-else class="task-empty">尚无A2A任务记录</div>
      </section>

      <section class="detail-section">
        <div class="section-heading"><Server :size="16" /><h3>接入信息</h3></div>
        <dl class="info-list">
          <div><dt>模式</dt><dd>{{ agent.integrationMode === 'managed-adapter' ? '托管 Adapter' : '原生 A2A' }}</dd></div>
          <div v-if="agent.definition.backend"><dt>Adapter</dt><dd>{{ agent.definition.backend.adapterType }}</dd></div>
          <div><dt>Endpoint</dt><dd><code>{{ status.endpoint || '未提供' }}</code></dd></div>
          <div><dt>凭据</dt><dd><KeyRound :size="13" />{{ agent.definition.backend?.secretRef ? 'Registry 托管' : agent.definition.backend?.authEnv ? '环境变量' : '无' }}</dd></div>
          <div><dt>Revision</dt><dd>r{{ agent.revision }}</dd></div>
        </dl>
      </section>

      <section class="detail-section">
        <div class="section-heading"><FileJson :size="16" /><h3>能力</h3></div>
        <div class="skill-detail" v-for="skill in agent.definition.skills" :key="skill.id">
          <strong>{{ skill.name }}</strong><p>{{ skill.description }}</p>
        </div>
      </section>

      <div class="drawer-actions">
        <button class="button primary" type="button" @click="$emit('edit', agent)"><Pencil :size="16" />编辑配置</button>
        <button class="button secondary" type="button" @click="copy(agent.cardUrl)"><ClipboardCopy :size="16" />复制 Card URL</button>
        <a class="button secondary" :href="agent.cardUrl" target="_blank"><ExternalLink :size="16" />查看 Card</a>
        <button class="button danger ghost" type="button" @click="$emit('remove', agent)"><Trash2 :size="16" />删除</button>
      </div>
    </aside>
  </div>
</template>
