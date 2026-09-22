<script setup lang="ts">
import { Bot } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { Activity, Availability } from '../types'

const props = defineProps<{
  agentId: string
  profile?: string
  skills?: Array<{ id: string; name: string }>
  availability: Availability
  activity: Activity
  compact?: boolean
}>()

function hash(text: string) {
  let value = 2166136261
  for (const char of text) {
    value ^= char.charCodeAt(0)
    value = Math.imul(value, 16777619)
  }
  return value >>> 0
}

const seed = computed(() => hash(props.agentId))
const role = computed(() => {
  const terms = `${props.agentId} ${props.profile || ''} ${(props.skills || []).map((item) => `${item.id} ${item.name}`).join(' ')}`.toLowerCase()
  if (/planner|计划|规划/.test(terms)) return 'planner'
  if (/qclaw|code|开发|工程|文档/.test(terms)) return 'builder'
  if (/frontier|forward|前瞻|分析/.test(terms)) return 'frontier'
  if (/dongjian|insight|洞见|研究|report/.test(terms)) return 'insight'
  return ['insight', 'frontier', 'planner', 'builder'][seed.value % 4]
})
const assetUrl = computed(() => `/registry-ui/assets/agents/${role.value}.webp`)
const imageFailed = ref(false)
</script>

<template>
  <div
    class="agent-avatar"
    :class="[
      `is-${availability.toLowerCase()}`,
      `activity-${activity.toLowerCase()}`,
      `role-${role}`,
      { compact },
    ]"
    role="img"
    :aria-label="`${agentId} ${availability}`"
  >
    <div class="avatar-stage">
      <div class="avatar-backplate"></div>
      <img
        v-if="!imageFailed"
        class="avatar-art"
        :src="assetUrl"
        alt=""
        draggable="false"
        @error="imageFailed = true"
      />
      <div v-else class="avatar-fallback"><Bot :size="44" /></div>
      <div class="avatar-presence"><i></i></div>
    </div>
    <div v-if="activity === 'WORKING'" class="work-particles"><i></i><i></i><i></i></div>
    <div v-if="activity === 'QUEUED'" class="queue-sign"><i></i><i></i><i></i></div>
    <div v-if="activity === 'WAITING_INPUT'" class="input-sign">...</div>
    <div v-if="activity === 'AUTH_REQUIRED'" class="auth-sign">!</div>
    <div v-if="activity === 'STALE'" class="stale-sign">~</div>
    <div v-if="availability === 'OFFLINE'" class="sleep-sign">Z</div>
    <div v-if="availability === 'UNKNOWN'" class="unknown-sign">?</div>
  </div>
</template>
