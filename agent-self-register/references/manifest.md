# Self-registration Manifest

The manifest declares the capabilities intentionally exposed to the Manager and
the technical contract required to invoke the agent.

## Common fields

```json
{
  "agentId": "stable-lowercase-id",
  "owner": "team-or-contact",
  "name": "Human-readable name",
  "description": "Narrow responsibility in the multi-agent system",
  "version": "1.0.0",
  "skills": [
    {
      "id": "stable-skill-id",
      "name": "Skill name",
      "description": "When the Manager should delegate to this skill",
      "examples": ["Example user task"],
      "inputModes": ["text/plain"],
      "outputModes": ["text/plain"]
    }
  ]
}
```

Do not infer skills from the base model. Include only developer-approved
responsibilities.

## Native A2A

```json
{
  "sourceAgentCardUrl": "https://agent.example.com/.well-known/agent-card.json"
}
```

Registry imports the authoritative Card and ignores the local capability text.

## OpenClaw Responses

```json
{
  "interface": {
    "kind": "openclaw-responses",
    "endpoint": "http://127.0.0.1:18789",
    "agentId": "planner",
    "authEnv": "OPENCLAW_GATEWAY_TOKEN",
    "timeoutSeconds": 600
  }
}
```

## Synchronous JSON HTTP

Templates support `{{input}}` and `{{contextId}}` at any depth:

```json
{
  "interface": {
    "kind": "declarative-http",
    "endpoint": "https://agent.example.com/invoke",
    "method": "POST",
    "executionMode": "sync",
    "request": {
      "body": {
        "query": "{{input}}",
        "session_id": "{{contextId}}"
      }
    },
    "response": {
      "textSelectors": ["$.data.answer"]
    }
  }
}
```

For a bearer-protected endpoint, do not write the token into this file. Put it
in the Skill-root `.env` file:

```dotenv
AGENT_ENDPOINT_TOKEN=provider-agent-token
```

The helper sends the equivalent write-only registration fragment:

```json
{
  "interface": {
    "authentication": {
      "type": "bearer",
      "token": "write-only-value"
    }
  }
}
```

Registry replaces the token with a unique reference such as
`agent:insight-agent:bearer`, encrypts the value in its secret table, and never
returns the plaintext through catalog or administration APIs. `authEnv` remains
available for platform-owned credentials that are already injected into the
Gateway environment.

Selectors support `$`, dotted object paths and fixed array indexes, such as
`$.choices[0].message.content`.

If the selector is unknown, omit `response.textSelectors` and provide:

```json
{
  "testCase": {
    "input": "A safe test task with no external side effects"
  }
}
```

The first version can infer a candidate text selector from one successful JSON
response. Complex asynchronous, streaming, file or signed protocols return
`ADAPTER_REQUIRED` and require a reviewed Adapter implementation.
