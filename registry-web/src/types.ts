export type Availability = 'ONLINE' | 'OFFLINE' | 'UNKNOWN' | 'DISABLED' | 'DEGRADED'
export type Activity = 'IDLE' | 'QUEUED' | 'WORKING' | 'WAITING_INPUT' | 'AUTH_REQUIRED' | 'STALE'

export type A2ATaskState =
  | 'TASK_STATE_UNSPECIFIED'
  | 'TASK_STATE_SUBMITTED'
  | 'TASK_STATE_WORKING'
  | 'TASK_STATE_INPUT_REQUIRED'
  | 'TASK_STATE_AUTH_REQUIRED'
  | 'TASK_STATE_COMPLETED'
  | 'TASK_STATE_FAILED'
  | 'TASK_STATE_CANCELED'
  | 'TASK_STATE_REJECTED'

export interface TaskRun {
  agentId: string
  taskId: string
  contextId: string | null
  state: A2ATaskState
  message: string
  source: string
  createdAt: string
  updatedAt: string
  finishedAt: string | null
  metadata: Record<string, unknown>
}

export interface TaskEvent {
  sequence: number
  eventId: string
  agentId: string
  taskId: string
  contextId: string | null
  type: string
  state: A2ATaskState
  message: string
  source: string
  timestamp: string
  metadata: Record<string, unknown>
  duplicate?: boolean
}

export interface SkillDefinition {
  id: string
  name: string
  description: string
  tags?: string[]
  examples?: string[]
  inputModes?: string[]
  outputModes?: string[]
}

export interface BackendDefinition {
  adapterType: string
  endpoint: string
  agentId?: string
  authEnv?: string
  authToken?: string
  secretRef?: string
  clearAuthSecret?: boolean
  timeoutSeconds?: number
  requestBody?: Record<string, unknown>
  responseTextSelectors?: string[]
}

export interface AgentDefinition {
  agentId: string
  integrationMode: 'native-a2a' | 'managed-adapter'
  status: 'ACTIVE' | 'DISABLED'
  name?: string
  description?: string
  version?: string
  skills?: SkillDefinition[]
  inputModes?: string[]
  outputModes?: string[]
  capabilities?: {
    streaming?: boolean
    pushNotifications?: boolean
  }
  sourceAgentCardUrl?: string
  backend?: BackendDefinition
}

export interface AgentRecord {
  agentId: string
  integrationMode: 'native-a2a' | 'managed-adapter'
  status: 'ACTIVE' | 'DISABLED'
  revision: number
  cardRevision: string
  cardUrl: string
  agentCard: Record<string, any>
  updatedAt: string
  definition: AgentDefinition
}

export interface RuntimeStatus {
  agentId: string
  availability: Availability
  activity: Activity
  checkedAt: string
  latencyMs: number | null
  detail: string
  endpoint: string | null
  activeTaskCount: number
  workingTaskCount: number
  waitingTaskCount: number
  currentTask: TaskRun | null
  recentTasks: TaskRun[]
}

export interface AdapterDefinition {
  id: string
  name: string
  description: string
  bestFor: string
  requiresAgentId?: boolean
  defaultEndpoint?: string
  defaultAuthEnv?: string
  defaultTimeoutSeconds?: number
  requestExample?: Record<string, unknown>
  responseExample?: Record<string, unknown>
  requirements?: string[]
}
