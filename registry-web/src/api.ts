import type {
  AdapterDefinition, AgentDefinition, AgentRecord, RuntimeStatus, TaskEvent, TaskRun,
} from './types'

let adminToken = sessionStorage.getItem('a2a-registry-admin-token') || ''

export function setAdminToken(token: string) {
  adminToken = token.trim()
  if (adminToken) sessionStorage.setItem('a2a-registry-admin-token', adminToken)
  else sessionStorage.removeItem('a2a-registry-admin-token')
}

export function getAdminToken() {
  return adminToken
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(adminToken ? { Authorization: `Bearer ${adminToken}` } : {}),
      ...(init.headers || {}),
    },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(body.error || `${response.status} ${response.statusText}`) as Error & { status?: number }
    error.status = response.status
    throw error
  }
  return body as T
}

export async function loadAgents() {
  const payload = await request<{ agents: AgentRecord[] }>('/registry/v1/admin/agents')
  return payload.agents
}

export async function loadAgent(agentId: string) {
  return request<AgentRecord>(`/registry/v1/admin/agents/${encodeURIComponent(agentId)}`)
}

export async function loadStatuses(refresh = false) {
  const suffix = refresh ? '/refresh' : ''
  const payload = await request<{ statuses: RuntimeStatus[] }>(`/registry/v1/admin/agent-status${suffix}`, {
    method: refresh ? 'POST' : 'GET',
  })
  return payload.statuses
}

export async function loadAgentTasks(agentId: string) {
  return request<{
    activity: RuntimeStatus['activity']
    activeTaskCount: number
    workingTaskCount: number
    waitingTaskCount: number
    currentTask: TaskRun | null
    recentTasks: TaskRun[]
  }>(`/registry/v1/admin/agents/${encodeURIComponent(agentId)}/tasks`)
}

export async function loadTaskEvents(agentId: string, taskId: string) {
  const payload = await request<{ events: TaskEvent[] }>(
    `/registry/v1/admin/agents/${encodeURIComponent(agentId)}/tasks/${encodeURIComponent(taskId)}/events`,
  )
  return payload.events
}

export async function streamTaskEvents(
  onEvent: (event: TaskEvent) => void,
  signal: AbortSignal,
) {
  const response = await fetch('/registry/v1/admin/task-events/stream', {
    headers: adminToken ? { Authorization: `Bearer ${adminToken}` } : {},
    signal,
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}))
    const error = new Error(body.error || `${response.status} ${response.statusText}`) as Error & { status?: number }
    error.status = response.status
    throw error
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (!signal.aborted) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split(/\r?\n\r?\n/)
    buffer = chunks.pop() || ''
    for (const chunk of chunks) {
      const data = chunk.split(/\r?\n/)
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trim())
        .join('\n')
      if (data) onEvent(JSON.parse(data) as TaskEvent)
    }
  }
}

export async function loadAdapters() {
  const payload = await request<{ adapters: AdapterDefinition[] }>('/registry/v1/adapters')
  return payload.adapters
}

export async function saveAgent(definition: AgentDefinition, editingId?: string) {
  const path = editingId
    ? `/registry/v1/agents/${encodeURIComponent(editingId)}`
    : '/registry/v1/agents'
  return request<AgentRecord>(path, {
    method: editingId ? 'PUT' : 'POST',
    body: JSON.stringify(definition),
  })
}

export async function deleteAgent(agentId: string) {
  return request<{ deleted: boolean }>(`/registry/v1/agents/${encodeURIComponent(agentId)}`, {
    method: 'DELETE',
  })
}

export async function inspectCard(url: string) {
  return request<{ agentCard: Record<string, any> }>('/registry/v1/agent-cards/inspect', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export async function submitAdapterRequest(payload: Record<string, string>) {
  return request<{ requestId: string }>('/registry/v1/adapter-requests', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
