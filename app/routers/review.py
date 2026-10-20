from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..security import Principal, require_role
from ..services import Services, get_services

router = APIRouter(prefix="/api/review", tags=["human review"])


class Decision(BaseModel):
    approve: bool
    corrected_result: dict | None = None
    comment: str = ""


@router.get("")
def open_items(status: str = "open", who: Principal = Depends(require_role("reviewer")),
               svc: Services = Depends(get_services)):
    return svc.store.list_reviews(status)


@router.post("/{item_id}")
def decide(item_id: int, decision: Decision, who: Principal = Depends(require_role("reviewer")),
           svc: Services = Depends(get_services)):
    item = svc.store.get_review(item_id)
    if not item:
        raise HTTPException(404, "Review item not found")
    if item["status"] != "open":
        raise HTTPException(409, "Item was already decided")
    final = decision.corrected_result or item["ai_result"]
    status = "approved" if decision.approve else "rejected"
    svc.store.decide(item_id, status, who.name, final if decision.approve else None)
    svc.store.audit(who.name, f"review_{status}", item["reference"], review_id=item_id,
                    corrected=decision.corrected_result is not None, comment=decision.comment)
    return {"id": item_id, "status": status}


@router.get("/audit")
def audit_log(limit: int = 100, who: Principal = Depends(require_role("admin")), svc: Services = Depends(get_services)):
    return svc.store.audit_entries(min(limit, 500))
