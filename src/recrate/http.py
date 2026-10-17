"""The one place ReCRATE touches the network.

Diagnose (fetch a paper, locate its RO-Crate) needs a few HTTP requests. They all
go through :func:`get` so that:

* behaviour is uniform (a clear User-Agent, a timeout, limited redirects);
* responses are cached under ``.recrate-cache/`` and a second call is free;
* tests patch exactly one function (``recrate.http.get``) and never touch the
  network.

It is built on the standard library (``urllib``) on purpose: Diagnose then needs
no extra dependency and the core keeps working everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

USER_AGENT = f"recrate/{__version__} (+https://github.com/recrate; reproducibility diagnose)"
TIMEOUT = 20
MAX_REDIRECTS = 5
CACHE_DIR = ".recrate-cache"


class HttpError(RuntimeError):
    """A request could not be completed (network, timeout, or HTTP >= 400)."""


@dataclass
class Response:
    url: str                       # final URL after redirects
    status: int
    headers: dict[str, str]        # header names lower-cased
    content: bytes = b""

    @property
    def text(self) -> str:
        return self.content.decode(self._charset(), errors="replace")

    def json(self):
        return json.loads(self.text)

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def _charset(self) -> str:
        ctype = self.header("content-type")
        if "charset=" in ctype:
            return ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        return "utf-8"


@dataclass
class _Cache:
    dir: Path
    enabled: bool = True
    _seen: set = field(default_factory=set)

    def _path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]
        return self.dir / f"{key}.json"

    def load(self, url: str) -> Response | None:
        if not self.enabled:
            return None
        p = self._path(url)
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        return Response(url=d["url"], status=d["status"], headers=d["headers"],
                        content=bytes.fromhex(d["content_hex"]))

    def store(self, url: str, resp: Response) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(url).write_text(json.dumps({
            "url": resp.url, "status": resp.status, "headers": resp.headers,
            "content_hex": resp.content.hex(),
        }), encoding="utf-8")


def _cache(cache_dir: str | Path | None) -> _Cache:
    if cache_dir is None:
        return _Cache(Path(CACHE_DIR), enabled=False)
    return _Cache(Path(cache_dir))


def get(url: str, *, accept: str | None = None, cache_dir: str | Path | None = CACHE_DIR,
        timeout: int = TIMEOUT) -> Response:
    """Fetch ``url`` with redirects, caching and a clear User-Agent.

    Raises :class:`HttpError` on a network failure or an HTTP status >= 400.
    This is the single network seam; tests monkeypatch it.
    """
    cache = _cache(cache_dir)
    cached = cache.load(url)
    if cached is not None:
        return cached

    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https/http, our UA
            resp = Response(url=r.geturl(), status=r.status,
                            headers={k.lower(): v for k, v in r.headers.items()},
                            content=r.read())
    except urllib.error.HTTPError as e:  # 4xx/5xx still carry a body we may want
        resp = Response(url=url, status=e.code,
                        headers={k.lower(): v for k, v in (e.headers or {}).items()},
                        content=e.read() if hasattr(e, "read") else b"")
        cache.store(url, resp)
        raise HttpError(f"{url} returned HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise HttpError(f"Could not fetch {url}: {e}") from e

    cache.store(url, resp)
    return resp


def link_headers(resp: Response) -> list[dict[str, str]]:
    """Parse RFC 8288 ``Link`` headers into ``[{url, rel, type}, ...]``."""
    raw = resp.header("link")
    out: list[dict[str, str]] = []
    if not raw:
        return out
    for part in _split_links(raw):
        m = part.strip()
        if not m.startswith("<") or ">" not in m:
            continue
        url, _, params = m.partition(">")
        entry = {"url": url[1:].strip(), "rel": "", "type": ""}
        for p in params.split(";"):
            if "=" in p:
                k, v = p.split("=", 1)
                k = k.strip().lower()
                v = v.strip().strip('"')
                if k in ("rel", "type"):
                    entry[k] = v
        out.append(entry)
    return out


def _split_links(raw: str) -> list[str]:
    """Split a Link header on commas that separate entries (not commas inside <...>)."""
    parts, depth, buf = [], 0, ""
    for ch in raw:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth <= 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return parts
