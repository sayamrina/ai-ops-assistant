"""Document sources for the knowledge index: local folder, Confluence Cloud, SharePoint (Microsoft Graph), Nextcloud.

Each source returns (file name, markdown text). Pages are converted to simple markdown so the same chunking applies.
"""
import html
import re
from pathlib import Path

import httpx
from defusedxml import ElementTree  # safe against XML entity attacks from a remote server

from .base import request_with_retries


def html_to_markdown(body: str) -> str:
    body = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", body, flags=re.S)
    body = re.sub(r"<h[2-3][^>]*>(.*?)</h[2-3]>", r"\n## \1\n", body, flags=re.S)
    body = re.sub(r"<li[^>]*>", "\n- ", body)
    body = re.sub(r"</p>|<br\s*/?>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", "", body))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") + ".md"


class LocalFolderSource:
    def __init__(self, folder: Path):
        self.folder = folder

    def fetch(self) -> list[tuple[str, str]]:
        return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(self.folder.glob("*.md"))]


class ConfluenceSource:
    """Confluence Cloud REST API v2: all current pages of one space, body in storage format."""

    def __init__(self, base_url: str, email: str, api_token: str, space_id: str, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.space_id = space_id
        self.client = client or httpx.Client(auth=(email, api_token), timeout=20)

    def fetch(self) -> list[tuple[str, str]]:
        url = f"{self.base_url}/wiki/api/v2/spaces/{self.space_id}/pages?body-format=storage&limit=50"
        documents = []
        while url:
            data = request_with_retries(self.client, "GET", url).json()
            for page in data.get("results", []):
                body = html_to_markdown(page["body"]["storage"]["value"])
                documents.append((slug(page["title"]), f"# {page['title']}\n\n{body}"))
            nxt = data.get("_links", {}).get("next")
            url = f"{self.base_url}{nxt}" if nxt else None
        return documents


class SharePointSource:
    """Microsoft Graph: markdown and text files in one document library folder. The token comes from an app
    registration with Sites.Selected permission (client credentials flow, handled outside this class)."""

    def __init__(self, site_id: str, folder_path: str, access_token: str, client: httpx.Client | None = None):
        self.base = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{folder_path.strip('/')}"
        self.client = client or httpx.Client(headers={"Authorization": f"Bearer {access_token}"}, timeout=20)

    def fetch(self) -> list[tuple[str, str]]:
        items = request_with_retries(self.client, "GET", f"{self.base}:/children").json().get("value", [])
        documents = []
        for item in items:
            if item.get("name", "").endswith((".md", ".txt")):
                content = request_with_retries(self.client, "GET", item["@microsoft.graph.downloadUrl"]).text
                documents.append((item["name"], content))
        return documents


class NextcloudSource:
    """Nextcloud WebDAV: markdown files in one folder of a (service) user."""

    def __init__(self, base_url: str, user: str, app_password: str, folder: str, client: httpx.Client | None = None):
        self.root = f"{base_url.rstrip('/')}/remote.php/dav/files/{user}/{folder.strip('/')}/"
        self.client = client or httpx.Client(auth=(user, app_password), timeout=20)

    def fetch(self) -> list[tuple[str, str]]:
        listing = request_with_retries(self.client, "PROPFIND", self.root, headers={"Depth": "1"})
        names = [el.text for el in ElementTree.fromstring(listing.content).iter("{DAV:}href")]
        documents = []
        for href in names:
            if href and href.endswith(".md"):
                name = href.rstrip("/").split("/")[-1]
                documents.append((name, request_with_retries(self.client, "GET", self.root + name).text))
        return documents
