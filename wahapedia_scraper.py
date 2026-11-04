#!/usr/bin/env python3
"""
Wahapedia datasheet scraper (Warhammer 40,000 11th edition).

Scrapes every datasheet of a faction and writes ONE JSON file in which all
information belonging to a unit is kept together:

    statline(s), ranged + melee weapon profiles, army rules (with rule text),
    core abilities, datasheet abilities, wargear abilities, keywords.

Usage
-----
    pip install requests beautifulsoup4
    python wahapedia_scraper.py
    python wahapedia_scraper.py --out am.json --delay 2 --limit 3
    python wahapedia_scraper.py --no-legends

Pages are cached in ./cache so re-running (or tweaking the parser) does not
hit the website again. Delete the folder to force a fresh download.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

FACTION_URL = "https://wahapedia.ru/wh40k11ed/factions/adeptus-mechanicus/"
FACTION_PATH = urlparse(FACTION_URL).path.rstrip("/")
USER_AGENT = "Mozilla/5.0 (compatible; personal-datasheet-scraper/1.0)"

log = logging.getLogger("wahapedia")


# --------------------------------------------------------------------------
# Networking (polite, cached, with retries)
# --------------------------------------------------------------------------
class Fetcher:
    def __init__(self, cache_dir: Path, delay: float):
        self.cache_dir = cache_dir
        self.delay = delay
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})

    def _cache_file(self, url: str) -> Path:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", urlparse(url).path.strip("/")) or "index"
        return self.cache_dir / f"{name}.html"

    def get(self, url: str) -> str:
        cached = self._cache_file(url)
        if cached.exists():
            return cached.read_text(encoding="utf-8")
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                r = self.session.get(url, timeout=30)
                if r.status_code == 200:
                    r.encoding = r.encoding or "utf-8"
                    cached.write_text(r.text, encoding="utf-8")
                    time.sleep(self.delay)
                    return r.text
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
            except requests.RequestException as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"Could not fetch {url}: {last_err}")


# --------------------------------------------------------------------------
# HTML -> list of text lines (block elements become lines, inline stay joined)
# --------------------------------------------------------------------------
BLOCK_TAGS = {
    "div", "p", "tr", "table", "tbody", "thead", "ul", "ol", "li", "section",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "blockquote", "dl", "dt", "dd",
}
SKIP_TAGS = {"script", "style", "noscript", "svg", "img", "head"}


def block_lines(root: Tag, mark_bold: bool = False, mark_headings: bool = False) -> list[str]:
    """Flatten *root* into visual lines. Table cells are joined with a TAB."""
    lines: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = "".join(buf)
        buf.clear()
        text = re.sub(r"[^\S\t]+", " ", text.replace("\xa0", " ").replace("\u200b", ""))
        line = "\t".join(p.strip() for p in text.split("\t")).strip("\t ")
        if line.strip():
            lines.append(line)

    def walk(n) -> None:
        if isinstance(n, Comment):
            return
        if isinstance(n, NavigableString):
            buf.append(str(n))
            return
        if not isinstance(n, Tag) or n.name in SKIP_TAGS:
            return
        if n.name == "br":
            flush()
            return
        block = n.name in BLOCK_TAGS
        if block:
            flush()
        if mark_headings and re.fullmatch(r"h[1-6]", n.name):
            buf.append("#" * int(n.name[1]) + " ")
        bold = mark_bold and n.name in ("b", "strong")
        if bold:
            buf.append("**")
        for c in n.children:
            walk(c)
        if bold:
            buf.append("**")
        if n.name in ("td", "th"):
            buf.append("\t")
        if block:
            flush()

    walk(root)
    flush()
    return lines


def plain(s: str) -> str:
    return s.replace("**", "").strip()


def halve(items: list[str]) -> list[str]:
    """Wahapedia repeats some elements twice; collapse [a, b, a, b] -> [a, b]."""
    n = len(items)
    if n >= 2 and n % 2 == 0 and items[: n // 2] == items[n // 2:]:
        return items[: n // 2]
    return items


# --------------------------------------------------------------------------
# Weapon ability helpers
# --------------------------------------------------------------------------
_ABILITY_BODY = "|".join([
    r"anti-[a-z]+(?:\s[a-z]+)?\s\d\+",
    r"sustained\s+hits\s+(?:\d+|d\d)",
    r"rapid\s+fire\s+(?:\d+|d\d(?:\+\d)?)",
    r"melta\s+(?:\d+|d\d)",
    r"blast(?:\s+\d+)?",
    r"assault", r"heavy", r"pistol", r"torrent", r"lethal\s+hits",
    r"devastating\s+wounds", r"twin-linked", r"hazardous", r"ignores\s+cover",
    r"precision", r"lance", r"indirect\s+fire", r"one\s+shot", r"psychic",
    r"extra\s+attacks", r"linked\s+fire",
])
ABILITY_FIND = re.compile(rf"(?<![\w-])(?:{_ABILITY_BODY})(?![\w-])", re.I)
ABILITY_TRAILING = re.compile(rf"\s+((?:{_ABILITY_BODY}))\s*$", re.I)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def split_abilities(text: str) -> list[str]:
    text = text.replace("[", "").replace("]", "").strip()
    if not text:
        return []
    if "," in text:
        return _unique([" ".join(t.split()).upper() for t in text.split(",") if t.strip()])
    found = [m.group(0).upper() for m in ABILITY_FIND.finditer(text)]
    leftover = " ".join(ABILITY_FIND.sub(" ", text).split())
    return _unique(found + ([leftover.upper()] if leftover else []))


def strip_trailing_abilities(text: str) -> tuple[str, list[str]]:
    abil: list[str] = []
    while True:
        m = ABILITY_TRAILING.search(text)
        if not m:
            break
        abil.append(m.group(1).upper())
        text = text[: m.start()]
    return text.strip(), list(reversed(abil))


# --------------------------------------------------------------------------
# Weapon tables
# --------------------------------------------------------------------------
WEAPON_KEYS = ["range", "attacks", "skill", "strength", "ap", "damage"]


def _cells(tr: Tag) -> list[Tag]:
    return tr.find_all(["td", "th"], recursive=False)


def _weapon_from_row(cells: list[Tag], wtype: str) -> dict | None:
    if len(cells) < 7:
        return None
    stats = [" ".join(c.get_text(" ").split()) for c in cells[-6:]]
    if not stats[0] or stats[0].upper() == "RANGE":
        return None
    name_cell = cells[-7]
    lines = halve([plain(l) for l in block_lines(name_cell, mark_bold=True)])
    if not lines:
        return None
    bold = name_cell.find(["b", "strong"])
    name = " ".join(bold.get_text(" ").split()) if bold else lines[0]
    spans = [" ".join(s.get_text(" ").split()) for s in name_cell.select(".kwb, .kwbu")]
    if spans:
        abilities = _unique([s.strip("[] ").upper() for s in spans if s.strip("[] ")])
    else:
        rest = " ".join(lines)
        rest = rest.replace(name, " ") if name else rest
        abilities = split_abilities(rest)
    w = {"name": name, "type": wtype}
    w.update(dict(zip(WEAPON_KEYS, stats)))
    w["skill_type"] = "BS" if wtype == "ranged" else "WS"
    w["abilities"] = abilities
    return w


def weapons_from_tables(container: Tag, whole_page: bool) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"ranged": [], "melee": []}
    for table in container.find_all("table"):
        if table.find("table"):          # only innermost tables
            continue
        rows = table.find_all("tr")
        wtype = None
        for tr in rows:
            texts = [" ".join(c.get_text(" ").split()).upper() for c in _cells(tr)]
            if "RANGE" in texts and "BS" in texts:
                wtype = "ranged"
            elif "RANGE" in texts and "WS" in texts:
                wtype = "melee"
            elif wtype:
                w = _weapon_from_row(_cells(tr), wtype)
                if w and w not in out[wtype]:
                    out[wtype].append(w)
        if whole_page and out["ranged"] and out["melee"]:
            break
    return out


# Fallback when no <table> is found: parse the flattened lines.
_VALUE_PATTERNS = [
    r'(?:\d+"|Melee)', r"(?:\d+|d\d+(?:\+\d+)?)", r"(?:\d\+|N/A|-)",
    r"\d+", r"-?\d+", r"(?:\d+|d\d+(?:\+\d+)?)",
]


def weapons_from_lines(section: list[str], wtype: str) -> list[dict]:
    items: list[str] = []
    for ln in section:
        items += [plain(p) for p in ln.split("\t") if plain(p)]
    items = [i for i in items if i.upper() not in {"RANGE", "A", "BS", "WS", "S", "AP", "D",
                                                    "RANGED WEAPONS", "MELEE WEAPONS"}]
    weapons, pending, i = [], [], 0
    while i < len(items):
        window = items[i:i + 6]
        if len(window) == 6 and all(re.fullmatch(p, v, re.I) for p, v in zip(_VALUE_PATTERNS, window)):
            if pending:
                name, abil = strip_trailing_abilities(pending[0])
                w = {"name": name, "type": wtype}
                w.update(dict(zip(WEAPON_KEYS, window)))
                w["skill_type"] = "BS" if wtype == "ranged" else "WS"
                w["abilities"] = abil
                if w not in weapons:
                    weapons.append(w)
            pending, i = [], i + 6
        else:
            pending.append(items[i])
            i += 1
    return weapons


# --------------------------------------------------------------------------
# Section splitting and field parsers
# --------------------------------------------------------------------------
SECTION_HEADINGS = {
    "RANGED WEAPONS", "MELEE WEAPONS", "WARGEAR OPTIONS", "ABILITIES", "WARGEAR ABILITIES",
    "UNIT COMPOSITION", "STRATAGEMS", "DETACHMENT ABILITY", "LEADER", "LED BY",
    "TRANSPORT", "ERRATA",
}
STAT_LABELS = {
    "M": "movement", "T": "toughness", "SV": "save", "W": "wounds",
    "LD": "leadership", "OC": "objective_control", "INSV": "invulnerable_save",
}


def heading_of(line: str) -> str | None:
    p = plain(line)
    first = p.split("\t")[0].strip().upper()
    if first in SECTION_HEADINGS:
        return first
    if p.upper().startswith("FACTION KEYWORDS:"):
        return "FACTION KEYWORDS"
    if p.upper().startswith("KEYWORDS:"):
        return "KEYWORDS"
    return None


def split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    pre: list[str] = []
    sections: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for ln in lines:
        h = heading_of(ln)
        if h:
            cur = sections.setdefault(h, [])
            if h in ("KEYWORDS", "FACTION KEYWORDS"):
                cur.append(plain(ln).split(":", 1)[1].strip())
            elif len(plain(ln).split("\t")) > 1 and h in ("ABILITIES",):
                cur.append(plain(ln).split("\t", 1)[1])
            continue
        (cur if cur is not None else pre).append(ln)
    return pre, sections


def parse_models(pre: list[str], unit_name: str) -> list[dict]:
    models: list[dict] = []
    cur: dict | None = None
    i = 0
    while i < len(pre):
        label_line = plain(pre[i])
        parts = [p.strip().upper() for p in label_line.split("\t")]
        # Layout A: header row of labels, next row holds the values.
        if len(parts) > 1 and all(p in STAT_LABELS for p in parts) and i + 1 < len(pre):
            vals = [plain(v) for v in pre[i + 1].split("\t")]
            pairs = list(zip(parts, vals))
            i += 2
        # Layout B: label line followed by a value line.
        elif label_line.upper() in STAT_LABELS and i + 1 < len(pre) \
                and plain(pre[i + 1]).upper() not in STAT_LABELS:
            pairs = [(label_line.upper(), plain(pre[i + 1]))]
            i += 2
        # Layout C: "M 10"" on one line.
        elif (m := re.fullmatch(r"(M|T|SV|W|LD|OC|INSV)\s+(\S+)", label_line, re.I)):
            pairs = [(m.group(1).upper(), m.group(2))]
            i += 1
        else:
            i += 1
            continue
        for label, value in pairs:
            if label == "M" and (cur is None or "movement" in cur):
                cur = {"name": unit_name}
                j = i - 1 - 2 * (len(pairs) == 1)   # step back before the label/value
                j = max(j, 0)
                while j >= 0 and plain(pre[j]).startswith("http"):
                    j -= 1
                if j >= 0 and plain(pre[j]).startswith("("):
                    cur["base_size"] = plain(pre[j])
                    j -= 1
                    while j >= 0 and plain(pre[j]).startswith("http"):
                        j -= 1
                if j >= 0 and plain(pre[j]) and plain(pre[j]).upper() not in STAT_LABELS \
                        and not plain(pre[j]).startswith("("):
                    cur["name"] = plain(pre[j])
                models.append(cur)
            if cur is not None:
                cur[STAT_LABELS[label]] = value
    return models


def parse_labelled_list(lines: list[str], labels: set[str]) -> list[str]:
    """Rows like 'CORE ABILITIES<TAB>Deadly Demise 1, Scouts 6"'."""
    result: list[str] = []
    for idx, ln in enumerate(lines):
        parts = [plain(p) for p in ln.split("\t")]
        if parts[0].upper() in labels:
            vals = [p for p in parts[1:] if p]
            if not vals and idx + 1 < len(lines):
                vals = [plain(lines[idx + 1])]
            for v in vals:
                result += [x.strip() for x in re.split(r"[;,]", v) if x.strip()]
    return result


ABIL_BOLD = re.compile(r"^\*\*(?P<n>[^*]+?):?\*\*:?\s*(?P<d>.*)$")
ABIL_PLAIN = re.compile(r"^(?P<n>[A-Z][^:]{0,50}):\s+(?P<d>.*)$")


def parse_named_entries(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    for ln in lines:
        m = ABIL_BOLD.match(ln.strip())
        if not m and not any("**" in l for l in lines):          # page uses no bold markup
            m = ABIL_PLAIN.match(ln.strip())
            if m and len(m.group("n").split()) > 6:
                m = None
        if m:
            entries.append({"name": m.group("n").strip(), "description": m.group("d").strip()})
        elif entries:
            entries[-1]["description"] = (entries[-1]["description"] + " " + ln.strip()).strip()
        else:
            entries.append({"name": plain(ln), "description": ""})
    for e in entries:
        e["description"] = e["description"].replace("**", "").strip()
    return entries


def parse_keywords(lines: list[str]) -> list[str]:
    text = " ".join(plain(l) for l in lines)
    return [k.strip().upper() for k in re.split(r"[;,]", text) if k.strip()]


# --------------------------------------------------------------------------
# Faction page: army rules + datasheet links
# --------------------------------------------------------------------------
def parse_army_rules(faction_html: str) -> dict[str, str]:
    soup = BeautifulSoup(faction_html, "html.parser")
    lines = block_lines(soup, mark_headings=True)
    try:
        start = next(i for i, l in enumerate(lines) if re.fullmatch(r"#{2,3}\s*Army Rules", l.strip(), re.I))
    except StopIteration:
        log.warning("Could not find the 'Army Rules' section on the faction page")
        return {}
    rules: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for ln in lines[start + 1:]:
        if re.match(r"##\s", ln):                       # next top-level section
            break
        if ln.startswith("### "):
            title = ln[4:].strip()
            cur = None if title.lower() == "errata" else rules.setdefault(title, [])
            continue
        if cur is not None:
            cur.append(ln.replace("\t", " ").strip())
    return {name: "\n".join(body).strip() for name, body in rules.items()}


def discover_datasheets(faction_html: str) -> list[dict]:
    soup = BeautifulSoup(faction_html, "html.parser")
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        url, _ = urldefrag(urljoin(FACTION_URL, a["href"]))
        p = urlparse(url)
        path = p.path.rstrip("/")
        if p.netloc != "wahapedia.ru" or not path.startswith(FACTION_PATH + "/"):
            continue
        slug = path[len(FACTION_PATH) + 1:]
        if not slug or "/" in slug:
            continue
        prev = a.find_previous_sibling()
        legends = bool(prev is not None and prev.name == "img" and
                       "legends" in (str(prev.get("src", "")) + str(prev.get("title", ""))).lower())
        entry = found.setdefault(url, {"url": url, "slug": slug, "legends": False})
        entry["legends"] = entry["legends"] or legends
    return list(found.values())


# --------------------------------------------------------------------------
# One datasheet page
# --------------------------------------------------------------------------
def pick_container(soup: BeautifulSoup) -> tuple[Tag, bool]:
    for sel in ("div.dsOuterFrame", "div.datasheet", "div.dsOuter"):
        for el in soup.select(sel):
            if "KEYWORDS" in el.get_text().upper():
                return el, False
    return (soup.body or soup), True


def parse_datasheet(html: str, url: str, army_rule_text: dict[str, str]) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    h1 = soup.find("h1")
    title = " ".join(h1.get_text(" ").split()) if h1 else url.rstrip("/").rsplit("/", 1)[-1]
    name = re.split(r"\s[–—-]\s", title, maxsplit=1)[-1].strip()

    container, whole_page = pick_container(soup)
    lines = block_lines(container, mark_bold=True)
    pre, sections = split_sections(lines)

    models = parse_models(pre, name)

    weapons = weapons_from_tables(container, whole_page)
    if not weapons["ranged"] and "RANGED WEAPONS" in sections:
        weapons["ranged"] = weapons_from_lines(sections["RANGED WEAPONS"], "ranged")
    if not weapons["melee"] and "MELEE WEAPONS" in sections:
        weapons["melee"] = weapons_from_lines(sections["MELEE WEAPONS"], "melee")

    core = parse_labelled_list(lines, {"CORE ABILITIES"})
    army_names = parse_labelled_list(lines, {"ARMY RULES"})
    army_rules = [{"name": n} for n in army_names if n in army_rule_text]

    legends = bool(container.find("img", src=re.compile("legends", re.I)) or
                   container.find("img", title=re.compile("legends", re.I)))

    return {
        "name": name,
        "url": url,
        "legends": legends,
        "models": models,
        "weapons": weapons,
        "army_rules": army_rules,
        "core_abilities": core,
        "abilities": parse_named_entries(sections.get("ABILITIES", [])),
        "wargear_abilities": parse_named_entries(sections.get("WARGEAR ABILITIES", [])),
        "keywords": parse_keywords(sections.get("KEYWORDS", [])),
        "faction_keywords": parse_keywords(sections.get("FACTION KEYWORDS", [])),
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=FACTION_URL)
    ap.add_argument("--out", default="adeptus_mechanicus_datasheets.json")
    ap.add_argument("--cache-dir", default="cache")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--limit", type=int, default=0, help="only scrape the first N datasheets (testing)")
    ap.add_argument("--no-legends", action="store_true", help="skip Warhammer Legends datasheets")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    fetcher = Fetcher(Path(args.cache_dir), args.delay)

    log.info("Fetching faction page …")
    faction_html = fetcher.get(args.url)
    army_rule_text = parse_army_rules(faction_html)
    log.info("Army rules found: %s", ", ".join(army_rule_text) or "none")

    sheets = discover_datasheets(faction_html)
    if args.no_legends:
        sheets = [s for s in sheets if not s["legends"]]
    if args.limit:
        sheets = sheets[: args.limit]
    if len(sheets) < 5:
        log.warning("Only %d datasheet links found – the page layout may have changed.", len(sheets))
    log.info("%d datasheets to scrape", len(sheets))

    results, suspicious = [], []
    for n, s in enumerate(sheets, 1):
        log.info("[%d/%d] %s", n, len(sheets), s["slug"])
        try:
            ds = parse_datasheet(fetcher.get(s["url"]), s["url"], army_rule_text)
        except Exception as e:                      # keep going on a single failure
            log.error("  failed: %s", e)
            continue
        ds["legends"] = ds["legends"] or s["legends"]
        if not ds["models"] or not ds["keywords"]:
            suspicious.append(ds["name"])
        results.append(ds)

    payload = {
        "faction": "Adeptus Mechanicus",
        "edition": "11th",
        "source": args.url,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "datasheet_count": len(results),
        "faction_rules": [{"name": n, "description": d} for n, d in army_rule_text.items()],
        "datasheets": results,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %d datasheets to %s", len(results), args.out)
    if suspicious:
        log.warning("No statline/keywords parsed for: %s – check the cached HTML in %s/",
                    ", ".join(suspicious), args.cache_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
