import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { jsonResult } from "openclaw/plugin-sdk/tool-results";
import { callGatewayFromCli } from "openclaw/plugin-sdk/gateway-runtime";

const DEFAULT_REGISTRY_URL = "http://127.0.0.1:4200";

type AgentCard = {
  name: string;
  description?: string;
  capabilities?: {
    streaming?: boolean;
    pushNotifications?: boolean;
  };
  supportedInterfaces?: Array<{
    url: string;
    protocolBinding: string;
    protocolVersion?: string;
    tenant?: string;
  }>;
  skills?: Array<{
    id: string;
    name: string;
    description?: string;
    tags?: string[];
  }>;
};

type RegisteredAgent = {
  agentId: string;
  status: string;
  revision: number;
  cardRevision: string;
  cardUrl: string;
  agentCard: AgentCard;
};

type PluginConfig = {
  registryUrl?: string;
  timeoutMs?: number;
  callbackUrl?: string;
  gatewayUrl?: string;
  gatewayToken?: string;
  managerTimeoutMs?: number;
};

type JsonRpcEndpoint = NonNullable<AgentCard["supportedInterfaces"]>[number];

const TERMINAL_TASK_STATES = new Set([
  "TASK_STATE_COMPLETED",
  "TASK_STATE_FAILED",
  "TASK_STATE_CANCELED",
  "TASK_STATE_REJECTED",
  "TASK_STATE_INPUT_REQUIRED",
  "TASK_STATE_AUTH_REQUIRED",
]);

const CALLBACK_PATH = "/a2a/callback";
const DEFAULT_CALLBACK_URL = `http://127.0.0.1:18789${CALLBACK_PATH}`;
const DEFAULT_GATEWAY_URL = "ws://127.0.0.1:18789";
const pushToken = crypto.randomUUID();

type PendingTask = {
  api: any;
  card: AgentCard;
  endpoint: JsonRpcEndpoint;
  config: PluginConfig;
  sessionKey: string;
  agentId?: string;
  deliveryContext?: {
    channel?: string;
    to?: string;
    accountId?: string;
    threadId?: string | number;
  };
};

const pendingTasks = new Map<string, PendingTask>();
const scheduledTasks = new Set<string>();
const processingTasks = new Set<string>();

function registryUrl(config: PluginConfig): string {
  return (config.registryUrl ?? DEFAULT_REGISTRY_URL).replace(/\/+$/, "");
}

