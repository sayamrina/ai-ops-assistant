"""Pseudonymisation before any text reaches an LLM (data protection by design).

Rule-based and conservative: it covers the identifiers listed in the internal data protection policy.
A production system would add a NER model (for example Microsoft Presidio) to catch more names.
"""
import re
from dataclasses import dataclass, field

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d{2,3}[ /-]?\d{2,4}[ /-]?\d{5,8}\b")),
    ("INSURANCE_NO", re.compile(r"\b[A-Z]\d{9}\b")),
    # Dates count as personal data only next to a birth keyword; invoice and request dates stay readable
    ("BIRTH_DATE", re.compile(r"(?i)(?<=born )\d{1,2}\.\d{1,2}\.\d{4}|(?<=birth )\d{1,2}\.\d{1,2}\.\d{4}"
                              r"|(?<=geb\. )\d{1,2}\.\d{1,2}\.\d{4}|(?<=to )\d{1,2}\.\d{1,2}\.\d{4}(?= according)")),
    # Names after a title, an owner marker ("Inh.") or a sign-off phrase
    ("NAME", re.compile(r"(?<=Dr\. )[A-ZÄÖÜ][a-zäöüß]+(?: [A-ZÄÖÜ][a-zäöüß]+)?")),
    ("NAME", re.compile(r"(?<=Inh\. )[A-ZÄÖÜ][a-zäöüß]+ [A-ZÄÖÜ][a-zäöüß]+")),
    ("NAME", re.compile(r"(?<=Thanks, )[A-ZÄÖÜ][a-zäöüß]+(?: [A-ZÄÖÜ][a-zäöüß]+)?")),
    ("NAME", re.compile(r"(?<=regards, )[A-ZÄÖÜ][a-zäöüß]+ [A-ZÄÖÜ][a-zäöüß]+")),
]


@dataclass
class Redaction:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


# A signature line: "First Last" at the end of the text or followed by more personal details
SIGNATURE = re.compile(r"(?m)^([A-ZÄÖÜ][a-zäöüß]+ [A-ZÄÖÜ][a-zäöüß]+)(?=,|\s*\Z)")
NOT_NAMES = {"Viele Grüße", "Kind regards", "Best regards", "Mit Grüßen", "Thank you", "Total due"}


def redact(text: str) -> Redaction:
    counts: dict[str, int] = {}

    def count(label: str) -> str:
        counts[label] = counts.get(label, 0) + 1
        return f"<{label}>"

    for label, pattern in PATTERNS:
        text = pattern.sub(lambda _m, label=label: count(label), text)
    text = SIGNATURE.sub(lambda m: m.group(0) if m.group(1) in NOT_NAMES else count("NAME"), text.rstrip()) + "\n"
    return Redaction(text=text, counts=counts)
