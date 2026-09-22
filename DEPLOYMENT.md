# A2A Registry + Gateway 部署指南

## 一、架构全景

```
┌──────────────────┐     ┌─────────────────┐     ┌─────────────────────────┐
│   OpenClaw 平台   │     │  本项目 (Docker)  │     │   外部 Agent 端点        │
│                  │     │                  │     │                         │
│  ┌────────────┐  │     │  ┌─────────────┐ │     │  POST /v1/responses     │
│  │ main agent │  │     │  │  Registry   │ │     │  (OpenClaw specialist   │
│  │ 加载:      │  │     │  │  :4200      │ │     │   agent)               │
│  │ manager-   │  │     │  │  SQLite     │ │     │                         │
│  │ a2a-plugin │──┼──①──┼─▶│  SecretStore│ │     │  或任意 HTTP API        │
│  └────────────┘  │     │  └──────┬──────┘ │     │  (generic/declarative   │
│        │         │     │         │ 内网    │     │   adapter)              │
│        │         │     │         ▼        │     └─────────────────────────┘
│        │         │     │  ┌─────────────┐ │               ▲
│        └─────────┼──②──┼─▶│  Gateway    │─┼───③───────────┘
│                  │     │  │  :4101      │ │
│  ┌────────────┐  │     │  └─────────────┘ │
│  │ feishu     │  │     └─────────────────┘
│  │ plugin     │  │
│  └────────────┘  │
└──────────────────┘
       ▲
       │ 飞书 webhook
       │
   ┌───┴───┐
   │ 飞书   │
   │ 用户   │
   └───────┘

① 公开 REST: 发现 Agent 列表、获取 Agent Card
② A2A JSON-RPC: 提交任务、获取结果、注册 push 通知
③ Backend 协议: OpenClaw Responses / Declarative HTTP
```

---

## 二、部署前准备

### 1. 服务器要求

- Linux x86_64（Ubuntu 20.04+ / Debian 11+ / CentOS 8+）
- Docker 20.10+ + Docker Compose v2
- Python 3.10+（仅用于生成密钥，不参与运行）
- 确保以下端口未被占用：`4200`、`4101`、`5672`、`15672`

### 2. 网络模式说明

Compose 默认创建**隔离的桥接网络**（bridge），容器有独立的虚拟网卡和内部 IP，不走宿主机网络栈。`ports: "4200:4200"` 只是做端口转发，不等于共享网络。

**这带来的一个重要问题**：如果 OpenClaw 平台跑在同一台宿主机上（例如 `127.0.0.1:18789`），agent 注册文件里写 `"endpoint": "http://127.0.0.1:18789"` 是**不通的**——容器内的 `127.0.0.1` 指向容器自己，不是宿主机。

三种解决方案：

| 方案 | agent 配置中的 endpoint | 说明 |
|---|---|---|
| `host.docker.internal` | `http://host.docker.internal:18789` | 仅 Docker Desktop（Windows/Mac）原生支持，Linux 需额外配置 |
| 宿主机 IP | `http://10.0.0.5:18789` | Linux 生产环境最可靠，需要 OpenClaw 监听 `0.0.0.0` |
| `network_mode: host` | `http://127.0.0.1:18789` | 容器直接共用宿主机网络，失去隔离，端口冲突风险高 |

**推荐做法**（Linux 生产环境）：让 OpenClaw Gateway 监听 `0.0.0.0:18789`，然后 agent 注册文件里写宿主机的真实 IP：

```json
{
  "backend": {
    "endpoint": "http://10.0.0.5:18789",
    ...
  }
}
```

如果不需要网络隔离（例如单机部署），也可以在 `docker-compose.yml` 两个 service 下各加 `network_mode: host`，然后删掉 `ports` 映射——容器直接监听宿主机端口，`127.0.0.1` 就是宿主机。代价是失去容器网络隔离和 Docker 内置 DNS。

### 2. 克隆项目并构建插件

```bash
# 将项目上传到服务器
scp -r ./coordination user@your-server:/srv/

# 登录服务器
ssh user@your-server
cd /srv/coordination

# 构建 manager-a2a-plugin（需要有 Node.js 和 OpenClaw CLI）
cd manager-a2a-plugin
npm install
npm run build
npm run plugin:build     # 打包为 .openclaw-plugin 文件
cd ..
```

### 3. 生成密钥

```bash
# 安全密钥（agent token 的加解密密钥，绝对不能泄露，重启不能变）
python3 -c "import secrets; print(secrets.token_hex(32))"
# 输出示例: a1b2c3d4e5f6...（64 位十六进制）

# 服务间通信 Token
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# 输出示例: xYz123AbC...（URL 安全的随机字符串）
```

### 4. 配置环境变量

```bash
cp .env.example .env
vim .env  # 按以下说明修改
```

必填项：