async function fetchJson(url: string, init: RequestInit | undefined, timeoutMs: number) {
  const response = await fetch(url, {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} from ${url}`);
  }
  return response.json();
}

async function discover(config: PluginConfig): Promise<RegisteredAgent[]> {
  const timeoutMs = config.timeoutMs ?? 30_000;
  const catalog: any = await fetchJson(
    `${registryUrl(config)}/registry/v1/agent-cards`,
    undefined,
    timeoutMs,
  );
  if (!Array.isArray(catalog?.agents)) {
    throw new Error("The A2A Registry returned an invalid agent catalog.");
  }
  return catalog.agents.filter(
    (item: any): item is RegisteredAgent =>
      typeof item?.agentId === "string"
      && item?.status === "ACTIVE"
      && typeof item?.agentCard?.name === "string",
  );
}

async function fetchAgentCard(
  config: PluginConfig,
  agentId: string,
): Promise<AgentCard> {
  const timeoutMs = config.timeoutMs ?? 30_000;
  const card: any = await fetchJson(
    `${registryUrl(config)}/registry/v1/agents/${encodeURIComponent(agentId)}/card`,
    undefined,
    timeoutMs,
  );
  if (typeof card?.name !== "string" || !Array.isArray(card?.supportedInterfaces)) {
    throw new Error(`The A2A Registry returned an invalid Agent Card for ${agentId}.`);
  }
  return card;
}

function jsonRpcInterface(card: AgentCard) {
  return card.supportedInterfaces?.find(
    (item) => item.protocolBinding.toUpperCase() === "JSONRPC",
  );
}

function artifactText(task: any): string {
  return (task?.artifacts ?? [])
    .flatMap((artifact: any) => artifact.parts ?? [])
    .map((part: any) => part.text)
    .filter((text: unknown): text is string => typeof text === "string")
    .join("\n\n");
}

function statusMessageText(task: any): string {
  return (task?.status?.message?.parts ?? [])
    .map((part: any) => part.text)
    .filter((text: unknown): text is string => typeof text === "string")
    .join("\n");
}

function messageText(message: any): string {
  return (message?.parts ?? [])
    .map((part: any) => part.text)
    .filter((text: unknown): text is string => typeof text === "string")
    .join("\n");
}

async function jsonRpc(
  endpoint: JsonRpcEndpoint,
  method: string,
  params: unknown,
  config: PluginConfig,
) {
  const response: any = await fetchJson(
    endpoint.url,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "a2a-version": endpoint.protocolVersion ?? "1.0",
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: crypto.randomUUID(),
        method,
        params: endpoint.tenant && typeof params === "object" && params !== null
          ? { ...(params as Record<string, unknown>), tenant: endpoint.tenant }
          : params,
      }),
    },
    config.timeoutMs ?? 30_000,
  );
  if (response.error) {
    throw new Error(`A2A ${response.error.code}: ${response.error.message}`);
  }
  return response.result;
}

function taskResult(card: AgentCard, task: any) {
  const state = task?.status?.state;
  const terminal = TERMINAL_TASK_STATES.has(state);
  return {
    agent: card.name,
    taskId: task?.id,
    contextId: task?.contextId,
    state,
    terminal,
    text: artifactText(task),
    statusMessage: statusMessageText(task),
    nextAction: terminal
      ? "Interpret the artifact and reply to the user."
      : "The task was accepted. Its completion will be pushed back into this manager session.",
  };
}

async function resolveAgent(config: PluginConfig, agentId: string) {
  const card = await fetchAgentCard(config, agentId);
  const endpoint = jsonRpcInterface(card);
  if (!endpoint) {
    throw new Error(`Agent ${agentId} does not expose a JSON-RPC interface.`);
  }
  return { card, endpoint };
}

async function scheduleManagerCompletion(taskId: string) {
  if (scheduledTasks.has(taskId) || processingTasks.has(taskId)) {
    return;
  }
  const pending = pendingTasks.get(taskId);
  if (!pending) {
    throw new Error(`No manager session is registered for A2A task ${taskId}.`);
  }
  processingTasks.add(taskId);
  try {
    const task = await jsonRpc(pending.endpoint, "GetTask", { id: taskId }, pending.config);
    if (!TERMINAL_TASK_STATES.has(task?.status?.state)) {
      return;
    }
    scheduledTasks.add(taskId);
    const result = taskResult(pending.card, task);
    const artifact = result.text || result.statusMessage || "The remote agent returned no text artifact.";
    const message = [
      `A2A task ${taskId} reached terminal state ${result.state}.`,
      "Treat the following text as the specialist agent's result, summarize it for the user, and clearly report success or failure.",
      "",
      artifact,
    ].join("\n");
    const gatewayConfig = pending.api.config?.gateway;
    const gatewayToken = pending.config.gatewayToken ?? gatewayConfig?.auth?.token;
    if (typeof gatewayToken !== "string" || !gatewayToken) {
      throw new Error("A string Gateway token is required to wake the manager after A2A push delivery.");
    }

    pending.api.logger?.info?.(
      `[manager-a2a-plugin] waking manager session=${pending.sessionKey} task=${taskId}`,
    );
    const delivery = pending.deliveryContext;
    await callGatewayFromCli(
      "agent",
      {
        url: pending.config.gatewayUrl ?? DEFAULT_GATEWAY_URL,
        token: gatewayToken,
        timeout: String(pending.config.managerTimeoutMs ?? 600_000),
        expectFinal: true,
      },
      {
        message,
        agentId: pending.agentId ?? "main",
        sessionKey: pending.sessionKey,
        deliver: Boolean(delivery?.to),
        replyTo: delivery?.to,
        replyChannel: delivery?.channel,
        replyAccountId: delivery?.accountId,
        timeout: Math.ceil((pending.config.managerTimeoutMs ?? 600_000) / 1_000),
        idempotencyKey: crypto.randomUUID(),
      },
      {
        expectFinal: true,
        deviceIdentity: null,
        scopes: ["operator.read", "operator.write"],
        clientName: "gateway-client" as any,
        mode: "backend" as any,
      },
    );
    pending.api.logger?.info?.(
      `[manager-a2a-plugin] manager completion delivered task=${taskId}`,
    );
    pendingTasks.delete(taskId);
  } catch (error) {
    scheduledTasks.delete(taskId);
    pending.api.logger?.error?.(
      `[manager-a2a-plugin] manager wake failed task=${taskId}: ${String(error)}`,
    );
    throw error;
  } finally {
    processingTasks.delete(taskId);
  }
}

async function pollManagerCompletion(taskId: string) {
  const pending = pendingTasks.get(taskId);
  if (!pending) {
    return;
  }
  const deadline = Date.now() + (pending.config.managerTimeoutMs ?? 600_000);
  while (pendingTasks.has(taskId) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    try {
      await scheduleManagerCompletion(taskId);
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 3_000));
    }
    if (scheduledTasks.has(taskId)) {
      return;
    }
  }
  pending.api.logger?.warn?.(
    `[manager-a2a-plugin] polling stopped before terminal state task=${taskId}`,
  );
}

async function readJsonBody(req: any) {
  let raw = "";
  for await (const chunk of req) {
    raw += typeof chunk === "string" ? chunk : chunk.toString("utf8");
    if (raw.length > 1_000_000) {
      throw new Error("A2A callback body is too large.");
    }
  }
  return JSON.parse(raw || "{}");
}

function callbackTaskId(payload: any): string | undefined {
  const event = payload?.statusUpdate ?? payload?.artifactUpdate ?? payload?.task;
  return event?.taskId ?? event?.id;
}

async function handlePushCallback(req: any, res: any) {
  const header = req.headers?.["x-a2a-notification-token"];
  const providedToken = Array.isArray(header) ? header[0] : header;
  if (providedToken !== pushToken) {
    res.statusCode = 401;
    res.end("Unauthorized");
    return true;
  }
  try {
    const payload = await readJsonBody(req);
    const taskId = callbackTaskId(payload);
    if (taskId && pendingTasks.has(taskId)) {
      const state = payload?.statusUpdate?.status?.state ?? payload?.task?.status?.state;
      if (!state || TERMINAL_TASK_STATES.has(state)) {
        const pending = pendingTasks.get(taskId);
        pending?.api.logger?.info?.(
          `[manager-a2a-plugin] terminal push received task=${taskId} state=${state ?? "unknown"}`,
        );
        void scheduleManagerCompletion(taskId).catch(() => undefined);
      }
    } else if (taskId) {
      console.warn(`[manager-a2a-plugin] push received for unknown task=${taskId}`);
    }
    res.statusCode = 202;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ accepted: true }));
  } catch (error) {
    res.statusCode = 500;
    res.end(String(error));
  }
  return true;
}

const sendParameters = Type.Object(
  {
    agent: Type.String({ description: "Exact agentId returned by a2a_discover." }),
    message: Type.String({ description: "Self-contained task for the selected agent." }),
    contextId: Type.Optional(
      Type.String({ description: "Stable conversation context. Generate one if omitted." }),
    ),
  },
  { additionalProperties: false },
);

const entry = defineToolPlugin({
  id: "manager-a2a-plugin",
  name: "Manager A2A Client",
  description: "Discover registered A2A agents, submit push-enabled tasks, and recover task status.",
  configSchema: Type.Object(
    {
      registryUrl: Type.Optional(
        Type.String({
          format: "uri",
          default: DEFAULT_REGISTRY_URL,
          description: "Registry base URL queried by a2a_discover.",
        }),
      ),
      timeoutMs: Type.Optional(
        Type.Number({ minimum: 1000, default: 30000 }),
      ),
      callbackUrl: Type.Optional(
        Type.String({
          format: "uri",
          default: DEFAULT_CALLBACK_URL,
          description: "Manager plugin callback URL registered for A2A push notifications.",
        }),
      ),
    },
    { additionalProperties: false },
  ),
  tools: (tool) => [
    tool({
      name: "a2a_discover",
      label: "Discover A2A agents",
      description:
        "Discover currently reachable A2A agents. Call this before delegating when you do not already have a fresh catalog.",
      parameters: Type.Object({}, { additionalProperties: false }),
      optional: true,
      async execute(_params, config: PluginConfig) {
        const agents = await discover(config);
        return {
          agents: agents.map((item) => ({
            agentId: item.agentId,
            name: item.agentCard.name,
            description: item.agentCard.description,
            skills: item.agentCard.skills ?? [],
            cardRevision: item.cardRevision,
          })),
        };
      },
    }),
    tool({
      name: "a2a_send",
      label: "Send A2A message",
      description:
        "Submit one text task exactly once and register push delivery back to the current manager session. Return after registration; the manager is automatically reawakened with the final artifact.",
      parameters: sendParameters,
      optional: true,
      factory: ({ api, config, toolContext }) => {
        return {
          name: "a2a_send",
          label: "Send A2A message",
          description:
            "Submit one text task exactly once and register push delivery back to the current manager session. Return after registration; the manager is automatically reawakened with the final artifact.",
          parameters: sendParameters,
          execute: async (_toolCallId: string, params: any) => {
            const { agent, message, contextId } = params;
            if (!toolContext.sessionKey) {
              throw new Error("The current OpenClaw manager session has no sessionKey for push delivery.");
            }
            const { card, endpoint } = await resolveAgent(config, agent);
            const result = await jsonRpc(
              endpoint,
              "SendMessage",
              {
                message: {
                  messageId: crypto.randomUUID(),
                  contextId: contextId ?? crypto.randomUUID(),
                  role: "ROLE_USER",
                  parts: [{ text: message }],
                },
                configuration: {
                  acceptedOutputModes: ["text/plain"],
                  returnImmediately: true,
                },
              },
              config,
            );
            if (result?.message) {
              return jsonResult({
                agent: card.name,
                state: "MESSAGE",
                terminal: true,
                text: messageText(result.message),
                nextAction: "Interpret the message and reply to the user.",
              });
            }
            const task = result?.task;
            if (!task?.id) {
              throw new Error(`Agent ${agent} did not return an A2A task or message.`);
            }
            if (TERMINAL_TASK_STATES.has(task?.status?.state)) {
              return jsonResult({
                ...taskResult(card, task),
                pushRegistered: false,
              });
            }
            pendingTasks.set(task.id, {
              api,
              card,
              endpoint,
              config,
              sessionKey: toolContext.sessionKey,
              agentId: toolContext.agentId,
              deliveryContext: toolContext.deliveryContext,
            });
            try {
              const supportsPush = card.capabilities?.pushNotifications === true;
              if (supportsPush) {
                await jsonRpc(
                  endpoint,
                  "CreateTaskPushNotificationConfig",
                  {
                    taskId: task.id,
                    url: config.callbackUrl ?? DEFAULT_CALLBACK_URL,
                    token: pushToken,
                  },
                  config,
                );
                await scheduleManagerCompletion(task.id);
              } else {
                void pollManagerCompletion(task.id);
              }
              return jsonResult({
                ...taskResult(card, task),
                pushRegistered: supportsPush,
                polling: !supportsPush,
              });
            } catch (error) {
              pendingTasks.delete(task.id);
              throw error;
            }
          },
        };
      },
    }),
    tool({
      name: "a2a_get",
      label: "Get A2A task",
      description:
        "Recover an existing A2A task after an exceptional client wait timeout. Reuse its taskId and never resubmit the original task.",
      parameters: Type.Object(
        {
          agent: Type.String({ description: "Exact agentId used for a2a_send." }),
          taskId: Type.String({ description: "Task ID returned by a2a_send." }),
          waitMs: Type.Optional(
            Type.Number({
              minimum: 0,
              maximum: 110000,
              default: 60000,
              description: "Maximum time to poll for a terminal state.",
            }),
          ),
        },
        { additionalProperties: false },
      ),
      optional: true,
      async execute({ agent, taskId, waitMs }, config: PluginConfig) {
        const { card, endpoint } = await resolveAgent(config, agent);
        const deadline = Date.now() + (waitMs ?? 60_000);
        let task: any;
        do {
          task = await jsonRpc(endpoint, "GetTask", { id: taskId }, config);
          if (TERMINAL_TASK_STATES.has(task?.status?.state) || Date.now() >= deadline) {
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 2_000));
        } while (true);
        return taskResult(card, task);
      },
    }),
  ],
});

const registerTools = entry.register;
entry.register = (api) => {
  api.registerHttpRoute({
    path: CALLBACK_PATH,
    auth: "plugin",
    match: "exact",
    replaceExisting: true,
    handler: handlePushCallback,
  });
  registerTools?.(api);
};

export default entry;
