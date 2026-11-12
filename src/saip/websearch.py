"""Looking things up on the web for the chat, using only the standard library.

Two things happen here: a search (DuckDuckGo needs no key; Brave and Tavily use the one you set) and opening a page to
read its text. A page address comes from the model, and so possibly from text on another web page, so before anything
is fetched the host must resolve only to public addresses: nothing on this machine or your home network is reachable
through it, and every redirect is checked again. All network access goes through one `opener`, so tests use a fake.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

TIMEOUT_S = 12
PAGE_CHARS = 12_000
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


class SearchError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _urlopen(req: urllib.request.Request, timeout: float) -> bytes:
    """The default opener: no redirects (callers follow them by hand), the body capped by the caller via `.limit`."""
    with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
        return resp.read(getattr(req, "limit", 2_000_000))


def _get(opener, req: urllib.request.Request) -> bytes:
    try:
        return (opener or _urlopen)(req, TIMEOUT_S)
    except SearchError:
        raise
    except urllib.error.HTTPError as e:
        raise SearchError(f"the site answered HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise SearchError(f"could not reach the site: {getattr(e, 'reason', e)}") from e


# ---------------------------------------------------------------------------------------------------- searching
class _DuckParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._into: tuple[str, dict] | None = None

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.rows.append({"title": "", "url": _unwrap(dict(attrs).get("href") or ""), "snippet": ""})
            self._into = ("title", self.rows[-1])
        elif "result__snippet" in classes and self.rows:
            self._into = ("snippet", self.rows[-1])

    def handle_endtag(self, tag):
        if tag in ("a", "td", "div") and self._into is not None:
            self._into = None

    def handle_data(self, data):
        if self._into:
            key, row = self._into
            row[key] += data


def _unwrap(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    return q["uddg"][0] if "uddg" in q else href


def parse_duckduckgo(html: str, limit: int) -> list[dict]:
    parser = _DuckParser()
    parser.feed(html)
    rows = [{k: " ".join(v.split()) for k, v in r.items()} for r in parser.rows]
    return [r for r in rows if r["title"] and r["url"].startswith("http")][:limit]


def search(query: str, provider: str = "", key: str = "", limit: int = 6, opener=None) -> list[dict]:
    """[{"title", "url", "snippet"}] for the query. `provider` decides: "" or "duckduckgo", "brave", "tavily"."""
    query = (query or "").strip()
    if not query:
        raise SearchError("the search was empty")
    provider = (provider or "duckduckgo").lower()
    if provider in ("brave", "tavily") and not key:
        raise SearchError(f"{provider} needs a search API key in the settings")
    if provider == "duckduckgo":
        req = urllib.request.Request("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query}),
                                     headers={"User-Agent": USER_AGENT})
    elif provider == "brave":
        req = urllib.request.Request("https://api.search.brave.com/res/v1/web/search?"
                                     + urllib.parse.urlencode({"q": query, "count": limit}),
                                     headers={"X-Subscription-Token": key, "Accept": "application/json"})
    elif provider == "tavily":
        body = json.dumps({"api_key": key, "query": query, "max_results": limit}).encode()
        req = urllib.request.Request("https://api.tavily.com/search", body,
                                     {"Content-Type": "application/json"}, method="POST")
    else:
        raise SearchError(f"unknown search provider '{provider}'")
    raw = _get(opener, req)
    try:
        if provider == "duckduckgo":
            rows = parse_duckduckgo(raw.decode("utf-8", "replace"), limit)
        elif provider == "brave":
            rows = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
                    for r in json.loads(raw)["web"]["results"]]
        else:
            rows = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                    for r in json.loads(raw)["results"]]
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise SearchError("the search service answered in a form I could not read") from e
    return [{k: " ".join(str(v).split()) for k, v in r.items()} for r in rows[:limit]]


# ---------------------------------------------------------------------------------------------------- pages
def check_url(url: str, resolve=socket.getaddrinfo) -> str:
    """The url when it is http(s) and its host resolves only to public addresses; otherwise SearchError."""
    parts = urllib.parse.urlparse((url or "").strip())
    if parts.scheme not in ("http", "https"):
        raise SearchError("only http and https pages can be opened")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise SearchError("that address has no host")
    if host == "localhost" or host.endswith(".localhost"):
        raise SearchError("pages on this machine cannot be opened")
    try:
        found = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            found = {ipaddress.ip_address(a[4][0].split("%")[0]) for a in resolve(host, parts.port or 443)}
        except (OSError, ValueError) as e:
            raise SearchError(f"could not find {host}") from e
    if not found:
        raise SearchError(f"could not find {host}")
    for ip in found:
        if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or not ip.is_global):
            raise SearchError(f"{host} is not a public address, so I will not open it")
    return url.strip()


class _Visible(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def _visible_text(html: str) -> str:
    parser = _Visible()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n\s*\n+", "\n", "\n".join(" ".join(line.split()) for line in text.split("\n"))).strip()


def fetch_text(url: str, limit: int = 200_000, opener=None, resolve=socket.getaddrinfo) -> str:
    """The readable text of a public page, at most PAGE_CHARS long. Redirects are followed (3 at most), each checked."""
    for _ in range(4):
        check_url(url, resolve)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,*/*"})
        req.limit = limit
        try:
            raw = (opener or _urlopen)(req, TIMEOUT_S)
        except urllib.error.HTTPError as e:
            target = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) and e.headers else None
            if not target:
                raise SearchError(f"the site answered HTTP {e.code}") from e
            url = urllib.parse.urljoin(url, target)
            continue
        except SearchError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise SearchError(f"could not reach the site: {getattr(e, 'reason', e)}") from e
        break
    else:
        raise SearchError("too many redirects")
    text = raw[:limit].decode("utf-8", "replace")
    if re.search(r"<\s*(html|body|head|div|p)\b", text[:5000], re.I):
        text = _visible_text(text)
    return text if len(text) <= PAGE_CHARS else text[:PAGE_CHARS] + " […]"
