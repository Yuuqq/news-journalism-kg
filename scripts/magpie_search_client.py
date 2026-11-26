# -*- coding: utf-8 -*-
"""Stdlib-only, config-driven client for the local Magpie Web Search MCP server.

Implements MCP Streamable HTTP JSON-RPC 2.0:
  initialize -> Mcp-Session-Id -> notifications/initialized ->
  tools/call (`web_search`, `web_fetch`).

Enforces strict local hostname validation (`urlparse(url).netloc.lower()`)
because upstream search channels occasionally ignore `allowed_domains`.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"

# Magpie web_search formats items like:
#   "1. Title text — https://example.com/path\nsnippet lines..."
_ITEM_HEADER_RE = re.compile(
    r"(?m)^(\d+)\.\s+(.+?)\s+[—–-]\s+(https?://\S+)\s*$"
)
_CN_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

CANONICAL_WIKI_HOSTS = frozenset({"en.wikipedia.org", "zh.wikipedia.org"})
_BAD_WIKI_PREFIXES = (
    "/wiki/Special:",
    "/wiki/Talk:",
    "/wiki/File:",
    "/wiki/Category:",
    "/wiki/Template:",
    "/wiki/Wikipedia:",
    "/wiki/Help:",
    "/wiki/Portal:",
    "/wiki/User:",
    "/wiki/Draft:",
    "/wiki/Module:",
    "/wiki/MediaWiki:",
    "/wiki/TimedText:",
    "/w/",
)


@dataclass
class SearchHit:
    rank: int
    title: str
    url: str
    domain: str
    snippet: str
    query: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_magpie_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load Magpie MCP settings from config.json with env var override."""
    cfg: Dict[str, Any] = {
        "mcp_url": "http://127.0.0.1:3425/mcp/magpie/web-search",
        "protocol_version": "2025-03-26",
        "timeout_seconds": 35,
        "fetch_timeout_seconds": 35,
        "default_count": 10,
        "default_context_size": "medium",
        "min_interval_seconds": 0.35,
        "max_retries": 2,
    }
    candidates = (
        [config_path]
        if config_path
        else [DEFAULT_CONFIG_PATH, Path.cwd() / "config.json"]
    )
    for p in candidates:
        if p and p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                section = raw.get("magpie_web_search", {})
                if isinstance(section, dict):
                    cfg.update(section)
                break
            except Exception:
                pass

    if "min_request_interval_seconds" in cfg and "min_interval_seconds" not in cfg:
        cfg["min_interval_seconds"] = cfg["min_request_interval_seconds"]

    env_url = os.environ.get("MAGPIE_WEB_SEARCH_MCP_URL", "").strip()
    if env_url:
        cfg["mcp_url"] = env_url
    return cfg


def validate_http_url(url: str) -> str:
    """Validate that a string is a syntactically valid http(s) URL."""
    cleaned = (url or "").strip()
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Unsupported or invalid HTTP(S) URL: {url!r}")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError(f"URL contains whitespace: {url!r}")
    return cleaned


def is_valid_http_url(url: str) -> bool:
    try:
        validate_http_url(url)
        return True
    except ValueError:
        return False


def extract_netloc(url: str) -> str:
    """Return strict lowercase netloc without port (preserving subdomains)."""
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        netloc = parsed.netloc.lower().strip()
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        if ":" in netloc:
            netloc = netloc.split(":", 1)[0]
        return netloc.strip(".")
    except Exception:
        return ""


def extract_hostname(url: str) -> str:
    """Return normalized lowercase hostname without port or leading www."""
    host = extract_netloc(url)
    if host.startswith("www."):
        host = host[4:]
    return host


def host_matches_domain(url_or_host: str, domain_suffix: str) -> bool:
    """Strict local hostname check (exact match or subdomain suffix)."""
    if "://" in url_or_host:
        host = extract_hostname(url_or_host)
    else:
        host = url_or_host.lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
    target = domain_suffix.lower().strip(".")
    if target.startswith("www."):
        target = target[4:]
    if not host or not target:
        return False
    return host == target or host.endswith("." + target)


def host_matches_any(url_or_host: str, domains: Iterable[str]) -> bool:
    return any(host_matches_domain(url_or_host, d) for d in domains)


