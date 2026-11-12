"""Two credentials, one gate: the browser's cookie and the local client's token.

The gate itself has not changed — `/api/*` and `/events` still need a credential
and the login page is still public. What this file adds is the second credential:
a token a local client reads from the endpoint file, sent as
``Authorization: Bearer``. The cookie keeps serving the browser, and the token is
never a password: holding it does not log anyone into the web client.

The token is a secret, so one test asserts it is not echoed back in a refusal.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.auth import Auth, AuthMiddleware
from src.server.config import ServerConfig
from src.server.endpoint import EndpointPublisher
from src.server.events import EventHub

PASSWORD = 'secret-pass'
TOKEN = 'per-run-token-that-no-log-line-should-carry'
BEARER = {'Authorization': f'Bearer {TOKEN}'}


def _client(auth: Auth) -> TestClient:
    """A real FastAPI app behind the real middleware, with no engine behind either."""

    app = FastAPI()
    app.add_middleware(AuthMiddleware, auth=auth)

    @app.get('/api/state')
    async def state() -> dict[str, bool]:
        return {'ok': True}

    @app.get('/events')
    async def events() -> dict[str, bool]:
        return {'ok': True}

    @app.get('/health')
    async def health() -> dict[str, bool]:
        return {'ok': True}

    @app.post('/api/login')
    async def login() -> dict[str, bool]:
        return {'ok': True}

    return TestClient(app)


def _auth(*, token: str | None = TOKEN) -> Auth:
    return Auth(PASSWORD, service_token=token)


def test_a_bearer_token_opens_a_protected_path() -> None:
    assert _client(_auth()).get('/api/state', headers=BEARER).status_code == 200


def test_no_credential_is_still_a_401() -> None:
    assert _client(_auth()).get('/api/state').status_code == 401


def test_a_wrong_bearer_token_is_a_401() -> None:
    response = _client(_auth()).get('/api/state', headers={'Authorization': 'Bearer not-the-token'})

    assert response.status_code == 401


def test_the_refusal_never_repeats_the_token() -> None:
    """An error message is a log line somewhere; the secret does not belong in it."""

    response = _client(_auth()).get('/api/state', headers={'Authorization': f'Bearer {TOKEN}x'})

    assert response.status_code == 401
    assert TOKEN not in response.text
    assert response.json() == {'error': 'unauthenticated'}


def test_another_scheme_is_not_the_token() -> None:
    client = _client(_auth())

    assert client.get('/api/state', headers={'Authorization': f'Basic {TOKEN}'}).status_code == 401
    assert client.get('/api/state', headers={'Authorization': TOKEN}).status_code == 401
    assert client.get('/api/state', headers={'Authorization': 'Bearer '}).status_code == 401


def test_a_service_without_a_token_accepts_no_bearer() -> None:
    """Unset is not "any token works": there is nothing to compare against."""

    assert _client(_auth(token=None)).get('/api/state', headers=BEARER).status_code == 401


def test_the_token_is_not_a_password() -> None:
    """Reading the file must not become a way into the web client."""

    assert _auth().check_password(TOKEN) is False


def _logged_in(auth: Auth) -> TestClient:
    """A client holding the cookie a browser would keep after `/api/login`."""

    client = _client(auth)
    client.cookies.set(auth.cookie_name, auth.issue())
    return client


def test_the_cookie_still_works() -> None:
    assert _logged_in(_auth()).get('/api/state').status_code == 200


def test_events_needs_a_credential_and_accepts_both() -> None:
    auth = _auth()

    assert _client(auth).get('/events').status_code == 401
    assert _client(auth).get('/events', headers=BEARER).status_code == 200
    assert _logged_in(auth).get('/events').status_code == 200


def test_the_public_paths_stay_public() -> None:
    client = _client(_auth())

    assert client.get('/health').status_code == 200
    assert client.post('/api/login').status_code == 200


class _RuntimeStub:
    """`create_app` reads the hub once; nothing in this file reaches a route with state."""

    def __init__(self) -> None:
        self.hub = EventHub()


def _app_client(tmp_path: Path, publisher: EndpointPublisher) -> TestClient:
    from src.server.app import create_app

    app = create_app(
        _RuntimeStub(),  # type: ignore[arg-type] - the auth boundary is the subject here
        ServerConfig(password=PASSWORD),
        dist=tmp_path / 'missing',
        publisher=publisher,
    )
    return TestClient(app)


def test_the_app_accepts_the_token_its_endpoint_publishes(tmp_path: Path) -> None:
    """The wiring, end to end through the real app: one publisher, one accepted token."""

    publisher = EndpointPublisher(path=tmp_path / 'service' / 'endpoint.json')
    client = _app_client(tmp_path, publisher)

    assert client.post('/api/logout').status_code == 401
    assert client.post('/api/logout', headers={'Authorization': 'Bearer nope'}).status_code == 401
    assert client.post('/api/logout', headers={'Authorization': f'Bearer {publisher.token}'}).status_code == 200


def test_the_web_login_keeps_working_next_to_the_token(tmp_path: Path) -> None:
    publisher = EndpointPublisher(path=tmp_path / 'service' / 'endpoint.json')
    client = _app_client(tmp_path, publisher)

    assert client.post('/api/login', json={'password': publisher.token}).status_code == 401
    login = client.post('/api/login', json={'password': PASSWORD})
    assert login.status_code == 200
    assert client.post('/api/logout').status_code == 200
