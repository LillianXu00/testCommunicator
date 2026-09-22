<script setup lang="ts">
import { ArrowUpRight, Clock3, PlugZap, ServerCog } from 'lucide-vue-next'
import AgentAvatar from './AgentAvatar.vue'
import type { AgentRecord, RuntimeStatus } from '../types'

defineProps<{
  agent: AgentRecord
  status: RuntimeStatus
}>()

defineEmits<{
  select: [agent: AgentRecord]
}>()

const availabilityLabel: Record<string, string> = {
  ONLINE: '在线',
  OFFLINE: '离线',
  UNKNOWN: '待探测',
  DISABLED: '已停用',
  DEGRADED: '异常',
}

const activityLabel: Record<string, string> = {
  IDLE: '空闲', QUEUED: '排队中', WORKING: '执行中',
  WAITING_INPUT: '等待输入', AUTH_REQUIRED: '等待认证', STALE: '状态超时',
}
</script>

<template>
  <button class="agent-card" type="button" @click="$emit('select', agent)">
    <div class="agent-card-topline">
      <span class="mode-label">
        <ServerCog v-if="agent.integrationMode === 'managed-adapter'" :size="13" />
        <PlugZap v-else :size="13" />
        {{ agent.integrationMode === 'managed-adapter' ? '托管适配' : '原生 A2A' }}
      </span>
      <ArrowUpRight :size="17" class="open-arrow" />
    </div>
    <AgentAvatar
      :agent-id="agent.agentId"
      :profile="`${agent.definition.name || ''} ${agent.definition.description || ''}`"
      :skills="agent.definition.skills"
      :availability="status.availability"
      :activity="status.activity"
    />
    <div class="agent-card-copy">
      <div class="agent-title-line">
        <h3>{{ agent.definition.name || agent.agentCard.name }}</h3>
        <span class="availability" :class="status.availability.toLowerCase()">
          <i></i>{{ availabilityLabel[status.availability] }}
        </span>
      </div>
      <p>{{ agent.definition.description || agent.agentCard.description }}</p>
    </div>
    <div class="activity-strip" :class="status.activity.toLowerCase()">
      <i></i>
      <strong>{{ activityLabel[status.activity] }}</strong>
      <span v-if="status.activeTaskCount">{{ status.activeTaskCount }}个进行中</span>
      <span v-else>暂无任务</span>
    </div>
    <div class="skill-row">
      <span v-for="skill in (agent.definition.skills || []).slice(0, 2)" :key="skill.id">
        {{ skill.name }}
      </span>
      <span v-if="(agent.definition.skills || []).length > 2">
        +{{ (agent.definition.skills || []).length - 2 }}
      </span>
    </div>
    <div class="agent-card-footer">
      <span><Clock3 :size="13" />{{ status.latencyMs == null ? '未获得延迟' : `${status.latencyMs} ms` }}</span>
      <span>r{{ agent.revision }}</span>
    </div>
  </button>
</template>
