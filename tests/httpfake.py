"""A fake for ``recrate.http.get`` so Diagnose tests never touch the network.

Register responses by exact URL or by a substring match, then monkeypatch::

    fake = FakeHTTP()
    fake.add("https://doi.org/10.x/y", html=..., final_url=...)
    monkeypatch.setattr("recrate.http.get", fake.get)

Every call is recorded in ``fake.calls`` so a test can assert the cache saved a
round-trip (a URL fetched only once).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from recrate import http


@dataclass
class _Entry:
    content: bytes
    status: int = 200
    headers: dict = field(default_factory=dict)
    final_url: str | None = None


class FakeHTTP:
    def __init__(self) -> None:
        self._exact: dict[str, _Entry] = {}
        self._contains: list[tuple[str, _Entry]] = []
        self.calls: list[str] = []

    def add(self, url: str, *, content: bytes | None = None, text: str | None = None,
            html: str | None = None, status: int = 200, headers: dict | None = None,
            final_url: str | None = None, contains: bool = False) -> "FakeHTTP":
        body = content if content is not None else (text or html or "").encode("utf-8")
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        if html is not None and "content-type" not in hdrs:
            hdrs["content-type"] = "text/html; charset=utf-8"
        entry = _Entry(content=body, status=status, headers=hdrs, final_url=final_url)
        if contains:
            self._contains.append((url, entry))
        else:
            self._exact[url] = entry
        return self

    def get(self, url, *, accept=None, cache_dir=http.CACHE_DIR, timeout=http.TIMEOUT) -> http.Response:
        self.calls.append(url)
        entry = self._exact.get(url)
        if entry is None:
            for frag, e in self._contains:
                if frag in url:
                    entry = e
                    break
        if entry is None:
            raise http.HttpError(f"Could not fetch {url}: not registered in FakeHTTP")
        if entry.status >= 400:
            raise http.HttpError(f"{url} returned HTTP {entry.status}")
        return http.Response(url=entry.final_url or url, status=entry.status,
                             headers=dict(entry.headers), content=entry.content)
