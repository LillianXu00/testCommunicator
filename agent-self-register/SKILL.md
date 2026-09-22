---
name: agent-self-register
description: Register or synchronize an agent and its explicitly exposed capabilities with the A2A Registry. Use when an agent starts, changes its approved skills or endpoint contract, needs native A2A discovery, needs an existing Adapter matched, or needs a declarative HTTP Adapter mapping generated and validated.
---

# Agent Self Register

Register the current agent through the Registry onboarding API. Treat developer
configuration as the source of truth; never claim general model abilities that
were not explicitly approved for exposure.

## Workflow

1. Open the Skill-root `.env` file and fill the Registry URL and only the
   credentials required by this deployment. Never copy these secrets into the
   manifest.
2. Read the agent's stable ID, owner, version and approved exposed skills from
   its deployment configuration or developer instructions.
3. Read [manifest.md](references/manifest.md) and create a JSON manifest.
4. Prefer `sourceAgentCardUrl` for an agent that already exposes native A2A.
5. For OpenClaw Responses, use `interface.kind: openclaw-responses`.
6. For synchronous JSON HTTP agents, use `interface.kind: declarative-http`, a
   request body template and response selectors.
7. When response selectors are unknown, include a safe, side-effect-free
   `testCase.input`; Registry may probe the endpoint and infer one selector.
8. Never persist credentials in the manifest file. Put a bearer-protected
   endpoint token in `AGENT_ENDPOINT_TOKEN` inside `.env`. The helper injects
   it into the registration request as a write-only field; Registry encrypts
   it and never returns it.
9. Validate locally, then register. The helper automatically loads `.env` from
   the Skill root regardless of the current working directory:

```bash
python scripts/register_agent.py path/to/manifest.json --dry-run
python scripts/register_agent.py path/to/manifest.json
```

The `.env` fields are:

- `A2A_REGISTRY_URL`: Registry base URL reachable from this agent.
- `REGISTRY_REGISTRATION_TOKEN`: credential for calling the Registry's
  self-registration API; leave empty when that API has no authentication.
- `AGENT_ENDPOINT_TOKEN`: credential the Gateway must later use to call this
  agent; leave empty when the agent endpoint has no bearer authentication.

Do not confuse the two tokens. Existing process environment variables override
values from `.env`. Use `--env-file path/to/.env` only when a deployment keeps
its environment file elsewhere.

## Interpret Results

- `NATIVE_A2A`: Registry validated the provider Agent Card; no Adapter is used.
- `EXISTING_ADAPTER`: Registry matched a platform Adapter.
- `GENERATED_DECLARATIVE_ADAPTER`: Registry generated and stored a hot-loaded
  HTTP mapping; no Python code or restart is required.
- `ADAPTER_REQUIRED`: The contract needs a new reviewed code Adapter. Report
  the returned request ID and stop; do not write executable code into Registry.

Only report registration success when the response status is `ACTIVE`. Preserve
the returned `revision`, `cardRevision`, `cardUrl` and `a2aUrl`.

## Update Behavior

Run the same registration again on startup or approved capability changes.
Registry treats an unchanged manifest as idempotent. A changed definition
creates a new revision while keeping the same `agentId`.
