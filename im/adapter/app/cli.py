import json
import os
import typer
import httpx

app = typer.Typer(help='imctl - OpenClaw friendly CLI for HumHub adapter')


def _base_url() -> str:
    return os.environ.get('IMCTL_BASE_URL', f"http://127.0.0.1:{os.environ.get('ADAPTER_INTERNAL_PORT', '8000')}")


def _headers() -> dict[str, str]:
    token = os.environ['ADAPTER_BEARER_TOKEN']
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def _print_response(response: httpx.Response) -> None:
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2, ensure_ascii=False))


@app.command('health')
def health() -> None:
    with httpx.Client(timeout=20) as client:
        _print_response(client.get(f'{_base_url()}/healthz'))


users_app = typer.Typer(help='User commands')
feed_app = typer.Typer(help='Feed commands')
dm_app = typer.Typer(help='DM commands')
app.add_typer(users_app, name='users')
app.add_typer(feed_app, name='feed')
app.add_typer(dm_app, name='dm')


@users_app.command('sync')
def users_sync(external_id: str, username: str, email: str, display_name: str = '') -> None:
    payload = {'external_id': external_id, 'username': username, 'email': email, 'display_name': display_name or None}
    with httpx.Client(timeout=20, headers=_headers()) as client:
        _print_response(client.post(f'{_base_url()}/v1/users/sync', json=payload))


@feed_app.command('list')
def feed_list(limit: int = 20) -> None:
    with httpx.Client(timeout=20, headers=_headers()) as client:
        _print_response(client.get(f'{_base_url()}/v1/feed', params={'limit': limit}))


@feed_app.command('post')
def feed_post(message: str, space_id: int = typer.Option(0)) -> None:
    payload = {'message': message, 'space_id': space_id or None}
    with httpx.Client(timeout=20, headers=_headers()) as client:
        _print_response(client.post(f'{_base_url()}/v1/feed/post', json=payload))


@dm_app.command('send')
def dm_send(message: str, thread_id: int = typer.Option(0), recipient_user_ids: str = typer.Option('')) -> None:
    ids = [int(item) for item in recipient_user_ids.split(',') if item.strip()]
    payload = {'message': message, 'thread_id': thread_id or None, 'recipient_user_ids': ids}
    with httpx.Client(timeout=20, headers=_headers()) as client:
        _print_response(client.post(f'{_base_url()}/v1/dm/send', json=payload))


@dm_app.command('threads')
def dm_threads() -> None:
    with httpx.Client(timeout=20, headers=_headers()) as client:
        _print_response(client.get(f'{_base_url()}/v1/dm/threads'))


@dm_app.command('read')
def dm_read(thread_id: int) -> None:
    with httpx.Client(timeout=20, headers=_headers()) as client:
        _print_response(client.get(f'{_base_url()}/v1/dm/threads/{thread_id}'))


if __name__ == '__main__':
    app()
