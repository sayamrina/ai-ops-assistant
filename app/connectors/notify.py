"""Outgoing integrations: Microsoft Teams (incoming webhook / Workflows) and Jira Cloud comments.

Without configuration they run in dry-run mode: the payload is built and returned, but nothing is sent.
"""
import logging

import httpx

from .base import request_with_retries

log = logging.getLogger("connectors")


def teams_card(title: str, facts: dict[str, str], text: str) -> dict:
    """Adaptive Card payload as accepted by Teams Workflows webhooks."""
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
                    {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in facts.items()]},
                    {"type": "TextBlock", "text": text, "wrap": True},
                ],
            },
        }],
    }


class TeamsNotifier:
    def __init__(self, webhook_url: str | None, client: httpx.Client | None = None):
        self.webhook_url = webhook_url
        self.client = client or httpx.Client(timeout=10)

    def send(self, payload: dict) -> dict:
        if not self.webhook_url:
            log.info("teams dry-run", extra={"extra_fields": {"title": payload["attachments"][0]["content"]["body"][0]["text"]}})
            return {"sent": False, "dry_run": True}
        request_with_retries(self.client, "POST", self.webhook_url, json=payload)
        return {"sent": True, "dry_run": False}


def adf(text: str) -> dict:
    """Jira Cloud expects comments in Atlassian Document Format."""
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in text.split("\n") if line]}


class JiraClient:
    def __init__(self, base_url: str | None, email: str | None, api_token: str | None, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.client = client or (httpx.Client(auth=(email, api_token), timeout=10) if base_url else None)

    def comment(self, issue_key: str, text: str) -> dict:
        if not self.base_url:
            log.info("jira dry-run", extra={"extra_fields": {"issue": issue_key, "chars": len(text)}})
            return {"sent": False, "dry_run": True}
        request_with_retries(self.client, "POST", f"{self.base_url}/rest/api/3/issue/{issue_key}/comment",
                             json={"body": adf(text)})
        return {"sent": True, "dry_run": False}
