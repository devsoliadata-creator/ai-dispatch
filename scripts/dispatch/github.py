"""A deliberately small GitHub REST client.

Only the six calls this automation makes. It exists so that every rule --
parsing the status block, deciding, rendering the record -- has exactly one
implementation, in Python, under test. The alternative was a second copy of
the status-block writer in workflow JavaScript.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str | None = None, repository: str | None = None):
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.repository = repository or os.environ.get("GITHUB_REPOSITORY", "")
        if not self.token or not self.repository:
            raise GitHubError("GITHUB_TOKEN and GITHUB_REPOSITORY are required")

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{API}{path}" if path.startswith("/") else path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "personal-assistant-dispatch")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                link = response.headers.get("Link", "")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise GitHubError(f"{method} {url} -> {exc.code}: {detail}") from exc
        parsed = json.loads(body) if body else None
        return parsed, link

    def _paginate(self, path: str) -> list[dict]:
        items: list[dict] = []
        url = f"{API}{path}"
        while url:
            page, link = self._request("GET", url)
            items.extend(page or [])
            url = _next_link(link)
        return items

    # ------------------------------------------------------------- issues

    def get_issue(self, number: int) -> dict:
        issue, _ = self._request("GET", f"/repos/{self.repository}/issues/{number}")
        return issue

    def update_issue(self, number: int, **fields: Any) -> dict:
        issue, _ = self._request("PATCH", f"/repos/{self.repository}/issues/{number}", fields)
        return issue

    def list_comments(self, number: int) -> list[dict]:
        return self._paginate(f"/repos/{self.repository}/issues/{number}/comments?per_page=100")

    def create_comment(self, number: int, body: str) -> dict:
        comment, _ = self._request(
            "POST", f"/repos/{self.repository}/issues/{number}/comments", {"body": body}
        )
        return comment

    def update_comment(self, comment_id: int, body: str) -> dict:
        comment, _ = self._request(
            "PATCH", f"/repos/{self.repository}/issues/comments/{comment_id}", {"body": body}
        )
        return comment

    def add_labels(self, number: int, labels: list[str]) -> None:
        self._request("POST", f"/repos/{self.repository}/issues/{number}/labels", {"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        try:
            self._request("DELETE", f"/repos/{self.repository}/issues/{number}/labels/{label}")
        except GitHubError:
            pass  # not present: nothing to remove

    # -------------------------------------------------------------- pulls

    def get_pull(self, number: int) -> dict:
        pull, _ = self._request("GET", f"/repos/{self.repository}/pulls/{number}")
        return pull


def _next_link(link_header: str) -> str:
    for part in (link_header or "").split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        if 'rel="next"' in section[1].replace(" ", "").replace("'", '"'):
            return section[0].strip().strip("<>")
    return ""


def label_names(issue: dict) -> list[str]:
    return [
        label if isinstance(label, str) else str(label.get("name", ""))
        for label in issue.get("labels") or []
    ]