```bash
# 服务器的实际 IP 或域名
REGISTRY_PUBLIC_URL=http://10.0.0.5:4200
A2A_PUBLIC_URL=http://10.0.0.5:4101
A2A_GATEWAY_PUBLIC_URL=http://10.0.0.5:4101

# 上面生成的
REGISTRY_SECRET_KEY=a1b2c3d4e5f6...
REGISTRY_SERVICE_TOKEN=xYz123AbC...

# RabbitMQ（务必修改默认密码）
RABBITMQ_PASSWORD=replace-with-a-strong-password
A2A_QUEUE_PARTITIONS=8
A2A_QUEUE_RESULT_TIMEOUT_SECONDS=900
A2A_WORKER_LEASE_SECONDS=1200

# 如果 agent backend 是 openclaw-responses 类型，必须配
OPENCLAW_GATEWAY_TOKEN=sk-your-openclaw-gateway-token
```

可选项：

```bash
# 保护 self-registration 端点，不配 = 不校验
REGISTRY_REGISTRATION_TOKEN=

# 保护 admin CRUD 端点，不配 = 不校验
REGISTRY_ADMIN_TOKEN=
```

### 5. 准备 Agent 注册文件

如果首次启动时希望自动注册 agent，把 JSON 文件放在 `config/` 目录下并设置环境变量：

```bash
# .env 中指定
REGISTRY_BOOTSTRAP_FILE=/config/planner-agent.json
```

[config/planner-agent.json](config/planner-agent.json) 示例：

```json
{
  "agentId": "planner",
  "integrationMode": "managed-adapter",
  "name": "OpenClaw Planner",
  "description": "...",
  "skills": [...],
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

> **注意**：bootstrap 文件只在 agent 首次注册时生效。如果 agent 已存在，seed 会跳过不覆盖。Bootstrap 不是必须的——你也可以启动后通过 API 手动注册。

---

## 三、启动服务

### 3.1 构建镜像 + 启动

`docker compose up -d` **会自动包含构建**（如果镜像不存在或加了 `--build`），但建议显式分两步，把构建错误和启动错误分开排查：

```bash
cd /srv/coordination

# 第一步：构建镜像（首次或 Dockerfile 变更后需要）
docker compose build

# 第二步：启动（后台运行）
docker compose up -d
```

构建阶段做的事：

```
docker compose build
       │
       ├─ 构建 registry 镜像（registry-service/Dockerfile）
       │      FROM python:3.11-slim
       │      pip install a2a-sdk cryptography httpx starlette uvicorn
       │      COPY registry_service/ → /app/registry_service/
       │
       └─ 构建 gateway 镜像（a2a-server/Dockerfile）
              FROM python:3.11-slim
              pip install a2a-sdk aio-pika httpx starlette uvicorn
              COPY a2a_server/ → /app/a2a_server/
```

镜像构建完后，`docker compose up -d` 的执行流程：

```
docker compose up -d
       │
       ├─ 1. 创建 Docker 网络
       ├─ 2. 创建 registry-data 和 rabbitmq-data 持久化卷
       │      Docker 在 /var/lib/docker/volumes/ 下分配目录
       │
       ├─ 3. 挂载 ./config → /config:ro（宿主机配置目录，只读）
       │
       ├─ 4. 启动 RabbitMQ 和 Registry
       │      uvicorn registry_service.app:create_app --factory
       │      ├── 读 REGISTRY_DB → /data/agent-registry.sqlite3
       │      │   注：/data 目录就是 registry-data volume 在容器内的映射
       │      ├── 读 REGISTRY_SECRET_KEY → 初始化加解密引擎
       │      ├── 读 REGISTRY_BOOTSTRAP_FILE → 从挂载的 /config 种子注册 agent
       │      └── 监听 0.0.0.0:4200
       │
       ├─ 5. 等待 RabbitMQ 与 Registry 健康检查通过
       │
       ├─ 6. 启动独立 a2a-worker，消费分区 Quorum Queue
       │      └── claim 任务 → 调用 Adapter → 保存结果 → ACK
       │
       └─ 7. 启动 gateway 容器
              uvicorn a2a_server.server:create_app --factory
              ├── 读 A2A_REGISTRY_URL → http://registry:4200（Docker 内部 DNS）
              ├── 读 A2A_REGISTRY_SERVICE_TOKEN → 调 Registry internal API 用
              └── 监听 0.0.0.0:4101
```


> **注意**：`REGISTRY_SECRET_KEY` 不在 volume 里，是靠 `.env` 环境变量注入的。如果 volume 里的数据库是加密的，必须保证 `.env` 里的 key 跟加密时一致，否则全部已注册 agent 的 token 解不开。

### 3.3 检查状态

```bash
# 查看启动日志
docker compose logs -f

# 确认 Registry、RabbitMQ、Gateway 和 Worker 均已启动
docker compose ps
# 输出:
# NAME            STATUS
# a2a-registry    Up (healthy)
# a2a-gateway     Up (healthy)
# a2a-rabbitmq    Up (healthy)
# a2a-worker      Up
```

### 验证

```bash
# Registry 健康检查
curl http://localhost:4200/health
# {"status":"ok","service":"agent-registry","activeAgents":1,...}

# Gateway 健康检查
curl http://localhost:4101/health
# {"status":"ok","service":"a2a-gateway","registry":"available","executionMode":"rabbitmq"}

