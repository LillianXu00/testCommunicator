# Manager A2A Client

OpenClaw tool plugin used by the `main` Manager agent.

- `a2a_discover` reads active Agent Cards from the Registry.
- `a2a_send` fetches only the selected Agent Card and sends one A2A task.
- `a2a_get` recovers an existing task without resubmitting the work.

The plugin follows the JSON-RPC interface in the selected Agent Card. Managed
agents normally advertise a unique URL such as:

```text
http://127.0.0.1:4101/a2a/agents/planner
```

Native provider Cards may point to any reachable A2A JSON-RPC service. If an
interface advertises a `tenant`, the plugin echoes that opaque value; managed
Gateway Cards do not require one.

For non-terminal tasks, the plugin registers push notification delivery when
the Card declares `pushNotifications`. Otherwise it polls `GetTask`. A terminal
artifact wakes the original OpenClaw `main` session, where the Manager
summarizes the specialist result for the user.
