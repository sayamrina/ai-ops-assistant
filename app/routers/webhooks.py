import json

from fastapi import APIRouter, Depends, HTTPException

from ..security import verify_webhook_signature
from ..services import Services, get_services
from .api import Ticket, run_triage

router = APIRouter(prefix="/webhooks", tags=["integrations"])


@router.post("/jira")
def jira_issue_created(body: bytes = Depends(verify_webhook_signature), svc: Services = Depends(get_services)):
    """Jira Cloud webhook (event jira:issue_created). Signed with HMAC-SHA256; retries are deduplicated."""
    event = json.loads(body)
    issue = event.get("issue") or {}
    key = issue.get("key")
    if not key:
        raise HTTPException(422, "Payload has no issue key")
    event_id = f"{event.get('webhookEvent', 'event')}:{key}:{event.get('timestamp', '')}"
    if not svc.store.first_time(event_id):
        return {"status": "duplicate", "event_id": event_id}
    fields = issue.get("fields") or {}
    description = fields.get("description") or ""
    if isinstance(description, dict):  # Atlassian Document Format → plain text
        description = " ".join(node.get("text", "") for block in description.get("content", [])
                               for node in block.get("content", []))
    ticket = Ticket(key=key, summary=fields.get("summary", "(no summary)"), description=description, notify=True)
    return {"status": "processed"} | run_triage(ticket, "jira-webhook", svc)
