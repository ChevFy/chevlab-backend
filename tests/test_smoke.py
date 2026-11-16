def test_structured_create_smoke(client):
    resp = client.post("/api/planner/todos", json={"title": "buy milk"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["todo"]["title"] == "buy milk"
    assert body["event"] is None
    assert body["reminders"] == []
