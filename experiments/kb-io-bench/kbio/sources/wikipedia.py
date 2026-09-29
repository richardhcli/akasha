"""Map concept notes to English Wikipedia pages and fetch them, pinned to a revision.

Mapping is exact lookup only (`action=query&titles=...&redirects=1`), never search: a note matches
a page when its stripped title, or a case variant of it, *is* that page or redirects to it, and the
page is not a disambiguation page. opensearch is used only to suggest candidates for hand overrides
(config/wiki-overrides.toml). Every API response is cached, so a rerun makes 0 requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import tomllib
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

import requests

from kbio.paths import CONFIG, DATA, EXP, SOURCE_VAULT
from kbio.sources.base import Document

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "kb-io-bench/0.1 (https://github.com/richardhcli/akasha; low-rate research benchmark)"
WIKI = DATA / "sources" / "wiki"
API_CACHE = WIKI / "api"
PAGES = WIKI / "pages"
MAPPING = WIKI / "mapping.json"
MANIFEST = EXP / "wiki-manifest.json"
LICENSE = "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)"
# Notes in these folders are personal workflow, not encyclopedic concepts; override to include.
SKIP_FOLDERS = ("Personal Workflow/",)
DROP_SECTIONS = {
    "references",
    "external links",
    "see also",
    "notes",
    "further reading",
    "bibliography",
    "sources",
    "citations",
    "footnotes",
    "works cited",
    "notes and references",
}


class Api:
    """Cached, throttled (1 request/s) MediaWiki API client."""

    def __init__(self) -> None:
        self.requests = 0
        self.cache_hits = 0
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def get(self, params: dict[str, str]) -> dict:
        params = {"format": "json", "formatversion": "2", **params}
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        path = API_CACHE / f"{key}.json"
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text())
        for attempt in range(5):
            delay = self._last + 1.0 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = self.session.get(API, params=params, timeout=60)
                if r.status_code == 200:
                    data = r.json()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"params": params, "response": data}) + "\n")
                    return {"params": params, "response": data}
                if r.status_code == 429:  # honour Retry-After, else back off hard
                    time.sleep(int(r.headers.get("Retry-After", 0) or 0) or 30 * (attempt + 1))
                    continue
            except requests.RequestException:
                pass
            time.sleep(5 * 2**attempt)
        raise RuntimeError(f"wikipedia API failed: {params}")


def concept_name(stem: str) -> str:
    """'Human Nature (concept)' -> 'Human Nature'; strips every trailing parenthetical."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", stem).strip()
    return name or stem


def variants(name: str) -> list[str]:
    out = [name, name[:1].upper() + name[1:], name[:1].upper() + name[1:].lower(), name.title()]
    return list(dict.fromkeys(v for v in out if v))


def load_overrides() -> dict[str, str]:
    path = CONFIG / "wiki-overrides.toml"
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text())["overrides"]


def lookup(api: Api, titles: list[str]) -> dict[str, dict]:
    """title -> {page, disambiguation, missing}, following normalization and redirects."""
    out: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        resp = api.get(
            {
                "action": "query",
                "titles": "|".join(batch),
                "redirects": "1",
                "prop": "pageprops",
                "ppprop": "disambiguation",
            }
        )["response"]["query"]
        norm = {n["from"]: n["to"] for n in resp.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in resp.get("redirects", [])}
        pages = {p["title"]: p for p in resp.get("pages", [])}
        for t in batch:
            n = norm.get(t, t)
            target = redir.get(n, n)
            p = pages.get(target, {})
            out[t] = {
                "page": target if p and not p.get("missing") and not p.get("invalid") else None,
                "redirect": n in redir,
                "disambiguation": "disambiguation" in (p.get("pageprops") or {}),
            }
    return out


