"""Whole-app tests: card discovery, auth, and a streamed task over real HTTP."""

import json

from starlette.testclient import TestClient

from app.auth import API_KEY_HEADER
from app.main import build_app
from app.settings import load_settings

CARD_PATH = "/.well-known/agent-card.json"
STREAM_BODY = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendStreamingMessage",
    "params": {
        "message": {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "What changed this year?"}],
        }
    },
}
# Required by A2A 1.0. Without it the server assumes 0.3 and rejects the call.
VERSION_HEADERS = {"A2A-Version": "1.0", "Accept": "text/event-stream"}


def anonymous_client() -> TestClient:
    return TestClient(build_app(load_settings({"A2A_ALLOW_ANONYMOUS": "1"})))


def authenticated_client() -> TestClient:
    return TestClient(build_app(load_settings({"A2A_API_KEY": "right-key"})))


def test_card_is_served_at_the_well_known_path():
    card = anonymous_client().get(CARD_PATH).json()

    assert card["name"] == "Research Specialist"
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["id"] == "web-research"


def test_card_advertises_a_jsonrpc_interface():
    card = anonymous_client().get(CARD_PATH).json()
    interface = card["supportedInterfaces"][0]

    assert interface["protocolBinding"] == "JSONRPC"
    assert interface["protocolVersion"] == "1.0"


def test_card_declares_the_api_key_scheme_when_auth_is_on():
    card = authenticated_client().get(CARD_PATH).json()

    assert card["securitySchemes"]["apiKey"]["apiKeySecurityScheme"]["name"] == API_KEY_HEADER


def test_card_is_reachable_without_a_key_so_clients_can_discover_it():
    # Discovery has to work before a client knows what to authenticate with.
    assert authenticated_client().get(CARD_PATH).status_code == 200


def test_rpc_requires_the_api_key():
    response = authenticated_client().post("/", json=STREAM_BODY, headers=VERSION_HEADERS)

    assert response.status_code == 401


def test_rpc_rejects_a_wrong_api_key():
    response = authenticated_client().post(
        "/", json=STREAM_BODY, headers={**VERSION_HEADERS, API_KEY_HEADER: "wrong"}
    )

    assert response.status_code == 401


def test_rpc_accepts_the_right_api_key():
    response = authenticated_client().post(
        "/", json=STREAM_BODY, headers={**VERSION_HEADERS, API_KEY_HEADER: "right-key"}
    )

    assert response.status_code == 200


def test_missing_version_header_is_rejected():
    # Documented 1.0 behaviour: absent the header the server assumes 0.3.
    response = anonymous_client().post(
        "/", json=STREAM_BODY, headers={"Accept": "text/event-stream"}
    )

    assert response.json()["error"]["code"] == -32009


def test_stream_runs_the_full_task_lifecycle():
    response = anonymous_client().post("/", json=STREAM_BODY, headers=VERSION_HEADERS)
    frames = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert "task" in frames[0]["result"], "the Task must be the first frame"
    assert any("artifactUpdate" in f["result"] for f in frames)
    assert frames[-1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_healthz():
    assert anonymous_client().get("/healthz").json() == {"status": "ok"}
