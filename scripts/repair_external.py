# -*- coding: utf-8 -*-
"""Repair scholar and book Wikipedia side tables without Tavily.

Clears pages that are a different person or a non-book, then fills the URL
and years from Wikidata. Default is a dry run; pass --write to save CSVs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests

from wiki_match import (
    claim_ids,
    claim_year,
    description_text,
    en_name_in_labels,
    identity_match,
    is_human,
    is_work,
    label_list,
    lifespan_ok,
    norm_latin,
    reject_person_title,
    titles_compatible,
    tokens,
    year_status,
    zh_name_in_labels,
    zh_title_exact,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
# Generic search returns a namesake. These queries name the person this KG means.
EXTRA_QUERY = {
    "SCH_CAREY": ("James W. Carey", "en"),
    "SCH_PARK": ("Robert E. Park", "en"),
    "SCH_PAGE": ("Arthur W. Page", "en"),
    "SCH_MURDOCK": ("Graham Murdock", "en"),
    "SCH_BURSON": ("Harold Burson", "en"),
    "SCH_DAN_SCHILLER": ("Dan Schiller", "en"),
    "SCH_WHITE": ("David Manning White", "en"),
    "SCH_MORLEY": ("David Morley sociologist", "en"),
    "SCH_TICHENOR": ("Phillip J. Tichenor", "en"),
    "SCH_NYSTROM": ("Christine Nystrom", "en"),
    "SCH_LI_LIANGRONG": ("李良荣 新闻学", "zh"),
}
CSV_DIR = ROOT / "data" / "csv"
UA = "news-journalism-kg/1.0 (local research repair)"
TODAY_REJECT_YEAR = 2026


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


class Wiki:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.cache_dir = ROOT / "data" / "enrich_cache" / "wiki_api"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._next_ok = 0.0

    def get(self, url: str, params: dict) -> dict:
        key = hashlib.md5(json.dumps({"url": url, "params": params}, sort_keys=True).encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        last: Exception | None = None
        for attempt in range(4):
            wait = self._next_ok - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.get(url, params=params, timeout=40)
                self._next_ok = time.time() + 1.1
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "30")
                    try:
                        delay = max(int(retry_after), 20)
                    except ValueError:
                        delay = 30
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                data = response.json()
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(5 + attempt * 5)
        raise last if last else RuntimeError("wiki_get_failed")

    def resolve(self, urls: list[str]) -> dict[str, dict]:
        grouped: dict[str, list[str]] = {}
        for url in urls:
            if not url or "/wiki/" not in url:
                continue
            host = urlparse(url).netloc
            title = unquote(urlparse(url).path.split("/wiki/", 1)[-1]).replace("_", " ")
            grouped.setdefault(host, []).append(title)
        found: dict[str, dict] = {}
        for host, titles in grouped.items():
            unique = list(dict.fromkeys(titles))
            for start in range(0, len(unique), 20):
                chunk = unique[start : start + 20]
                try:
                    data = self.get(
                        f"https://{host}/w/api.php",
                        {
                            "action": "query",
                            "prop": "pageprops",
                            "ppprop": "wikibase_item",
                            "redirects": 1,
                            "format": "json",
                            "titles": "|".join(chunk),
                        },
                    )
                except (requests.RequestException, ValueError, RuntimeError):
                    for title in chunk:
                        found[f"{host}|{title}"] = {"error": True}
                    continue
                found.update(self._map_pages(host, data))
        return found

    def _map_pages(self, host: str, data: dict) -> dict[str, dict]:
        query = data.get("query", {})
        redirects = {item["from"]: item["to"] for item in query.get("redirects", [])}
        normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
        by_title: dict[str, dict] = {}
        for page in query.get("pages", {}).values():
            title = page.get("title") or ""
            if "missing" in page:
                by_title[title] = {"missing": True}
            else:
                by_title[title] = {"qid": (page.get("pageprops") or {}).get("wikibase_item") or ""}
        mapped: dict[str, dict] = {}
        for source in list(normalized) + list(redirects) + list(by_title):
            title = normalized.get(source, source)
            seen: set[str] = set()
            while title in redirects and title not in seen:
                seen.add(title)
                title = redirects[title]
            info = by_title.get(title, {"missing": True})
            mapped[f"{host}|{source}"] = info
            mapped[f"{host}|{title}"] = info
        return mapped

    def entities(self, qids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        clean = [qid for qid in dict.fromkeys(qids) if qid and qid.startswith("Q")]
        for start in range(0, len(clean), 40):
            chunk = clean[start : start + 40]
            data = self.get(
                "https://www.wikidata.org/w/api.php",
                {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "labels|aliases|descriptions|claims|sitelinks",
                    "languages": "en|zh",
                    "sitefilter": "enwiki|zhwiki|simplewiki",
                    "format": "json",
                },
            )
            out.update(data.get("entities", {}))
        return out

    def search(self, text: str, lang: str) -> list[str]:
        if not text:
            return []
        data = self.get(
            "https://www.wikidata.org/w/api.php",
            {
                "action": "wbsearchentities",
                "search": text,
                "language": lang,
                "uselang": lang,
                "type": "item",
                "limit": 6,
                "format": "json",
            },
        )
        return [hit["id"] for hit in data.get("search", []) if hit.get("id")]

    def opensearch(self, lang: str, query: str) -> list[str]:
        if not query:
            return []
        data = self.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            {
                "action": "opensearch",
                "search": query,
                "limit": 5,
                "format": "json",
                "redirects": "resolve",
            },
        )
        return list(data[3]) if len(data) > 3 else []


def wiki_url(entity: dict) -> str:
    links = entity.get("sitelinks") or {}
    for site, lang in (("enwiki", "en"), ("zhwiki", "zh"), ("simplewiki", "simple")):
        title = (links.get(site) or {}).get("title")
        if not title:
            continue
        slug = quote(title.replace(" ", "_"), safe="()!'.,")
        return f"https://{lang}.wikipedia.org/wiki/{slug}"
    return ""


def title_of(url: str) -> str:
    if "/wiki/" not in (url or ""):
        return ""
    return unquote(urlparse(url).path.split("/wiki/", 1)[-1]).replace("_", " ")


def author_matches(name_en: str, name_zh: str, blob: str) -> bool:
    if name_zh and name_zh in (blob or ""):
        return True
    folded = norm_latin(blob or "")
    return any(len(token) >= 4 and token in folded for token in tokens(name_en))


def distinctive_title(title_en: str, title_zh: str, labels_en: list[str], labels_zh: list[str]) -> bool:
    core = len(re.sub(r"[《》\s]", "", title_zh or ""))
    if core >= 4 and any(zh_title_exact(title_zh, label) for label in labels_zh):
        return True
    return len(tokens((title_en or "").split(":")[0], drop_stop=True)) >= 3 and any(
        titles_compatible(title_en, label) for label in labels_en
    )


def score_person(entity: dict, name_en: str, name_zh: str, active: str) -> tuple | None:
    if not is_human(entity):
        return None
    labels_en = label_list(entity, "en")
    labels_zh = label_list(entity, "zh")
    if not identity_match(name_en, name_zh, labels_en, labels_zh):
        return None
    birth = claim_year(entity, "P569")
    death = claim_year(entity, "P570", reject_year=TODAY_REJECT_YEAR)
    if not lifespan_ok(birth, active):
        return None
    url = wiki_url(entity)
    if not url or reject_person_title(title_of(url)):
        return None
    score = 0
    if en_name_in_labels(name_en, labels_en):
        score += 5
    if zh_name_in_labels(name_zh, labels_zh):
        score += 5
    if "enwiki" in (entity.get("sitelinks") or {}):
        score += 2
    if birth:
        score += 1
    blob = description_text(entity)
    latin = set(norm_latin(blob).split())
    zh_hit = any(word in blob for word in ("记者", "新闻", "传播", "媒介", "社会学", "哲学家", "报人", "文化研究"))
    en_hit = bool(latin & {
        "journalist", "newspaper", "publisher", "communication", "media",
        "sociologist", "philosopher", "cultural", "press", "broadcast", "broadcasting",
    })
    if zh_hit or en_hit:
        score += 6
    other_field = latin & {
        "basketball", "football", "baseball", "racing", "tennis", "musician",
        "singer", "actor", "actress", "comedian", "judge", "empress", "emperor",
        "monarch", "pathologist", "coach",
    }
    bad_role = re.search(
        r"\((?:basketball|american football|football|actor|judge|musician|singer|racing driver|table tennis|writer|radio)\)",
        title_of(url),
        re.IGNORECASE,
    )
    if (other_field or bad_role) and not (zh_hit or en_hit):
        return None
    if (
        not (zh_hit or en_hit)
        and death.isdigit()
        and str(active).isdigit()
        and int(active) > int(death) + 5
    ):
        return None
    return score, url, birth, death, entity.get("id", "")


def author_blob(entity: dict, entities: dict[str, dict]) -> str:
    parts = [description_text(entity)]
    for author_id in claim_ids(entity, "P50"):
        author = entities.get(author_id) or {}
        parts.extend(label_list(author, "en"))
        parts.extend(label_list(author, "zh"))
    return " ".join(parts)


def score_work(entity: dict, book: dict, scholar: dict, entities: dict[str, dict]) -> tuple | None:
    if not is_work(entity):
        return None
    title_en = book.get("title_en") or ""
    title_zh = book.get("title_zh") or ""
    labels_en = label_list(entity, "en") + [title_of(wiki_url(entity))]
    labels_zh = label_list(entity, "zh")
    sitelink_titles = [item.get("title", "") for item in (entity.get("sitelinks") or {}).values()]
    title_ok = any(titles_compatible(title_en, label) for label in labels_en + sitelink_titles) or any(
        zh_title_exact(title_zh, label) for label in labels_zh + sitelink_titles
    )
    if not title_ok:
        return None
    blob = author_blob(entity, entities)
    matched_author = author_matches(scholar.get("name_en", ""), scholar.get("name_zh", ""), blob)
    if not matched_author and not distinctive_title(title_en, title_zh, labels_en, labels_zh):
        return None
    url = wiki_url(entity)
    if not url:
        return None
    found = claim_year(entity, "P577")
    score = (3 if matched_author else 0) + (2 if "enwiki" in (entity.get("sitelinks") or {}) else 0) + (1 if found else 0)
    return score, url, found


def best_scored(options: list[tuple]) -> tuple | None:
    if not options:
        return None
    options = sorted(options, key=lambda item: (-item[0], item[1]))
    if len(options) > 1 and options[0][0] == options[1][0] and options[0][1] != options[1][1]:
        return None
    return options[0]


def pick_person(options: list[tuple], active: str) -> tuple | None:
    """Prefer the journalism-related namesake closest to mid-career at active_year."""
    if not options:
        return None

    def sort_key(item: tuple) -> tuple:
        score, url, birth = item[0], item[1], item[2]
        gap = 999
        if birth.isdigit() and str(active).isdigit():
            gap = abs(int(active) - int(birth) - 40)
        return (-score, gap, url)

    ordered = sorted(options, key=sort_key)
    if len(ordered) > 1 and sort_key(ordered[0]) == sort_key(ordered[1]) and ordered[0][1] != ordered[1][1]:
        return None
    # A namesake with no journalism description is only acceptable when it is the sole hit.
    if ordered[0][0] < 11 and len(ordered) > 1:
        boosted = [item for item in ordered if item[0] >= 11]
        if not boosted:
            return None
        return boosted[0]
    return ordered[0]


def choose_from_opensearch(wiki: Wiki, scholar_id: str, name_en: str, name_zh: str, active: str) -> tuple | None:
    queries = []
    if name_en:
        queries.append((name_en, "en"))
    if name_zh:
        queries.append((name_zh, "zh"))
    extra = EXTRA_QUERY.get(scholar_id)
    if extra:
        queries.append(extra)
    urls: list[str] = []
    for text, lang in queries:
        urls.extend(wiki.opensearch(lang, text))
    resolved = wiki.resolve(urls)
    found = wiki.entities([info.get("qid", "") for info in resolved.values()])
    options = []
    for item in found.values():
        hit = score_person(item, name_en, name_zh, active)
        if hit:
            options.append(hit)
    return pick_person(options, active)


def repair_scholars(wiki: Wiki, today: str, write: bool) -> None:
    scholars = read_csv(CSV_DIR / "scholars.csv")
    path = CSV_DIR / "scholars_external.csv"
    rows = read_csv(path)
    by_id = {row["scholar_id"]: row for row in rows}
    resolved = wiki.resolve([row.get("wikipedia_url", "") for row in rows])
    current_qids = []
    located: dict[str, dict] = {}
    for row in rows:
        url = row.get("wikipedia_url", "")
        host = urlparse(url).netloc
        title = title_of(url)
        located[row["scholar_id"]] = resolved.get(f"{host}|{title}", {"missing": True}) if url else {"missing": True}
        qid = located[row["scholar_id"]].get("qid") or ""
        if qid:
            current_qids.append(qid)
    entities = wiki.entities(current_qids)
    changes = []
    for scholar in scholars:
        sid = scholar["scholar_id"]
        row = by_id.get(sid)
        if not row:
            continue
        info = located.get(sid) or {}
        if info.get("error"):
            print(f"SKIP {sid} resolve error")
            continue
        entity = entities.get(info.get("qid") or "")
        current = score_person(entity, scholar.get("name_en", ""), scholar.get("name_zh", ""), scholar.get("active_year", "")) if entity else None
        if current and row.get("birth_year"):
            continue
        if current and not row.get("birth_year") and current[2]:
            row["birth_year"], row["death_year"] = current[2], current[3]
            row["method"] = (row.get("method") or "wiki") + "+wikidata_years"
            row["enriched_at"] = today
            changes.append(f"YEARS {sid} {scholar['name_zh']} {current[2]}-{current[3]} {title_of(row['wikipedia_url'])}")
            continue
        if current:
            continue
        name_en = scholar.get("name_en", "")
        name_zh = scholar.get("name_zh", "")
        active = scholar.get("active_year", "")

        def gather(queries: list[tuple[str, str]]) -> list[tuple]:
            ids: list[str] = []
            for text, lang in queries:
                ids.extend(wiki.search(text, lang))
            found = wiki.entities(ids)
            scored = []
            for item in found.values():
                hit = score_person(item, name_en, name_zh, active)
                if hit:
                    scored.append(hit)
            return scored

        try:
            options = gather([(name_en, "en"), (name_zh, "zh")])
            chosen = pick_person(options, active)
            if not chosen:
                extra = []
                if name_en:
                    extra.append((f"{name_en} communication", "en"))
                if name_zh:
                    extra.append((f"{name_zh} 记者", "zh"))
                options = gather(extra)
            chosen = pick_person(options, active)
            if not chosen:
                chosen = choose_from_opensearch(wiki, sid, name_en, name_zh, active)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            print(f"SKIP {sid} search error {type(exc).__name__}")
            continue
        old = title_of(row.get("wikipedia_url", ""))
        if not chosen:
            if row.get("wikipedia_url"):
                row.update({"wikipedia_url": "", "birth_year": "", "death_year": "", "evidence_url": "", "method": "no_wiki_hit", "enriched_at": today})
                changes.append(f"CLEAR {sid} {scholar['name_zh']} was {old}")
            continue
        _score, url, birth, death, _qid = chosen
        row.update({
            "wikipedia_url": url,
            "birth_year": birth,
            "death_year": death,
            "evidence_url": url,
            "method": "wikidata_repair",
            "enriched_at": today,
        })
        changes.append(f"REPLACE {sid} {scholar['name_zh']} {old} -> {title_of(url)} {birth}-{death}")
    print(f"[scholars] {len(changes)} changes")
    for line in changes:
        print(" ", line)
    if write:
        fields = ["scholar_id", "wikipedia_url", "birth_year", "death_year", "evidence_url", "method", "enriched_at"]
        ordered = [by_id[sid] for sid in sorted(by_id)]
        write_csv(path, ordered, fields)


def load_works(wiki: Wiki, qids: list[str]) -> dict[str, dict]:
    found = wiki.entities(qids)
    authors = [aid for item in found.values() for aid in claim_ids(item, "P50")]
    found.update(wiki.entities(authors))
    return found


def search_work(wiki: Wiki, book: dict, scholar: dict) -> tuple | None:
    title_en = book.get("title_en") or ""
    title_zh = book.get("title_zh") or ""
    author = scholar.get("name_en") or scholar.get("name_zh") or ""
    ids = wiki.search(title_en, "en") + wiki.search(title_zh, "zh")
    found = load_works(wiki, ids)
    options = [scored for item in found.values() if (scored := score_work(item, book, scholar, found))]
    if options:
        return best_scored(options)
    short = title_en.split(":")[0].strip()
    surname = author.split()[-1] if author else ""
    urls = (
        wiki.opensearch("en", short)
        + wiki.opensearch("en", f"{short} book")
        + wiki.opensearch("en", f"{short} {surname}".strip())
        + wiki.opensearch("zh", title_zh)
    )
    resolved = wiki.resolve(urls)
    extra = load_works(wiki, [info.get("qid", "") for info in resolved.values()])
    options = [scored for item in extra.values() if (scored := score_work(item, book, scholar, extra))]
    return best_scored(options)


def repair_books(wiki: Wiki, today: str, write: bool) -> None:
    books = read_csv(CSV_DIR / "books.csv")
    # duplicate book_id: keep the later, longer row
    books_by_id: dict[str, dict] = {}
    for book in books:
        books_by_id[book["book_id"]] = book
    scholars = {row["scholar_id"]: row for row in read_csv(CSV_DIR / "scholars.csv")}
    path = CSV_DIR / "books_external.csv"
    rows = read_csv(path)
    by_id = {row["book_id"]: row for row in rows}
    resolved = wiki.resolve([row.get("wikipedia_url", "") for row in rows])
    qids = []
    located: dict[str, dict] = {}
    for row in rows:
        url = row.get("wikipedia_url", "")
        host = urlparse(url).netloc
        info = resolved.get(f"{host}|{title_of(url)}", {"missing": True}) if url else {"missing": True}
        located[row["book_id"]] = info
        if info.get("qid"):
            qids.append(info["qid"])
    entities = wiki.entities(qids)
    author_ids = []
    for qid, entity in entities.items():
        author_ids.extend(claim_ids(entity, "P50"))
    entities.update(wiki.entities(author_ids))
    changes = []
    for book_id, book in books_by_id.items():
        row = by_id.get(book_id)
        if not row:
            continue
        status = row.get("year_status") or ""
        if status in {"verified", "near_match"}:
            continue
        info = located.get(book_id) or {}
        if info.get("error"):
            print(f"SKIP {book_id} resolve error")
            continue
        scholar = scholars.get(book.get("scholar_id"), {})
        entity = entities.get(info.get("qid") or "")
        current = score_work(entity, book, scholar, entities) if entity else None
        if current and not row.get("found_year") and current[2]:
            row["found_year"] = current[2]
            row["year_status"] = year_status(book.get("year", ""), current[2])
            row["enriched_at"] = today
            changes.append(f"YEAR {book_id} {book['title_zh']} {row['year_status']} {current[2]}")
            continue
        if current:
            continue
        if status == "mismatch" and row.get("wikipedia_url"):
            print(f"REVIEW {book_id} {book['title_zh']} kept mismatch {title_of(row['wikipedia_url'])}")
            continue
        try:
            chosen = search_work(wiki, book, scholar)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            print(f"SKIP {book_id} search error {type(exc).__name__}")
            continue
        old = title_of(row.get("wikipedia_url", ""))
        if not chosen:
            if row.get("wikipedia_url") or status != "no_wiki_hit":
                row.update({"wikipedia_url": "", "found_year": "", "year_status": "no_wiki_hit", "evidence_url": "", "enriched_at": today})
                changes.append(f"CLEAR {book_id} {book['title_zh']} was {old or status}")
            continue
        _score, url, found_year = chosen
        row.update({
            "wikipedia_url": url,
            "found_year": found_year,
            "year_status": year_status(book.get("year", ""), found_year),
            "evidence_url": url,
            "enriched_at": today,
        })
        changes.append(f"REPLACE {book_id} {book['title_zh']} {old or status} -> {title_of(url)} {found_year} {row['year_status']}")
    print(f"[books] {len(changes)} changes")
    for line in changes:
        print(" ", line)
    if write:
        fields = ["book_id", "wikipedia_url", "found_year", "year_status", "evidence_url", "enriched_at"]
        ordered = [by_id[bid] for bid in sorted(by_id)]
        write_csv(path, ordered, fields)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--only", choices=["scholars", "books", "all"], default="all")
    args = parser.parse_args()
    today = time.strftime("%Y-%m-%d")
    wiki = Wiki()
    if args.only in {"scholars", "all"}:
        repair_scholars(wiki, today, args.write)
    if args.only in {"books", "all"}:
        repair_books(wiki, today, args.write)
    if not args.write:
        print("[dry-run] pass --write to save")


if __name__ == "__main__":
    main()
