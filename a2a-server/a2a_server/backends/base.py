from typing import Protocol


class AgentBackend(Protocol):
    """Framework-neutral execution boundary behind an A2A server."""

    async def invoke(self, prompt: str, context_id: str) -> str:
        """Run one agent turn and return its user-visible text."""
        ...
