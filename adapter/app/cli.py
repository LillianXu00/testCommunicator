from __future__ import annotations

import asyncio
import json

import typer

from adapter.app.config import get_settings
from adapter.app.humhub_client import HumHubClient
from adapter.app.models import SyncUserRequest
from adapter.app.services.user_service import UserService

app = typer.Typer(help="OpenClaw to HumHub adapter CLI")


@app.command("sync-user")
def sync_user(
    external_id: str = typer.Option(..., help="OpenClaw external identity primary key"),
    username: str = typer.Option(..., help="HumHub username"),
    display_name: str = typer.Option(..., help="HumHub display name"),
    email: str | None = typer.Option(None, help="HumHub email or placeholder if omitted"),
    account_type: str = typer.Option("user", help="OpenClaw actor type: user/claw/agent"),
    first_name: str | None = typer.Option(None, help="Profile first name"),
    last_name: str | None = typer.Option(None, help="Profile last name"),
    password: str | None = typer.Option(None, help="Optional initial password"),
    must_change_password: bool = typer.Option(False, help="Require password reset on first HumHub login"),
    language: str | None = typer.Option("en-US", help="HumHub language"),
    visibility: int = typer.Option(1, help="HumHub visibility"),
    status: int = typer.Option(1, help="HumHub status"),
    tag: list[str] = typer.Option([], "--tag", help="Repeatable profile tags"),
) -> None:
    async def _run() -> None:
        settings = get_settings()
        client = HumHubClient(settings.humhub_base_url, settings.humhub_api_token)
        service = UserService(humhub_client=client, settings=settings)
        result = await service.sync_user(
            SyncUserRequest(
                external_id=external_id,
                username=username,
                display_name=display_name,
                email=email,
                account_type=account_type,
                first_name=first_name,
                last_name=last_name,
                password=password,
                must_change_password=must_change_password,
                language=language,
                visibility=visibility,
                status=status,
                tags=tag,
            )
        )
        typer.echo(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))

    asyncio.run(_run())


if __name__ == "__main__":
    app()
