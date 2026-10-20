import io
import json

from app.security import sign

from .conftest import WEBHOOK_SECRET, auth


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready").json()
    assert ready["chunks"] > 20 and ready["db"] is True


def test_authentication_and_roles(client):
    assert client.post("/api/ask", json={"question": "MFA reset?"}).status_code == 401
    assert client.post("/api/ask", json={"question": "MFA reset?"}, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/api/triage", json={"key": "ITOPS-1", "summary": "Help me"}, headers=auth("viewer")).status_code == 403
    assert client.get("/api/review", headers=auth("operator")).status_code == 403


def test_ask_returns_sources_and_is_audited(client):
    r = client.post("/api/ask", json={"question": "How long must a password be?"}, headers=auth("viewer"))
    assert r.status_code == 200 and r.json()["sources"]
    audit = client.get("/api/review/audit", headers=auth("admin")).json()
    assert audit[0]["action"] == "ask" and audit[0]["actor"] == "viewer-user"


def test_document_review_flow(client, llm):
    llm.responses["classify_document"] = {"doc_type": "invoice", "confidence": 0.5}
    llm.responses["extract_invoice"] = {"vendor": "A", "invoice_number": "1", "invoice_date": "2026-10-01",
                                        "total_amount": 10, "currency": "EUR", "confidence": 0.5}
    files = {"file": ("inv.txt", io.BytesIO(b"Invoice 1 from A, 10 EUR"), "text/plain")}
    doc = client.post("/api/documents", files=files, headers=auth("operator")).json()
    assert doc["needs_review"] and doc["review_id"]
    queue = client.get("/api/review", headers=auth("reviewer")).json()
    assert queue[0]["reference"] == "inv.txt"
    corrected = queue[0]["ai_result"] | {"fields": {"total_amount": 11.9}}
    decided = client.post(f"/api/review/{doc['review_id']}", json={"approve": True, "corrected_result": corrected},
                          headers=auth("reviewer"))
    assert decided.json()["status"] == "approved"
    assert client.post(f"/api/review/{doc['review_id']}", json={"approve": False}, headers=auth("reviewer")).status_code == 409


def test_jira_webhook_signature_and_idempotency(client):
    payload = json.dumps({"webhookEvent": "jira:issue_created", "timestamp": 1760000000,
                          "issue": {"key": "ITOPS-9", "fields": {"summary": "MFA reset", "description": "New phone, need MFA reset"}}}).encode()
    bad = client.post("/webhooks/jira", content=payload, headers={"X-Hub-Signature": "sha256=00"})
    assert bad.status_code == 401
    headers = {"X-Hub-Signature": sign(payload, WEBHOOK_SECRET), "Content-Type": "application/json"}
    first = client.post("/webhooks/jira", content=payload, headers=headers).json()
    assert first["status"] == "processed" and first["ticket"] == "ITOPS-9"
    again = client.post("/webhooks/jira", content=payload, headers=headers).json()
    assert again["status"] == "duplicate"


def test_metrics_are_exposed(client):
    client.get("/health")
    body = client.get("/metrics").text
    assert "http_requests_total" in body
