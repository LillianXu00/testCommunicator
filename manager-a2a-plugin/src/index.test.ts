import { afterEach, describe, expect, it, vi } from "vitest";
import entry from "./index.js";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import { callGatewayFromCli } from "openclaw/plugin-sdk/gateway-runtime";

vi.mock("openclaw/plugin-sdk/gateway-runtime", () => ({
  callGatewayFromCli: vi.fn(async () => ({ status: "ok" })),
}));

describe("manager-a2a-plugin", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("declares discovery and send tools", () => {
    expect(getToolPluginMetadata(entry)?.tools.map((tool) => tool.name)).toEqual([
      "a2a_discover",
      "a2a_send",
      "a2a_get",
    ]);
  });

  it("registers push delivery and reawakens the same manager session", async () => {
    const requests: any[] = [];
    const fetchedUrls: string[] = [];
    let getTaskCount = 0;
    const card = {
      name: "OpenClaw Planner",
      capabilities: {
        pushNotifications: true,
      },
      supportedInterfaces: [
        {
          url: "http://127.0.0.1:4101/a2a/agents/planner",
          protocolBinding: "JSONRPC",
          protocolVersion: "1.0",
        },
      ],
      skills: [],
    };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      fetchedUrls.push(url);
      if (url.includes("/registry/v1/agent-cards")) {
        return new Response(JSON.stringify({
          agents: [{
            agentId: "planner",
            status: "ACTIVE",
            revision: 1,
            cardRevision: "sha256:test",
            cardUrl: "http://127.0.0.1:4101/registry/v1/agents/planner/card",
            agentCard: card,
          }],
        }), { status: 200 });
      }
      if (url.endsWith("/registry/v1/agents/planner/card")) {
        return new Response(JSON.stringify(card), { status: 200 });
      }
      const request = JSON.parse(String(init?.body));
      requests.push(request);
      if (request.method === "SendMessage") {
        return new Response(JSON.stringify({
          jsonrpc: "2.0",
          id: request.id,
          result: {
            task: {
              id: "task-1",
              contextId: "context-1",
              status: { state: "TASK_STATE_WORKING" },
            },
          },
        }), { status: 200 });
      }
      if (request.method === "CreateTaskPushNotificationConfig") {
        return new Response(JSON.stringify({
          jsonrpc: "2.0",
          id: request.id,
          result: request.params,
        }), { status: 200 });
      }
      getTaskCount += 1;
      return new Response(JSON.stringify({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          id: "task-1",
          contextId: "context-1",
          status: {
            state: getTaskCount === 1 ? "TASK_STATE_WORKING" : "TASK_STATE_COMPLETED",
          },
          artifacts: getTaskCount === 1 ? [] : [{ parts: [{ text: "done" }] }],
        },
      }), { status: 200 });
    }));

    const tools: any[] = [];
    const routes: any[] = [];
    const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
    entry.register({
      pluginConfig: {},
      config: { gateway: { auth: { token: "gateway-token" } } },
      logger,
      registerHttpRoute(route: any) {
        routes.push(route);
      },
      registerTool(tool: any) {
        const resolved = typeof tool === "function"
          ? tool({
              sessionKey: "agent:main:main",
              agentId: "main",
              deliveryContext: {
                channel: "feishu",
                to: "chat:oc_test",
                accountId: "default",
              },
            })
          : tool;
        tools.push(...(Array.isArray(resolved) ? resolved : [resolved]));
      },
    } as any);

    const discovered = await tools.find((tool) => tool.name === "a2a_discover").execute(
      "discover-call",
      {},
    );
    expect(JSON.stringify(discovered)).toContain("planner");

    const submitted = await tools.find((tool) => tool.name === "a2a_send").execute(
      "send-call",
      { agent: "planner", message: "make report" },
    );
    expect(JSON.stringify(submitted)).toContain("task-1");
    expect(JSON.stringify(submitted)).toContain("pushRegistered");
    expect(
      fetchedUrls.filter((url) => url.endsWith("/registry/v1/agent-cards")),
    ).toHaveLength(1);
    expect(
      fetchedUrls.filter((url) => url.endsWith("/registry/v1/agents/planner/card")),
    ).toHaveLength(1);
    expect(requests[0].params.configuration.returnImmediately).toBe(true);
    expect(requests.every((request) => request.params.tenant === undefined)).toBe(true);
    expect(requests.map((request) => request.method)).toEqual([
      "SendMessage",
      "CreateTaskPushNotificationConfig",
      "GetTask",
    ]);

    const pushRequest: any = {
      headers: {
        "x-a2a-notification-token": requests[1].params.token,
      },
      async *[Symbol.asyncIterator]() {
        yield JSON.stringify({
          statusUpdate: {
            taskId: "task-1",
            status: { state: "TASK_STATE_COMPLETED" },
            final: true,
          },
        });
      },
    };
    const pushResponse: any = {
      statusCode: 0,
      setHeader: vi.fn(),
      end: vi.fn(),
    };
    await routes[0].handler(pushRequest, pushResponse);

    expect(pushResponse.statusCode).toBe(202);
    await vi.waitFor(() => expect(callGatewayFromCli).toHaveBeenCalledOnce());
    expect(vi.mocked(callGatewayFromCli).mock.calls[0][0]).toBe("agent");
    expect(vi.mocked(callGatewayFromCli).mock.calls[0][2]).toMatchObject({
      sessionKey: "agent:main:main",
      agentId: "main",
      deliver: true,
      replyTo: "chat:oc_test",
      replyChannel: "feishu",
      replyAccountId: "default",
    });
    expect((vi.mocked(callGatewayFromCli).mock.calls[0][2] as any).message).toContain("done");
    expect(requests.map((request) => request.method)).toEqual([
      "SendMessage",
      "CreateTaskPushNotificationConfig",
      "GetTask",
      "GetTask",
    ]);
  });
});
