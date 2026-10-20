"""Document intake: classify, extract structured fields, validate, and route uncertain cases to a human."""
import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from .llm import LLM
from .observability import PIPELINE_RESULTS
from .privacy import redact

DocType = Literal["support_request", "invoice", "doctor_application", "data_subject_request", "other"]


class SupportRequest(BaseModel):
    request_type: Literal["refund", "prescription", "technical", "other"]
    case_id: str | None = None
    urgency: Literal["normal", "high"]
    summary: str

    @field_validator("case_id")
    @classmethod
    def case_format(cls, value):
        if value and not re.fullmatch(r"CT-\d{5}", value):
            raise ValueError("case_id must look like CT-12345")
        return value


class Invoice(BaseModel):
    vendor: str
    invoice_number: str
    invoice_date: date
    due_date: date | None = None
    total_amount: float
    currency: Literal["EUR", "USD", "CHF", "GBP"]


class DoctorApplication(BaseModel):
    licence_present: bool
    specialist_certificate_present: bool
    liability_insurance_present: bool
    missing_documents: list[str] = []


class DataSubjectRequest(BaseModel):
    request_article: Literal["15", "16", "17", "18", "20", "21"]
    received_date: date
    answer_deadline: date | None = None


SCHEMAS = {"support_request": SupportRequest, "invoice": Invoice,
           "doctor_application": DoctorApplication, "data_subject_request": DataSubjectRequest}

CLASSIFY = """TASK: classify_document
Classify an incoming document of a telemedicine company. Types:
- support_request: a patient asks support for help (refund, prescription, technical problem)
- invoice: a bill or receipt from a supplier
- doctor_application: a doctor applies to work with the company
- data_subject_request: a person exercises a GDPR right (access, correction, deletion, ...)
- other: anything else (newsletters, ads, spam)
Personal data has been replaced by placeholders such as <NAME>. Return JSON:
{"doc_type": one of the types, "confidence": number between 0 and 1}"""

EXTRACT = """TASK: extract_{doc_type}
Extract the fields below from the document. Use null when a value is not in the document; never invent values.
Dates as YYYY-MM-DD. Amounts as numbers with a dot as decimal separator (the total including tax).
Fields: {fields}
Also return "confidence" (0 to 1): how sure you are that all fields are correct.
Return JSON with exactly these keys plus "confidence"."""

FIELD_HINTS = {
    "support_request": 'request_type ("refund"|"prescription"|"technical"|"other"), case_id (like CT-12345), '
                       'urgency ("high" if the patient says it is urgent or needs it today, else "normal"), '
                       "summary (one neutral sentence without personal data)",
    "invoice": "vendor (business name only, without owner names or placeholders), invoice_number, invoice_date, "
               "due_date (equal to invoice_date if due on receipt), "
               'total_amount, currency (ISO code, "EUR" for €)',
    "doctor_application": "licence_present (Approbation mentioned as attached), specialist_certificate_present "
                          "(Facharzturkunde attached now, false if it will be sent later), liability_insurance_present",
    "data_subject_request": 'request_article (GDPR article as string: "15" access, "16" correction, "17" deletion), '
                            "received_date (date of the message)",
}


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass
class DocumentResult:
    reference: str
    doc_type: str
    fields: dict
    confidence: float
    needs_review: bool
    review_reasons: list[str] = field(default_factory=list)
    redacted_fields: dict[str, int] = field(default_factory=dict)


def process_document(reference: str, text: str, llm: LLM, threshold: float) -> DocumentResult:
    clean = redact(text)
    reasons: list[str] = []
    classified = llm.chat_json(CLASSIFY, clean.text[:6000])
    doc_type = classified.get("doc_type", "other")
    if doc_type not in SCHEMAS and doc_type != "other":
        doc_type, reasons = "other", ["unknown document type"]
    confidence = float(classified.get("confidence", 0))
    fields: dict = {}
    if doc_type in SCHEMAS:
        raw = llm.chat_json(EXTRACT.format(doc_type=doc_type, fields=FIELD_HINTS[doc_type]), clean.text[:6000])
        confidence = min(confidence, float(raw.pop("confidence", 0)))
        try:
            model = SCHEMAS[doc_type](**raw)
            fields = model.model_dump(mode="json")
        except ValidationError as exc:
            fields = raw
            reasons.append("validation failed: " + "; ".join(e["loc"][0].__str__() + " " + e["msg"] for e in exc.errors()))
        # Business rules are computed in code, not by the model
        if doc_type == "data_subject_request" and fields.get("received_date"):
            fields["answer_deadline"] = add_months(date.fromisoformat(fields["received_date"]), 1).isoformat()
        if doc_type == "doctor_application" and not reasons:
            fields["missing_documents"] = [name for name, key in [("licence", "licence_present"),
                                           ("specialist_certificate", "specialist_certificate_present"),
                                           ("liability_insurance", "liability_insurance_present")] if not fields.get(key)]
    if confidence < threshold:
        reasons.append(f"low confidence ({confidence:.2f})")
    if doc_type == "data_subject_request":
        reasons.append("GDPR request: legal deadline, always checked by a person")
    result = DocumentResult(reference, doc_type, fields, round(confidence, 2), bool(reasons), reasons, clean.counts)
    PIPELINE_RESULTS.labels("document", "review" if result.needs_review else "auto").inc()
    return result
