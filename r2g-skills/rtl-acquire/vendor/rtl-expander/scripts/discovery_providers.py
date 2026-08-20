#!/usr/bin/env python3
"""Unified, read-only discovery providers for public RTL repositories."""

from __future__ import annotations

import abc
import datetime as dt
import email.utils
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class SearchPage:
    repositories: list[dict[str, Any]]
    next_cursor: str | None
    rate_limit_remaining: int | None = None
    rate_limit_reset: str | None = None
    total_count: int | None = None


class ProviderError(RuntimeError):
    def __init__(
        self, message: str, *, retry_after: int = 0, reset_at: str | None = None,
        status_code: int | None = None, rate_limited: bool = False,
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.reset_at = reset_at
        self.status_code = status_code
        self.rate_limited = rate_limited


def _reset_at(headers: dict[str, str], retry_after: str | None = None) -> tuple[int, str | None]:
    now = dt.datetime.now(dt.timezone.utc)
    delay = 0
    if retry_after:
        try:
            delay = max(0, int(retry_after))
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(retry_after)
                delay = max(0, int((parsed - now).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    raw_reset = headers.get("x-ratelimit-reset") or headers.get("ratelimit-reset")
    reset_at = None
    if raw_reset:
        try:
            value = int(float(raw_reset))
            parsed = dt.datetime.fromtimestamp(value, dt.timezone.utc) if value > 10_000_000 else now + dt.timedelta(seconds=value)
            reset_at = parsed.replace(microsecond=0).isoformat()
            delay = max(delay, int((parsed - now).total_seconds()))
        except (TypeError, ValueError, OverflowError, OSError):
            reset_at = raw_reset
    if delay and reset_at is None:
        reset_at = (now + dt.timedelta(seconds=delay)).replace(microsecond=0).isoformat()
    return delay, reset_at


class HTTPJSONClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[Any, dict[str, str]]:
        request = urllib.request.Request(url, headers={"User-Agent": "rtl-expander/1", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(16 * 1024 * 1024)
                return json.loads(body), {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
            remaining = response_headers.get("x-ratelimit-remaining") or response_headers.get("ratelimit-remaining")
            rate_limited = exc.code == 429 or (
                exc.code == 403 and (remaining == "0" or bool(response_headers.get("retry-after")))
            )
            retry_after, reset_at = _reset_at(response_headers, response_headers.get("retry-after"))
            if rate_limited and retry_after <= 0:
                retry_after = 300
                reset_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=retry_after)).replace(microsecond=0).isoformat()
            raise ProviderError(
                f"HTTP {exc.code} for {url}", retry_after=retry_after, reset_at=reset_at,
                status_code=exc.code, rate_limited=rate_limited,
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"provider request failed for {url}: {type(exc).__name__}") from exc


class DiscoveryProvider(abc.ABC):
    name: str

    def __init__(self, client: HTTPJSONClient | None = None):
        self.client = client or HTTPJSONClient()

    @abc.abstractmethod
    def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        raise NotImplementedError

    def get_repository_metadata(self, namespace: str, repo_name: str) -> dict[str, Any]:
        return {}

    def list_organization_repositories(self, namespace: str, cursor: str | None, limit: int) -> SearchPage:
        return SearchPage([], None)

    def resolve_upstream(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def resolve_forks(self, namespace: str, repo_name: str, cursor: str | None, limit: int) -> SearchPage:
        return SearchPage([], None)

    def discover_dependencies(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class GitHubProvider(DiscoveryProvider):
    name = "github"
    api = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _candidate(item: dict[str, Any]) -> dict[str, Any]:
        license_data = item.get("license") or {}
        return {
            "provider": "github", "url": item.get("html_url") or item.get("clone_url"),
            "default_branch": item.get("default_branch"), "size_bytes": int(item.get("size", 0)) * 1024,
            "license_hint": license_data.get("spdx_id"), "stars": item.get("stargazers_count", 0),
            "fork": bool(item.get("fork")), "archived": bool(item.get("archived")),
            "description": item.get("description") or "", "provider_id": item.get("id"),
            "primary_language": item.get("language"),
        }

    def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        page = max(1, int(cursor or "1"))
        per_page = min(100, max(1, limit))
        params = urllib.parse.urlencode({"q": query, "sort": "updated", "order": "desc", "per_page": per_page, "page": page})
        payload, headers = self.client.get_json(f"{self.api}/search/repositories?{params}", self._headers())
        items = [self._candidate(item) for item in payload.get("items", []) if item.get("html_url")]
        next_cursor = str(page + 1) if len(items) == per_page and page * per_page < min(1000, int(payload.get("total_count", 0))) else None
        return SearchPage(items, next_cursor, _int_header(headers, "x-ratelimit-remaining"), headers.get("x-ratelimit-reset"), int(payload.get("total_count", 0)))

    def get_repository_metadata(self, namespace: str, repo_name: str) -> dict[str, Any]:
        payload, _ = self.client.get_json(f"{self.api}/repos/{namespace}/{repo_name}", self._headers())
        return self._candidate(payload) | {"parent": (payload.get("parent") or {}).get("html_url"), "source": (payload.get("source") or {}).get("html_url")}

    def list_organization_repositories(self, namespace: str, cursor: str | None, limit: int) -> SearchPage:
        page, per_page = max(1, int(cursor or "1")), min(100, max(1, limit))
        params = urllib.parse.urlencode({"type": "public", "per_page": per_page, "page": page})
        try:
            payload, headers = self.client.get_json(f"{self.api}/orgs/{namespace}/repos?{params}", self._headers())
        except ProviderError:
            payload, headers = self.client.get_json(f"{self.api}/users/{namespace}/repos?{params}", self._headers())
        items = [self._candidate(item) for item in payload]
        return SearchPage(items, str(page + 1) if len(items) == per_page else None, _int_header(headers, "x-ratelimit-remaining"), headers.get("x-ratelimit-reset"))

    def resolve_upstream(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"url": url, "provider": self.name, "edge_type": "upstream"} for url in {metadata.get("parent"), metadata.get("source")} if url]

    def resolve_forks(self, namespace: str, repo_name: str, cursor: str | None, limit: int) -> SearchPage:
        page, per_page = max(1, int(cursor or "1")), min(100, max(1, limit))
        params = urllib.parse.urlencode({"per_page": per_page, "page": page, "sort": "stargazers"})
        payload, headers = self.client.get_json(f"{self.api}/repos/{namespace}/{repo_name}/forks?{params}", self._headers())
        items = [self._candidate(item) for item in payload]
        return SearchPage(items, str(page + 1) if len(items) == per_page else None, _int_header(headers, "x-ratelimit-remaining"), headers.get("x-ratelimit-reset"))


class GitLabProvider(DiscoveryProvider):
    name = "gitlab"
    api = "https://gitlab.com/api/v4"

    def _headers(self) -> dict[str, str]:
        token = os.environ.get("GITLAB_TOKEN")
        return {"PRIVATE-TOKEN": token} if token else {}

    @staticmethod
    def _candidate(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": "gitlab", "url": item.get("web_url") or item.get("http_url_to_repo"),
            "default_branch": item.get("default_branch"), "stars": item.get("star_count", 0),
            "archived": bool(item.get("archived")), "description": item.get("description") or "",
            "provider_id": item.get("id"), "namespace_kind": (item.get("namespace") or {}).get("kind"),
        }

    def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        page, per_page = max(1, int(cursor or "1")), min(100, max(1, limit))
        search_term = query.split()[0]
        params = urllib.parse.urlencode({"search": search_term, "simple": "true", "per_page": per_page, "page": page, "order_by": "last_activity_at"})
        payload, headers = self.client.get_json(f"{self.api}/projects?{params}", self._headers())
        items = [self._candidate(item) for item in payload if item.get("web_url")]
        next_cursor = headers.get("x-next-page") or None
        return SearchPage(items, next_cursor, _int_header(headers, "ratelimit-remaining"), headers.get("ratelimit-reset"))

    def get_repository_metadata(self, namespace: str, repo_name: str) -> dict[str, Any]:
        project = urllib.parse.quote(f"{namespace}/{repo_name}", safe="")
        payload, _ = self.client.get_json(f"{self.api}/projects/{project}", self._headers())
        candidate = self._candidate(payload)
        candidate["forked_from"] = (payload.get("forked_from_project") or {}).get("web_url")
        return candidate

    def list_organization_repositories(self, namespace: str, cursor: str | None, limit: int) -> SearchPage:
        page, per_page = max(1, int(cursor or "1")), min(100, max(1, limit))
        group = urllib.parse.quote(namespace, safe="")
        params = urllib.parse.urlencode({"per_page": per_page, "page": page, "include_subgroups": "true"})
        payload, headers = self.client.get_json(f"{self.api}/groups/{group}/projects?{params}", self._headers())
        items = [self._candidate(item) for item in payload if item.get("web_url")]
        return SearchPage(items, headers.get("x-next-page") or None, _int_header(headers, "ratelimit-remaining"), headers.get("ratelimit-reset"))

    def resolve_upstream(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"url": metadata["forked_from"], "provider": self.name, "edge_type": "upstream"}] if metadata.get("forked_from") else []


class CodebergProvider(DiscoveryProvider):
    name = "codeberg"
    api = "https://codeberg.org/api/v1"

    def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        page, page_limit = max(1, int(cursor or "1")), min(50, max(1, limit))
        params = urllib.parse.urlencode({"q": query.split()[0], "page": page, "limit": page_limit})
        payload, headers = self.client.get_json(f"{self.api}/repos/search?{params}")
        data = payload.get("data", []) if isinstance(payload, dict) else []
        items = [{
            "provider": "codeberg", "url": item.get("html_url") or item.get("clone_url"),
            "default_branch": item.get("default_branch"), "stars": item.get("stars_count", 0),
            "archived": bool(item.get("archived")), "description": item.get("description") or "",
            "provider_id": item.get("id"),
        } for item in data if item.get("html_url") or item.get("clone_url")]
        return SearchPage(items, str(page + 1) if len(items) == page_limit else None, _int_header(headers, "x-ratelimit-remaining"), headers.get("x-ratelimit-reset"))

    def list_organization_repositories(self, namespace: str, cursor: str | None, limit: int) -> SearchPage:
        page, page_limit = max(1, int(cursor or "1")), min(50, max(1, limit))
        params = urllib.parse.urlencode({"page": page, "limit": page_limit})
        payload, headers = self.client.get_json(f"{self.api}/orgs/{namespace}/repos?{params}")
        items = [{
            "provider": "codeberg", "url": item.get("html_url") or item.get("clone_url"),
            "default_branch": item.get("default_branch"), "stars": item.get("stars_count", 0),
            "archived": bool(item.get("archived")), "description": item.get("description") or "",
            "provider_id": item.get("id"),
        } for item in payload if item.get("html_url") or item.get("clone_url")]
        return SearchPage(items, str(page + 1) if len(items) == page_limit else None, _int_header(headers, "x-ratelimit-remaining"), headers.get("x-ratelimit-reset"))


class FuseSoCProvider(GitHubProvider):
    """Discover repositories containing FuseSoC CAPI2 core descriptions."""

    name = "fusesoc"

    def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        page, per_page = max(1, int(cursor or "1")), min(100, max(1, limit))
        core_query = f"{query} extension:core"
        params = urllib.parse.urlencode({"q": core_query, "per_page": per_page, "page": page})
        payload, headers = self.client.get_json(f"{self.api}/search/code?{params}", self._headers())
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for item in payload.get("items", []):
            repository = item.get("repository") or {}
            url = repository.get("html_url")
            if not url or url in seen:
                continue
            seen.add(url)
            candidate = self._candidate(repository)
            candidate["provider"] = "github"
            candidate["ecosystem"] = "fusesoc"
            candidate["core_path"] = item.get("path")
            items.append(candidate)
        next_cursor = str(page + 1) if len(payload.get("items", [])) == per_page and page * per_page < min(1000, int(payload.get("total_count", 0))) else None
        return SearchPage(items, next_cursor, _int_header(headers, "x-ratelimit-remaining"), headers.get("x-ratelimit-reset"), int(payload.get("total_count", 0)))


def _int_header(headers: dict[str, str], key: str) -> int | None:
    try:
        return int(headers[key])
    except (KeyError, TypeError, ValueError):
        return None


def provider_registry(client: HTTPJSONClient | None = None) -> dict[str, DiscoveryProvider]:
    return {
        "github": GitHubProvider(client), "gitlab": GitLabProvider(client),
        "codeberg": CodebergProvider(client), "fusesoc": FuseSoCProvider(client),
    }
