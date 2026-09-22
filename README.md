# OpenClaw Main -> Dynamic A2A Registry and Gateway

This demo lets the OpenClaw `main` agent discover and delegate work to
registered specialist agents. It supports two integration modes:

1. `native-a2a`: the provider operates an A2A server and registers its Agent
   Card URL.
2. `managed-adapter`: the provider exposes an agent endpoint that matches one
   of the platform-maintained adapters.

The Registry is a curated control-plane catalog. It runs independently from
the A2A Gateway data plane. The `main` agent still decides which specialist to
use from the registered Agent Cards.

```text
Registry control plane : http://127.0.0.1:4200
A2A Gateway data plane : http://127.0.0.1:4101
```

## Runtime flow

```text
User -> OpenClaw main
  -> a2a_discover
  -> Registry returns active Agent Cards
  -> main selects an agent
  -> a2a_send fetches only that agent's Card

native-a2a
  -> Manager calls the provider's A2A interface directly

managed-adapter
  -> Manager calls /a2a/agents/{agentId}
  -> shared Gateway resolves agentId through the Registry internal API
  -> Gateway publishes ExecuteTask when RabbitMQ is configured
  -> partition Worker invokes the selected platform Adapter
  -> result returns to Gateway and completes the A2A Task

Task/Message -> Manager summarizes -> User
```

Each Agent has a separate Card. Managed agents share one Gateway process, but
their Cards advertise different logical URLs:

```text
http://127.0.0.1:4101/a2a/agents/planner
http://127.0.0.1:4101/a2a/agents/dongjian
```

The managed Gateway does not use the A2A `tenant` field for agent selection.
An external native Agent Card may still declare its own tenant, and the Manager
client will echo it as required by A2A 1.0.

## Start

```powershell
npm install
npm --prefix registry-web install
npm run build
npm run bridge
```

For deterministic local execution:

```powershell
npm run bridge:mock
```

Run either service independently:

```powershell
npm run registry
npm run gateway
```

`npm run bridge` supervises both processes. The Registry owns SQLite and the
management UI on port `4200`; the Gateway exposes only A2A routes on `4101`.
Without `A2A_BROKER_URL`, managed tasks execute inline for local compatibility.

## RabbitMQ task execution

The Docker Compose deployment enables queue-backed execution by default:

```powershell
docker compose up --build
```

It starts Registry, RabbitMQ, A2A Gateway and an independent Python Worker.
RabbitMQ management is available at `http://127.0.0.1:15672` with user `a2a`
and the password configured by `RABBITMQ_PASSWORD`.

Task commands use a stable message ID of `execute:{agentId}:{taskId}` and are
routed to one of `A2A_QUEUE_PARTITIONS` quorum queues by a stable hash of the
task ID. Every partition enables Single Active Consumer and `prefetch=1`, so
messages in one partition execute sequentially while partitions run in
parallel. Failed broker deliveries are moved to the `a2a.task.dead` queue after
five returns.

Before invoking an Adapter, the Worker claims `(agentId, taskId)` through the
Registry. Completed executions are persisted and replayed on redelivery rather
than calling the Agent twice. The lease only covers broker/worker retries; a
remote Agent still needs its own idempotency key to eliminate the narrow crash
window after remote completion and before Registry persistence.

Set `A2A_WORKER_LEASE_SECONDS` longer than the largest Adapter execution
timeout. The first queue-backed version still keeps the live A2A task and its
temporary RabbitMQ reply queue in Gateway memory, so a Gateway restart requires
the Manager to recover or resubmit the task; Worker-side execution records stay
durable in Registry.

For local services with an existing RabbitMQ instance:

```powershell
$env:A2A_BROKER_URL = "amqp://a2a:change-me-rabbitmq@127.0.0.1:5672/"
npm run bridge
```

`run-services.py` starts the Worker automatically when `A2A_BROKER_URL` is
present. `npm run worker` starts only the Worker for debugging.

### Post-task Agent experience

When `AGENT_EXPERIENCE_ENABLED=1`, a completed queue-backed task publishes a
durable `CollectExperience` message after its formal result has been returned.
The experience consumer calls the same managed Agent in an isolated
`experience:{taskId}` context and requires a structured reflection with two
separate sections:

