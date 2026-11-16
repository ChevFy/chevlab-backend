"""AC-7 / FR-21/22 — every planner route is private.

Unauthenticated requests (no bearer) must be rejected before any handler logic,
and no data returned. With a valid ``get_current_user`` override they pass the
gate (exercised throughout the rest of the suite).
"""

import pytest

# (method, path) for each of the 9 endpoints. Bodies are irrelevant: the auth
# gate runs before body validation at the router-dependency level.
ROUTES = [
    ("post", "/api/planner/todos"),
    ("post", "/api/planner/todos/parse"),
    ("get", "/api/planner/todos"),
    ("get", "/api/planner/todos/1"),
    ("patch", "/api/planner/todos/1"),
    ("delete", "/api/planner/todos/1"),
    ("get", "/api/planner/events?from=2026-11-15T00:00:00Z&to=2026-11-16T00:00:00Z"),
    ("get", "/api/planner/reminders"),
    ("post", "/api/planner/reminders/1/ack"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_unauthenticated_rejected(unauth_client, method, path):
    resp = unauth_client.request(method, path, json={})
    # HTTPBearer(auto_error=False) + explicit 401 in get_current_user → 401.
    assert resp.status_code in (401, 403), (method, path, resp.status_code)
    # No resource payload leaks through the gate.
    body = resp.json()
    assert "detail" in body
    assert "items" not in body and "todo" not in body


@pytest.mark.parametrize("method,path", ROUTES)
def test_authenticated_passes_gate(client, method, path):
    # With the override the gate is satisfied; we only assert it is NOT a 401/403
    # (the handler may still 404/422 depending on the route — that's fine).
    resp = client.request(method, path, json={})
    assert resp.status_code not in (401, 403), (method, path, resp.status_code)
