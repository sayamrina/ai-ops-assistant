from app.privacy import redact


def test_removes_direct_identifiers():
    text = ("From: lena.h@mail.example\nCall +49 170 5550192, IBAN DE44 5001 0517 5407 3249 31.\n"
            "Mehmet Yilmaz, born 12.03.1979")
    result = redact(text)
    for secret in ["lena.h@mail.example", "5550192", "DE44", "12.03.1979", "Yilmaz"]:
        assert secret not in result.text
    assert result.counts == {"EMAIL": 1, "PHONE": 1, "IBAN": 1, "BIRTH_DATE": 1, "NAME": 1}


def test_keeps_business_data():
    text = "Invoice date: 2026-10-01\nCase CT-20931\nTotal 2,454.26 EUR\nViele Grüße\nDr. Tobias Wendt"
    result = redact(text)
    assert "2026-10-01" in result.text and "CT-20931" in result.text and "2,454.26" in result.text
    assert "Viele Grüße" in result.text and "Wendt" not in result.text