- `executionProcess`: problems actually encountered, effective actions and
  unresolved issues from this run.
- `taskImprovement`: strengths to preserve and concrete ways to improve this
  particular kind of deliverable next time.

The reflection does not block the A2A result. Registry stores a separate
experience lease for `(agentId, taskId)`, so a failed collection can be retried
and a completed collection is replayed without invoking the Agent again. The
result is saved to MemOS Cloud as structured evidence. Agent, capability and
tag fields are retained only as provenance; they do not determine whether the
experience may improve a shared Skill. Scheduled evolution retrieves evidence
from the semantic task profile alongside explicit user feedback.

Enable it in the same `cmd.exe` session that starts the services:

```cmd
set "AGENT_EXPERIENCE_ENABLED=1"
set "MEMOS_API_KEY=your-memos-api-key"
set "MEMOS_USER_ID=openclaw-user"
start-network.cmd
```

`start-network.cmd` defaults `MEMOS_BASE_URL` to the hosted
`https://memos.memtensor.cn/api/openmem/v1` endpoint, `MEMOS_USER_ID` to the
same `openclaw-user` identity used by the OpenClaw MemOS plugin, and
`MEMOS_TIMEOUT_SECONDS` to `30`.
`MEMOS_TOKEN` remains supported as a compatibility alias for the official
`MEMOS_API_KEY` variable.

The first version collects experience for `managed-adapter` Agents executed by
the RabbitMQ Worker. A native A2A provider must expose an experience Artifact or
an equivalent reflection capability before its internal execution experience
can be collected by the platform.

Registry UI:

```text
http://127.0.0.1:4200/registry-ui/
```

The UI source is a Vue 3/Vite app in `registry-web`. Use `npm run dev:web`
for local frontend development; `npm run build:web` emits the production
assets served by Registry. Runtime availability is refreshed every 8 seconds
with low-impact endpoint connectivity probes.

## Registry API

Public discovery:

```text
GET /registry/v1/agents
GET /registry/v1/agent-cards
GET /registry/v1/agents/{agentId}/card
GET /registry/v1/adapters
GET /health
```

Administration:

```text
GET    /registry/v1/admin/agents
GET    /registry/v1/admin/agents/{agentId}
POST   /registry/v1/agents
PUT    /registry/v1/agents/{agentId}
DELETE /registry/v1/agents/{agentId}
POST   /registry/v1/agent-cards/inspect
POST   /registry/v1/adapter-requests
GET    /registry/v1/admin/adapter-requests
```

Set `REGISTRY_ADMIN_TOKEN` to require a bearer token for administration.
`A2A_REGISTRY_ADMIN_TOKEN` remains accepted for compatibility.
Set `REGISTRY_SERVICE_TOKEN` on Registry and the same value in
`A2A_REGISTRY_SERVICE_TOKEN` on Gateway to protect the internal definition API.
Set `REGISTRY_REGISTRATION_TOKEN` to protect self-registration.
The Registry UI stores the token only in the current browser tab.

The Gateway and Worker use the service-protected execution endpoints
`/registry/v1/internal/task-executions/claim` and `complete` for durable
idempotency records. Task lifecycle events carry a per-task sequence number;
late events remain in the timeline but cannot roll the latest task state back.

Managed Agent bearer tokens can be submitted as the write-only
`backend.authToken` administration field, or as
`interface.authentication.token` during self-registration. Registry replaces
the value with the unique `agent:{agentId}:bearer` reference and encrypts the
token in the `agent_secrets` SQLite table. The encryption key is read from
`REGISTRY_SECRET_KEY`; for local demos Registry creates
`.runtime/registry-secrets.key`. Back up that key with the database and protect
both files. Catalog, Agent Card and admin responses never include plaintext.

`npm run bridge` automatically creates a process-local service token shared by
Registry and Gateway. When starting them separately, configure the same
`REGISTRY_SERVICE_TOKEN` and `A2A_REGISTRY_SERVICE_TOKEN` value so Gateway can
resolve managed credentials through the internal API.

## Register a native A2A agent

The provider owns the A2A server, AgentExecutor, Agent Card and deployment.
The Registry fetches and validates the Card, then stores a discovery snapshot.

