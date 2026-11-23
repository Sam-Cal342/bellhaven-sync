"""Scrape every Bellhaven community from the public website.

Discovery is deliberately broader than the directory: the home page links to a newly
acquired community (Findlay) that the paginated directory does not list.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

DETAIL_PATH = re.compile(r"^/communities/[a-z0-9-]+/?$")
MAX_DIRECTORY_PAGES = 50


@dataclass
class Location:
    slug: str
    url: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    care_offerings: list[str]
    phone: str = ""
    administrator: str = ""
    found_via: list[str] = field(default_factory=list)


@dataclass
class ScrapeResult:
    locations: list[Location]
    warnings: list[str]
    scraped_at: str

    def to_json(self) -> dict:
        return {"scraped_at": self.scraped_at, "warnings": self.warnings,
                "locations": [asdict(l) for l in self.locations]}


def _get(session: requests.Session, url: str) -> BeautifulSoup:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _detail_links(soup: BeautifulSoup, base: str) -> list[str]:
    out = []
    for a in soup.find_all("a", href=True):
        path = urlparse(urljoin(base, a["href"])).path
        if DETAIL_PATH.match(path):
            out.append(path.rstrip("/"))
    return out


def _parse_detail(soup: BeautifulSoup, url: str, slug: str) -> tuple[Location | None, list[str]]:
    warnings: list[str] = []
    h1 = soup.find("h1")
    fields: dict[str, object] = {}
    for dt in soup.select("dl.detail dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            fields[dt.get_text(strip=True).lower()] = dd
    addr_dd = fields.get("address")
    if h1 is None or addr_dd is None:
        return None, [f"{url}: missing name or address block, skipped"]

    lines = [s.strip() for s in addr_dd.get_text("\n").split("\n") if s.strip()]
    street, city, state, zip_code = (lines[0] if lines else ""), "", "", ""
    m = re.match(r"^(.*),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?$", lines[-1] if len(lines) > 1 else "")
    if m:
        city, state, zip_code = m.group(1).strip(), m.group(2), m.group(3)
    else:
        warnings.append(f"{url}: could not parse city/state/zip from {lines!r}")

    care_dd = fields.get("care offerings")
    care = [b.get_text(strip=True) for b in care_dd.select(".badge")] if care_dd else []
    if not care:
        warnings.append(f"{url}: no care offerings listed")

    text = lambda k: fields[k].get_text(" ", strip=True) if k in fields else ""
    return Location(slug=slug, url=url, name=h1.get_text(" ", strip=True), street=street, city=city,
                    state=state, zip=zip_code, care_offerings=care, phone=text("phone"),
                    administrator=text("administrator")), warnings


def scrape(base_url: str | None = None, session: requests.Session | None = None) -> ScrapeResult:
    base = (base_url or config.SITE_BASE_URL).rstrip("/")
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "bellhaven-crm-sync/1.0")
    warnings: list[str] = []
    found: dict[str, list[str]] = {}  # detail path -> where we found it

    def note(paths, source):
        for p in paths:
            found.setdefault(p, [])
            if source not in found[p]:
                found[p].append(source)

    # 1) Home page (has the "new this year" announcement link + claimed community count)
    home = _get(session, base + "/")
    note(_detail_links(home, base), "home page")
    claimed_total = None
    m = re.search(r"serve\s+(\d+)\s+communities", home.get_text(" "))
    if m:
        claimed_total = int(m.group(1))

    # 2) Directory, following "Next" links until there are none
    url, pages, listed_total = base + "/communities", 0, None
    while url and pages < MAX_DIRECTORY_PAGES:
        soup = _get(session, url)
        pages += 1
        note(_detail_links(soup, base), f"directory page {pages}")
        m = re.search(r"(\d+)\s+communities listed", soup.get_text(" "))
        if m:
            listed_total = int(m.group(1))
        nxt = soup.find("a", string=re.compile(r"Next", re.I))
        url = urljoin(base, nxt["href"]) if nxt else None

    # 3) Detail pages
    locations: list[Location] = []
    for path in sorted(found):
        slug = path.rsplit("/", 1)[-1]
        detail_url = base + path
        try:
            loc, w = _parse_detail(_get(session, detail_url), detail_url, slug)
        except requests.RequestException as exc:
            warnings.append(f"{detail_url}: fetch failed ({exc})")
            continue
        warnings.extend(w)
        if loc:
            loc.found_via = found[path]
            locations.append(loc)

    in_directory = sum(1 for l in locations if any(s.startswith("directory") for s in l.found_via))
    if listed_total is not None and in_directory != listed_total:
        warnings.append(f"Directory says {listed_total} communities listed but {in_directory} were parsed from it")
    if claimed_total is not None and len(locations) != claimed_total:
        warnings.append(f"Home page claims {claimed_total} communities but {len(locations)} were found")
    only_home = [l.name for l in locations if l.found_via == ["home page"]]
    if only_home:
        warnings.append(f"Found only via home page (not in directory): {', '.join(only_home)}")

    return ScrapeResult(locations=locations, warnings=warnings,
                        scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def save_snapshot(result: ScrapeResult, directory: Path | None = None) -> Path:
    directory = directory or config.SNAPSHOT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"locations_{result.scraped_at[:19].replace(':', '').replace('-', '')}.json"
    path.write_text(json.dumps(result.to_json(), indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    res = scrape()
    print(f"{len(res.locations)} locations")
    for w in res.warnings:
        print("WARN", w)
    for l in res.locations:
        print(f"  {l.name} | {l.street}, {l.city}, {l.state} {l.zip} | {', '.join(l.care_offerings)}")
    print("snapshot:", save_snapshot(res))
