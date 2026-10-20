import json

import httpx

from app.connectors.notify import JiraClient, TeamsNotifier, teams_card
from app.connectors.sources import ConfluenceSource, NextcloudSource
from app.triage import triage_ticket


def test_security_incident_needs_a_human(index, llm):
    result = triage_ticket("ITOPS-1", "Laptop stolen", "My laptop was stolen at the train station", llm, index, 4, 0.1, 0.8)
    assert result.category == "security_incident" and result.priority == "critical"
    assert result.needs_review


def test_routine_ticket_is_automated(index, llm):
    llm.responses["triage"] = {"category": "it_access", "priority": "normal", "confidence": 0.93, "rationale": "MFA"}
    result = triage_ticket("ITOPS-2", "New phone", "I got a new phone, how do I reset MFA?", llm, index, 4, 0.1, 0.8)
    assert result.team == "IT Operations" and not result.needs_review and result.sources


def test_confluence_source_follows_pagination():
    pages = {
        "/wiki/api/v2/spaces/42/pages": {"results": [{"title": "VPN Guide", "body": {"storage": {"value": "<h2>Setup</h2><p>Install the client.</p>"}}}],
                                         "_links": {"next": "/wiki/api/v2/spaces/42/pages?cursor=2"}},
    }

    def handler(request: httpx.Request):
        if "cursor=2" in str(request.url):
            return httpx.Response(200, json={"results": [{"title": "Wi-Fi", "body": {"storage": {"value": "<p>Use eduroam.</p>"}}}], "_links": {}})
        return httpx.Response(200, json=pages[request.url.path])

    source = ConfluenceSource("https://x.atlassian.net", "a", "b", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))
    docs = source.fetch()
    assert [name for name, _ in docs] == ["vpn-guide.md", "wi-fi.md"]
    assert "## Setup" in docs[0][1]


def test_nextcloud_source_reads_markdown_files():
    listing = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
      <d:response><d:href>/remote.php/dav/files/bot/kb/</d:href></d:response>
      <d:response><d:href>/remote.php/dav/files/bot/kb/policy.md</d:href></d:response>
      <d:response><d:href>/remote.php/dav/files/bot/kb/photo.jpg</d:href></d:response></d:multistatus>"""

    def handler(request: httpx.Request):
        if request.method == "PROPFIND":
            return httpx.Response(207, content=listing)
        return httpx.Response(200, text="# Policy\n\nText")

    source = NextcloudSource("https://cloud.example", "bot", "pw", "kb", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.fetch() == [("policy.md", "# Policy\n\nText")]


def test_teams_retries_then_sends(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        return httpx.Response(503 if len(calls) == 1 else 200)

    notifier = TeamsNotifier("https://teams.example/hook", client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr("app.connectors.base.time.sleep", lambda _s: None)  # no real waiting in tests
    assert notifier.send(teams_card("T", {"a": "b"}, "text")) == {"sent": True, "dry_run": False}
    assert len(calls) == 2 and calls[0]["attachments"][0]["content"]["type"] == "AdaptiveCard"


def test_jira_comment_uses_adf():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201)

    jira = JiraClient("https://x.atlassian.net", "a", "b", client=httpx.Client(transport=httpx.MockTransport(handler)))
    jira.comment("ITOPS-7", "line one\nline two")
    assert seen["path"] == "/rest/api/3/issue/ITOPS-7/comment"
    assert seen["body"]["body"]["type"] == "doc" and len(seen["body"]["body"]["content"]) == 2
