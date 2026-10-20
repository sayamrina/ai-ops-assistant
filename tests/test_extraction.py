from datetime import date

from app.extraction import add_months, process_document

INVOICE = "INVOICE NimbusHost GmbH Invoice number: NH-1 Invoice date: 2026-10-01 Total 2,454.26 EUR"


def test_valid_invoice_is_processed_automatically(llm):
    llm.responses["classify_document"] = {"doc_type": "invoice", "confidence": 0.97}
    llm.responses["extract_invoice"] = {"vendor": "NimbusHost GmbH", "invoice_number": "NH-1", "invoice_date": "2026-10-01",
                                        "due_date": "2026-10-31", "total_amount": 2454.26, "currency": "EUR", "confidence": 0.95}
    result = process_document("inv.txt", INVOICE, llm, threshold=0.8)
    assert result.doc_type == "invoice" and not result.needs_review
    assert result.fields["total_amount"] == 2454.26


def test_invalid_fields_go_to_review(llm):
    llm.responses["classify_document"] = {"doc_type": "support_request", "confidence": 0.9}
    llm.responses["extract_support_request"] = {"request_type": "refund", "case_id": "12345", "urgency": "normal",
                                                "summary": "Refund", "confidence": 0.9}
    result = process_document("mail.txt", "refund for case 12345", llm, threshold=0.8)
    assert result.needs_review and "validation failed" in result.review_reasons[0]


def test_gdpr_deadline_is_computed_and_always_reviewed(llm):
    llm.responses["classify_document"] = {"doc_type": "data_subject_request", "confidence": 0.99}
    llm.responses["extract_data_subject_request"] = {"request_article": "17", "received_date": "2026-01-31", "confidence": 0.99}
    result = process_document("dsr.txt", "Please delete my data", llm, threshold=0.8)
    assert result.fields["answer_deadline"] == "2026-02-28"
    assert result.needs_review


def test_add_months_handles_month_end():
    assert add_months(date(2026, 10, 14), 1) == date(2026, 11, 14)
    assert add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)