# 查看已注册的 Agent
curl http://localhost:4200/registry/v1/agents
# {"agents":[...],"total":1}
```

---

## 四、注册 Agent

如果用了 bootstrap 文件并启动成功，跳过此步骤。

### 方式 A：Admin API 手动注册

```bash
curl -X POST http://localhost:4200/registry/v1/agents \
  -H "Authorization: Bearer $REGISTRY_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d @config/dongjian-agent.json
```

### 方式 B：自注册

```bash
# 先确保 REGISTRY_REGISTRATION_TOKEN 已配好

curl -X POST http://localhost:4200/onboarding/v1/register \
  -H "Authorization: Bearer $REGISTRY_REGISTRATION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agentId": "my-agent",
    "name": "My Custom Agent",
    "description": "A custom HTTP agent",
    "skills": [{
      "id": "custom-skill",
      "name": "Custom Skill",
      "description": "..."
    }],
    "interface": {
      "kind": "declarative-http",
      "endpoint": "https://my-api.example.com/execute",
      "request": {
        "body": {
          "query": "{{input}}",
          "session": "{{contextId}}"
        }
      },
      "response": {
        "textSelectors": ["$.result.text"]
      }
    }
  }'
```

自注册不需要事先写 agent JSON 到 config 目录——外部服务可以直接调用这个端点把自己注册进来。

---

## 五、安装 manager-a2a-plugin 到 OpenClaw

### 1. 插件构建产物

`npm run plugin:build` 会生成：

```bash
ls manager-a2a-plugin/
# openclaw.plugin.json   # 插件元数据（入口、配置 schema、工具声明）
# dist/index.js          # 编译后的插件代码
```

### 2. 安装到 OpenClaw

```bash
# 方式 A：OpenClaw CLI
openclaw plugins install ./manager-a2a-plugin

# 方式 B：直接复制到 OpenClaw plugins 目录
cp -r manager-a2a-plugin ~/.openclaw/plugins/manager-a2a-plugin
```

### 3. 配置 main agent 加载插件

在你的 OpenClaw main agent 配置中，确保 plugin 已激活并配置了正确的 Registry URL：

```jsonc
// ~/.openclaw/agents/main/config.json
{
  "plugins": ["manager-a2a-plugin"],
  "pluginConfig": {
    "manager-a2a-plugin": {
      "registryUrl": "http://10.0.0.5:4200",   // ← 指向你的 Registry 服务器
      "timeoutMs": 60000,
      "gatewayToken": "sk-xxx"                   // ← OpenClaw Gateway token
    }
  }
}
```

### 4. 重启 main agent

```bash
openclaw restart main
```

---


## 六、日常运维

### 添加新 Agent

```bash
curl -X POST http://localhost:4200/registry/v1/agents \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d @new-agent.json
```

plugin 下一次 `a2a_discover` 时自动感知到新 agent（无缓存，每次实时查 Registry）。

### 禁用 Agent

```bash
curl -X PUT http://localhost:4200/registry/v1/agents/my-agent \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agentId": "my-agent", "status": "DISABLED"}'
```

### 查看日志

```bash
docker compose logs -f registry
docker compose logs -f gateway
```

### 数据备份

```bash
# SQLite 数据库在 named volume 中
docker run --rm -v coordination_registry-data:/data -v $(pwd):/backup \
  alpine cp /data/agent-registry.sqlite3 /backup/backup-$(date +%Y%m%d).sqlite3

# 同时备份 REGISTRY_SECRET_KEY（没有它数据库里的加密 token 解不开）
```

### 数据恢复

```bash
# 1. 停服务
docker compose down

# 2. 还原数据库
docker run --rm -v coordination_registry-data:/data -v $(pwd):/backup \
  alpine cp /backup/backup-20250803.sqlite3 /data/agent-registry.sqlite3

# 3. 确保 .env 里的 REGISTRY_SECRET_KEY 与备份时一致

# 4. 重启
docker compose up -d
```

---

## 八、重要注意事项

1. **`REGISTRY_SECRET_KEY` 绝对不能变、不能丢**。它加密了所有 agent 的 bearer token。丢了密钥 = 所有已注册 agent 的 token 无法解密 = 需要重新注册所有 agent。

2. **`REGISTRY_SERVICE_TOKEN` 在 Registry 和 Gateway 两边必须一致**。不一致 → Gateway 调 Registry internal API 返回 401 → 所有 managed agent 不可用。

3. **plugin 的 `registryUrl` 必须指向 Registry 的公网地址**，不是 Docker 内部名字（main agent 不在 Docker 网络里，所以 `http://registry:4200` 不行）。

4. **Gateway Card 里的接口 URL 必须是可以从 main agent 访问到的地址**（`A2A_PUBLIC_URL`），因为 plugin 拿着这个 URL 去发 JSON-RPC 请求。

5. **飞书 plugin 和 main agent 是 OpenClaw 平台的事**，不属于本项目 Docker 部署范围。本项目的 Docker 只管 Registry + Gateway 两个服务。