def map_notes(api: Api) -> dict[str, dict]:
    notes = sorted(str(p.relative_to(SOURCE_VAULT)) for p in SOURCE_VAULT.rglob("*.md"))
    overrides = load_overrides()
    names = {n: concept_name(Path(n).stem) for n in notes}
    cand = {v for n in notes for v in variants(names[n])}
    cand = sorted(cand | set(overrides.values()) - {"skip"})
    found = lookup(api, cand)
    mapping: dict[str, dict] = {}
    for n in notes:
        name = names[n]
        if name in overrides or Path(n).stem in overrides:
            o = overrides.get(Path(n).stem, overrides.get(name))
            if o == "skip":
                mapping[n] = {"name": name, "page": None, "how": "override-skip"}
            else:
                assert found[o]["page"], f"override target missing: {o}"
                mapping[n] = {"name": name, "page": found[o]["page"], "how": "override"}
            continue
        if n.startswith(SKIP_FOLDERS) or name.startswith("{") or not re.search(r"[A-Za-z]", name):
            mapping[n] = {"name": name, "page": None, "how": "skip-folder"}
            continue
        hit = None
        for v in variants(name):
            f = found[v]
            if f["page"] and not f["disambiguation"]:
                hit = (f["page"], "redirect" if f["redirect"] else "exact")
                break
        disamb = any(found[v]["disambiguation"] for v in variants(name))
        mapping[n] = {
            "name": name,
            "page": hit[0] if hit else None,
            "how": hit[1] if hit else ("disambiguation" if disamb else "missing"),
        }
    return mapping


def suggest(api: Api, name: str) -> list[str]:
    r = api.get({"action": "opensearch", "search": name, "limit": "5", "namespace": "0"})
    return r["response"][1]


def fetch_page(api: Api, title: str, with_links: bool = True) -> dict | None:
    """Full plaintext + revision (+ outgoing article links), in one request (plus continuations).
    None for a missing page."""
    params = {
        "action": "query",
        "titles": title,
        "redirects": "1",
        "prop": "extracts|revisions|links|info",
        "explaintext": "1",
        "exsectionformat": "wiki",
        "rvprop": "ids|timestamp",
        "plnamespace": "0",
        "pllimit": "max",
        "inprop": "url",
    }
    if not with_links:
        params["prop"] = "extracts|revisions|info"
        del params["plnamespace"], params["pllimit"]
    page: dict = {}
    links: list[str] = []
    cont: dict[str, str] = {}
    while True:
        resp = api.get({**params, **cont})["response"]
        p = resp["query"]["pages"][0]
        if not page:
            page = p
        else:
            page.setdefault("extract", p.get("extract"))
        links += [link["title"] for link in p.get("links", [])]
        if "continue" not in resp:
            break
        cont = {k: str(v) for k, v in resp["continue"].items()}
    if page.get("missing") or page.get("invalid") or not page.get("revisions"):
        return None
    rev = page["revisions"][0]
    return {
        "pageid": page["pageid"],
        "title": page["title"],
        "revid": rev["revid"],
        "timestamp": rev["timestamp"],
        "url": page.get("fullurl")
        or "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page["title"].replace(" ", "_")),
        "extract": page.get("extract") or "",
        "links": sorted(set(links)),
    }