```json
{
  "agentId": "external-writer",
  "integrationMode": "native-a2a",
  "sourceAgentCardUrl": "https://writer.example.com/.well-known/agent-card.json",
  "status": "ACTIVE"
}
```

Register it:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:4200/registry/v1/agents `
  -ContentType application/json `
  -Body (Get-Content .\external-writer.json -Raw)
```

The imported Card remains authoritative for name, description, skills,
capabilities and remote A2A interfaces. Updating the registration fetches it
again.

## Register a managed adapter agent

```json
{
  "agentId": "planner",
  "integrationMode": "managed-adapter",
  "name": "OpenClaw Planner",
  "description": "Generates forward-looking reports.",
  "version": "1.0.0",
  "skills": [
    {
      "id": "forward-report",
      "name": "Forward-looking report",
      "description": "Generates a report from a digest URL.",
      "inputModes": ["text/plain"],
      "outputModes": ["text/plain"]
    }
  ],
  "inputModes": ["text/plain"],
  "outputModes": ["text/plain"],
  "capabilities": {
    "streaming": false,
    "pushNotifications": true
  },
  "backend": {
    "adapterType": "openclaw-responses",
    "endpoint": "http://127.0.0.1:18789",
    "agentId": "planner",
    "authEnv": "OPENCLAW_GATEWAY_TOKEN",
    "timeoutSeconds": 600
  },
  "status": "ACTIVE"
}
```

Available adapters:

- `openclaw-responses`: calls `{endpoint}/v1/responses` with model
  `openclaw/{agentId}`.
- `declarative-http`: hot-loads a JSON request template containing
  `{{input}}` and `{{contextId}}`, then reads configured JSON selectors.

Use `register_agent.py` for JSON-file registration:

```powershell
python .\register_agent.py .\dongjian-agent.json
```

## Agent self-registration Skill

The standalone Skill is in `agent-self-register/`. An agent submits its
developer-approved skills and technical interface manifest to:

```text
POST /onboarding/v1/register
```

Registration settings are kept in `agent-self-register/.env`. The helper loads
that file automatically. For an endpoint protected by a bearer token, put its
token in `AGENT_ENDPOINT_TOKEN`; the helper sends it as a write-only field, and
it is not required in the manifest file.

Registry deterministically selects one of four outcomes:

- `NATIVE_A2A`
- `EXISTING_ADAPTER`
- `GENERATED_DECLARATIVE_ADAPTER`
- `ADAPTER_REQUIRED`

Validate and submit the bundled example:

```powershell
python .\agent-self-register\scripts\register_agent.py `
  .\agent-self-register\references\example-manifest.json `
  --dry-run
```

Unknown asynchronous, streaming, file or signed protocols create an Adapter
request instead of loading generated code into Registry.

## Scheduled Manager skill evolution

`skill-evolution-service` runs an optional local scheduler that turns
task-relevant MemOS Cloud user feedback and Agent experience into a versioned
Skill candidate.
Scheduling, evidence hashes,
leases and publication are deterministic Python code; OpenClaw `main` decides
whether to return `CREATE`, `UPDATE`, `NO_CHANGE` or `REVIEW_REQUIRED` and
generates the candidate package.

The scheduler calls SkillHub's native HTTP API from Python, so no ClawHub CLI
installation or CLI login is required. Create a SkillHub API token with
`skill:read` and `skill:publish` scopes, then configure it in the same
`cmd.exe` window used to start the project:

```cmd
set "SKILL_EVOLUTION_ENABLED=1"
set "MEMOS_BASE_URL=https://memos.memtensor.cn/api/openmem/v1"
set "MEMOS_API_KEY=your-memos-api-key"
set "MEMOS_USER_ID=openclaw-user"
set "SKILLHUB_REGISTRY=http://127.0.0.1:8080"
set "SKILLHUB_API_TOKEN=your-skillhub-api-token"