def normalize_canonical_wikipedia_url(url: str) -> str:
    """Return canonical en/zh Wikipedia article URL or '' if invalid/non-canonical.

    Strictly checks `urlparse(url).netloc.lower()` against en/zh Wikipedia
    (also normalizing en.m.wikipedia.org / zh.m.wikipedia.org to desktop form)
    and strips query/fragment while rejecting Talk/Special/File/Category pages.
    """
    cleaned = (url or "").strip()
    if not is_valid_http_url(cleaned):
        return ""
    parsed = urllib.parse.urlparse(cleaned)
    netloc = extract_netloc(cleaned)
    if netloc == "en.m.wikipedia.org":
        netloc = "en.wikipedia.org"
    elif netloc == "zh.m.wikipedia.org":
        netloc = "zh.wikipedia.org"
    if netloc not in CANONICAL_WIKI_HOSTS:
        return ""
    path = parsed.path or ""
    # Canonicalize Chinese Wikipedia language-variant paths (/zh-hans/, /zh-tw/, etc.) to /wiki/
    if netloc == "zh.wikipedia.org":
        m_zh = re.match(r"^/(?:zh(?:-[a-z]+)?)/(.+)$", path)
        if m_zh:
            path = "/wiki/" + m_zh.group(1)
    if not path.startswith("/wiki/") or len(path) <= len("/wiki/"):
        return ""
    decoded_path = urllib.parse.unquote(path)
    for bad in _BAD_WIKI_PREFIXES:
        if decoded_path.startswith(bad) or path.startswith(bad):
            return ""
    # Reject other namespace colons like Help:, Portal:, 维基百科:, 分类:, 模板:
    slug = decoded_path[len("/wiki/") :]
    if ":" in slug:
        prefix = slug.split(":", 1)[0]
        if prefix.lower() in {
            "special", "talk", "file", "image", "category", "template",
            "wikipedia", "help", "portal", "user", "draft", "module",
            "mediawiki", "timedtext", "book", "topic",
            "特殊", "讨论", "討論", "文件", "檔案", "分类", "分類",
            "模板", "维基百科", "維基百科", "帮助", "幫助", "主题", "主題", "用户", "用戶",
        }:
            return ""
    return f"https://{netloc}{path}"


def is_canonical_wikipedia_url(url: str) -> bool:
    """True only if URL is already canonical https://en.wikipedia.org/wiki/... or zh."""
    if not is_valid_http_url(url):
        return False
    netloc = extract_netloc(url)
    if netloc not in CANONICAL_WIKI_HOSTS:
        return False
    norm = normalize_canonical_wikipedia_url(url)
    return bool(norm) and url.split("#")[0].split("?")[0] == norm


def contains_cn_mobile(text: str) -> bool:
    return bool(_CN_MOBILE_RE.search(text or ""))


def mask_cn_mobile(text: str) -> str:
    return _CN_MOBILE_RE.sub("[REDACTED_MOBILE]", text or "")


def parse_web_search_text(text: str, query: str = "") -> List[SearchHit]:
    """Parse Magpie web_search plain-text output into structured SearchHit items."""
    if not text or not text.strip():
        return []
    matches = list(_ITEM_HEADER_RE.finditer(text))
    if not matches:
        return []

    hits: List[SearchHit] = []
    for idx, m in enumerate(matches):
        rank = int(m.group(1))
        title = m.group(2).strip()
        url = m.group(3).strip().rstrip(",;\"'>")
        # Strip trailing ')' only when unbalanced (preserve Wikipedia URLs like Foo_(book))
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        # Strip trailing period only if not part of abbreviations like _Jr. or _Sr.
        if url.endswith(".") and not re.search(r"(?:Jr|Sr|St|Dr|Mr|Ms)\.$", url, re.I):
            url = url[:-1]
        if not is_valid_http_url(url):
            continue
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        hits.append(
            SearchHit(
                rank=rank,
                title=title,
                url=url,
                domain=extract_hostname(url),
                snippet=snippet[:1400],
                query=query,
            )
        )
    return hits


def _parse_mcp_body(content_type: str, body: str) -> Dict[str, Any]:
    """Handle both application/json and text/event-stream MCP responses."""
    ct = (content_type or "").lower()
    if "text/event-stream" in ct:
        last_obj: Dict[str, Any] = {}
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        last_obj = json.loads(payload)
                    except json.JSONDecodeError:
                        pass
        return last_obj
    if not body.strip():
        return {}
    return json.loads(body)