def _drop_braced(text: str, opener: str) -> str:
    """Remove every balanced `{opener ...}` group (plaintext math blobs)."""
    out, i = [], 0
    while True:
        j = text.find(opener, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        depth, k = 0, j
        while k < len(text):
            depth += (text[k] == "{") - (text[k] == "}")
            k += 1
            if depth == 0:
                break
        i = k


def clean_extract(text: str) -> str:
    """Plaintext extract -> markdown: `== X ==` to `##`, drop reference-type sections and math
    blobs, and strip leftover braces (they would break akasha span grammar)."""
    text = _drop_braced(text, "{\\displaystyle")
    text = _drop_braced(text, "{\\textstyle")
    text = text.replace("{", "(").replace("}", ")")
    # MathML fallbacks come out as runs of deeply indented one-token lines: fold each run inline
    text = re.sub(r"\n(?:[ \t]*\n| {4,}[^\n]*\n)*(?: {4,}[^\n]*)\n(?:[ \t]*\n)*", " [math] ", text)
    text = re.sub(r"(\[math\]\s*){2,}", "[math] ", text)
    lines: list[str] = []
    dropping_level = 0
    for line in text.split("\n"):
        m = re.match(r"^(={2,6})\s*(.*?)\s*\1\s*$", line)
        if m:
            level = len(m.group(1))
            if dropping_level and level > dropping_level:
                continue
            dropping_level = 0
            if m.group(2).strip().lower() in DROP_SECTIONS:
                dropping_level = level
                continue
            lines.append("#" * level + " " + m.group(2).strip())
            continue
        if dropping_level:
            continue
        lines.append(line.rstrip())
    text = "\n\n".join(line for line in lines if line.strip())  # one paragraph per line
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # drop headings with no content under them
    text = re.sub(r"(?m)^#{2,6} [^\n]*\n+(?=#{2,6} |\Z)", "", text)
    return text.strip() + "\n"


def load_page(pageid: int) -> dict:
    return json.loads((PAGES / f"{pageid}.json").read_text())


class WikipediaSource:
    """Source plugin: the fetched, cleaned pages (all of them, or a given set of page ids)."""

    name = "wikipedia"

    def __init__(self, pageids: list[int] | None = None) -> None:
        self.pageids = pageids

    def iter_documents(self) -> Iterator[Document]:
        ids = self.pageids or sorted(int(p.stem) for p in PAGES.glob("*.json"))
        for pid in ids:
            p = load_page(pid)
            yield Document(
                id=f"wiki:{pid}",
                title=p["title"],
                text=p["text"],
                meta={k: p[k] for k in ("pageid", "revid", "url", "timestamp", "links")},
            )


def fetch_all(api: Api, titles: list[str], with_links: bool = True) -> list[dict]:
    """Fetch (or load from cache) every title; store cleaned pages under PAGES."""
    out = []
    for n, t in enumerate(titles, start=1):
        if n % 200 == 0:
            print(f"  {n}/{len(titles)} (requests {api.requests})", flush=True)
        raw = fetch_page(api, t, with_links)
        if raw is None:
            continue
        text = clean_extract(raw["extract"])
        rec = {k: v for k, v in raw.items() if k != "extract"}
        rec["text"] = text
        rec["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        path = PAGES / f"{raw['pageid']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or json.loads(path.read_text())["sha256"] != rec["sha256"]:
            path.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        out.append(rec)
    return out


def write_manifest(pages: list[dict]) -> None:
    old = json.loads(MANIFEST.read_text())["pages"] if MANIFEST.exists() else []
    by_id = {p["pageid"]: p for p in old}
    for p in pages:
        by_id[p["pageid"]] = {
            "pageid": p["pageid"],
            "title": p["title"],
            "revid": p["revid"],
            "timestamp": p["timestamp"],
            "url": p["url"],
            "sha256": p["sha256"],
        }
    MANIFEST.write_text(
        json.dumps(
            {
                "license": LICENSE,
                "attribution": "Text from English Wikipedia, by Wikipedia contributors; "
                "see each page's history at its URL. Cleaned to plaintext and perturbed "
                "(kb-io-bench leakage control); perturbations are recorded locally.",
                "pages": sorted(by_id.values(), key=lambda p: p["pageid"]),
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="map concept notes to Wikipedia and fetch them")
    ap.add_argument("--suggest", action="store_true", help="opensearch suggestions for misses")
    ap.add_argument("--map-only", action="store_true", help="map, but fetch no pages")
    args = ap.parse_args()
    api = Api()
    mapping = map_notes(api)
    MAPPING.parent.mkdir(parents=True, exist_ok=True)
    MAPPING.write_text(json.dumps(mapping, indent=1, ensure_ascii=False) + "\n")
    titles = sorted({m["page"] for m in mapping.values() if m["page"]})
    pages = [] if args.map_only else fetch_all(api, titles)
    if pages:
        write_manifest(pages)
    if args.suggest:
        sugg = {
            m["name"]: suggest(api, m["name"])
            for m in mapping.values()
            if m["how"] in ("missing", "disambiguation")
        }
        (WIKI / "suggestions.json").write_text(json.dumps(sugg, indent=1) + "\n")
    hows: dict[str, int] = {}
    for m in mapping.values():
        hows[m["how"]] = hows.get(m["how"], 0) + 1
    eligible = sum(1 for m in mapping.values() if m["how"] not in ("skip-folder", "override-skip"))
    matched = sum(1 for m in mapping.values() if m["page"])
    stats = {
        "notes": len(mapping),
        "eligible": eligible,
        "matched": matched,
        "match_rate_all": round(matched / len(mapping), 3),
        "match_rate_eligible": round(matched / max(eligible, 1), 3),
        "by_how": hows,
        "unique_pages": len(titles),
        "pages_with_revid": sum(1 for p in pages if p.get("revid")),
        "http_requests": api.requests,
        "cache_hits": api.cache_hits,
    }
    (WIKI / "map-stats.json").write_text(json.dumps(stats, indent=1) + "\n")
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