start-network.cmd
```

`start-network.cmd` still runs Registry, Gateway and the A2A Worker locally.
When `SKILL_EVOLUTION_ENABLED=1`, `run-services.py` also starts the local
evolution scheduler. RabbitMQ remains independent; this maintenance scheduler
uses its own SQLite lease and does not enter the user-task queue.

Edit `config/skill-evolution-policies.json` to enable a policy. Keep
`autoPublish` false while validating the first candidates; set it to true only
when automatic namespace publication is intended:

```json
{
  "id": "insight-report-evolution",
  "enabled": true,
  "intervalSeconds": 21600,
  "taskProfile": {
    "name": "洞见前瞻报告生成",
    "description": "从AI资讯聚合页获取文章并生成事实摘要、重点解读、趋势研判、金融机构应对策略和结构化报告。",
    "evidenceQuery": "检索与洞见前瞻报告生成直接相关的用户反馈和执行经验，重点关注资讯抓取完整性、事实准确性、信源覆盖、分析深度、策略可执行性、报告结构和交付质量。只根据任务内容和经验内容的语义相关性召回，不限制执行主体或具体实现方式。",
    "relevanceCriteria": [
      "经验涉及AI资讯或行业动态的采集、核验与整理",
      "经验涉及摘要、解读、趋势研判或金融机构应对策略",
      "经验能够改善该类报告的内容、流程或交付效果"
    ]
  },
  "minimumNewMemories": 5,
  "targetSkill": "insight-report-writer",
  "namespace": "global",
  "visibility": "namespace-only",
  "managerAgentId": "main",
  "fetchCurrentSkill": true,
  "autoPublish": true
}
```

For the native SkillHub API client, policy `namespace` maps to the SkillHub
namespace, `targetSkill` maps to the slug, and the Manager candidate version is
inserted into the upload-only `SKILL.md` frontmatter. The local candidate is not
rewritten. Policy visibility maps directly to SkillHub `PUBLIC`,
`NAMESPACE_ONLY`, or `PRIVATE`. After publishing, the service downloads the
exact version and verifies that every packaged file was stored intact.

`taskProfile.evidenceQuery` is sent directly to MemOS Cloud semantic search.
There is no tag or Skill-ID eligibility filter after retrieval. OpenClaw
`main` receives the task description, relevance criteria and returned evidence,
and judges usefulness solely from task semantics. MemOS Cloud search does not
expose the chronological `/memos` cursor used by the earlier local API
integration, so the service uses a deterministic evidence hash to skip an
unchanged result set. Generated packages and provenance are retained under
`.runtime/skill-candidates`; scheduler leases, outcomes and the last evidence
hash are stored in `.runtime/skill-evolution.sqlite3`.

Run or inspect it independently:

```cmd
npm run skill-evolution -- --policy insight-report-evolution
npm run skill-evolution -- --show-state
npm run skill-evolution -- --recover-running
```

`--recover-running` is an operator recovery command. Stop the existing
scheduler process first, then use it to release a lease left behind by an
interrupted process. Normal `NO_CHANGE`, `DUPLICATE_EVIDENCE`, waiting, and
publish outcomes are logged explicitly by the scheduler.

Rerun `scripts/setup.ps1` once after upgrading so the project-managed
`skill-evolution` Skill and Manager guidance are copied into the `main`
workspace. Publishing makes a Skill discoverable in SkillHub; each
heterogeneous Agent still needs its own SkillHub sync/install adapter before it
can activate that Skill.

## Request a new Adapter

An agent that is neither native A2A nor compatible with an existing Adapter
must provide an endpoint and interface documentation. Submit a request from the
Registry UI or:

```json
{
  "agentName": "Knowledge Agent",
  "contact": "team@example.com",
  "endpoint": "https://agent.example.com/invoke",
  "documentationUrl": "https://docs.example.com/agent-api",
  "notes": "Describe authentication, async jobs and response examples."
}
```

```text
POST /registry/v1/adapter-requests
```

The request does not execute user-supplied code. A new Adapter must be
implemented, tested, reviewed and added to the platform Adapter catalog.

## Persistence

SQLite stores:

- normalized agent definitions
- imported or generated Agent Card snapshots
- Card hashes and revisions
- active/disabled status
- Adapter requests

The database is opened only by `registry-service`. Gateway reads active
definitions over the protected internal HTTP API.

Default database:

```text
.runtime/agent-registry.sqlite3
```

Older records without `integrationMode` are read as `managed-adapter`.

## Verification

```powershell
npm test
npm run smoke
```

The Python suite covers CRUD, native Card import, managed Gateway routing,
official A2A client compatibility, task polling and push notifications.