class MagpieWebSearchClient:
    """Streamable HTTP MCP JSON-RPC 2.0 client for local Magpie web-search."""

    def __init__(
        self,
        mcp_url: Optional[str] = None,
        config_path: Optional[Path] = None,
        client_name: str = "news-journalism-kg-enricher",
    ) -> None:
        self.cfg = load_magpie_config(config_path)
        self.mcp_url = validate_http_url(mcp_url or self.cfg["mcp_url"])
        self.client_name = client_name
        self.session_id: Optional[str] = None
        self._req_id = 0
        self._last_call_ts = 0.0
        self.search_calls = 0
        self.fetch_calls = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _pace(self) -> None:
        min_gap = float(
            self.cfg.get(
                "min_interval_seconds",
                self.cfg.get("min_request_interval_seconds", 0.35),
            )
        )
        elapsed = time.monotonic() - self._last_call_ts
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        self._last_call_ts = time.monotonic()

    def _post(
        self, payload: Dict[str, Any], timeout: float
    ) -> tuple[int, Optional[str], Dict[str, Any]]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            self.mcp_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get(
                    "mcp-session-id"
                )
                ct = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, sid, _parse_mcp_body(ct, raw)
        except urllib.error.HTTPError as exc:
            sid = exc.headers.get("Mcp-Session-Id") if exc.headers else None
            ct = exc.headers.get("Content-Type", "") if exc.headers else ""
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = _parse_mcp_body(ct, raw)
            except Exception:
                parsed = {"raw_error": raw[:500]}
            return exc.code, sid, parsed

    def initialize(self) -> str:
        """Establish a fresh MCP session with the Magpie web-search endpoint."""
        self.session_id = None
        timeout = float(self.cfg.get("timeout_seconds", 35))
        status, sid, data = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": self.cfg.get("protocol_version", "2025-03-26"),
                    "capabilities": {},
                    "clientInfo": {"name": self.client_name, "version": "1.0.0"},
                },
            },
            timeout=min(timeout, 20.0),
        )
        if status != 200 or "error" in data:
            raise RuntimeError(f"Magpie MCP initialize failed (HTTP {status}): {data}")
        self.session_id = sid
        try:
            self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                timeout=10.0,
            )
        except Exception:
            pass
        return self.session_id or ""

    def _call_tool(
        self, tool_name: str, arguments: Dict[str, Any], timeout: float
    ) -> Dict[str, Any]:
        if not self.session_id:
            self.initialize()

        max_retries = int(self.cfg.get("max_retries", 2))
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            self._pace()
            try:
                status, sid, data = self._post(
                    {
                        "jsonrpc": "2.0",
                        "id": self._next_id(),
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                    timeout=timeout,
                )
                if sid:
                    self.session_id = sid
                if status in (400, 404) and attempt < max_retries:
                    self.initialize()
                    continue
                if status != 200 or "error" in data:
                    raise RuntimeError(f"Magpie MCP {tool_name} HTTP {status}: {data}")
                return data.get("result", {})
            except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
                last_err = exc
                if attempt < max_retries:
                    time.sleep(0.8 * (attempt + 1))
                    if "session" in str(exc).lower() or isinstance(exc, OSError):
                        try:
                            self.initialize()
                        except Exception:
                            pass
                    continue
                raise
        raise RuntimeError(f"Magpie MCP {tool_name} failed: {last_err}")

    def search(
        self,
        query: str,
        *,
        count: Optional[int] = None,
        allowed_domains: Optional[Sequence[str]] = None,
        blocked_domains: Optional[Sequence[str]] = None,
        recency_days: Optional[int] = None,
        search_context_size: Optional[str] = None,
        enforce_local_domain_filter: bool = True,
    ) -> List[SearchHit]:
        """Execute a web_search call and return structured SearchHit records.

        Defaults to `enforce_local_domain_filter=True` so any `allowed_domains`
        constraint is strictly validated locally via `urlparse(url).netloc.lower()`.
        """
        args: Dict[str, Any] = {
            "query": query,
            "count": int(
                count if count is not None else self.cfg.get("default_count", 10)
            ),
            "search_context_size": search_context_size
            or self.cfg.get("default_context_size", "medium"),
        }
        if allowed_domains:
            args["allowed_domains"] = list(allowed_domains)
        if blocked_domains:
            args["blocked_domains"] = list(blocked_domains)
        if recency_days is not None:
            args["recency_days"] = int(recency_days)

        self.search_calls += 1
        result = self._call_tool(
            "web_search", args, timeout=float(self.cfg.get("timeout_seconds", 35))
        )
        if result.get("isError"):
            texts = [
                c.get("text", "")
                for c in result.get("content", [])
                if isinstance(c, dict)
            ]
            raise RuntimeError(f"web_search error for {query!r}: {' '.join(texts)}")

        combined = "\n\n".join(
            c.get("text", "")
            for c in result.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        )
        hits = parse_web_search_text(combined, query=query)
        if enforce_local_domain_filter and allowed_domains:
            hits = [h for h in hits if host_matches_any(h.url, allowed_domains)]
        if blocked_domains:
            hits = [h for h in hits if not host_matches_any(h.url, blocked_domains)]
        return hits

    def fetch(
        self, url: str, *, max_chars: int = 12000, offset: int = 0
    ) -> Dict[str, Any]:
        """Fetch a web page as Markdown via Magpie web_fetch."""
        safe_url = validate_http_url(url)
        self.fetch_calls += 1
        result = self._call_tool(
            "web_fetch",
            {"url": safe_url, "max_chars": int(max_chars), "offset": int(offset)},
            timeout=float(self.cfg.get("fetch_timeout_seconds", 35)),
        )
        text = "\n".join(
            c.get("text", "")
            for c in result.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        )
        return {"ok": not bool(result.get("isError")), "url": safe_url, "text": text}
