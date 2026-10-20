# Evaluation (openai: gpt-4o-mini, text-embedding-3-small)

| Pipeline | Metric | Result |
|---|---|---|
| Knowledge search | Right document in top 1 / top 3 (18 questions) | 94% / 100% |
| Knowledge search | Answered and cited the right document | 100% |
| Knowledge search | Refused questions outside the documents (2) | 100% |
| Document intake | Document type correct (12 documents) | 100% |
| Document intake | Extracted fields correct (44 fields) | 100% |
| Document intake | Sent to human review | 3 of 12 |
| Ticket triage | Category / priority correct (8 tickets) | 100% / 100% |
| Ticket triage | All sensitive tickets routed to a human | yes |
