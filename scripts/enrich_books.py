# -*- coding: utf-8 -*-
"""
经典著作条目外部补全（维基 opensearch 定位 + Tavily extract 出版年核验）。

对 books.csv 的每本著作（title_en + 作者 + 记录年份）：
  1. opensearch 定位维基百科条目页（标题词覆盖匹配，防误配到同名电影/事件）
  2. Tavily extract 该页（缓存 data/enrich_cache/）→ infobox Published/Publication_date 行解析出版年
  3. 出版年与库内记录对照：一致 → verified；±2 年 → near_match；其余 → mismatch（两边都保留，不覆盖）
产物：data/csv/books_external.csv（旁挂表，核心 CSV 零改动）：
  book_id, wikipedia_url, found_year, year_status, evidence_url, enriched_at

用法：TAVILY_API_KEY=xxx python enrich_books.py [--csv ../news-history-kg/data/csv/books.csv] [--workers 4]
"""
from __future__ import annotations

import argparse
import csv as _csv
import hashlib
import os
import re
import sys
import time
import concurrent.futures as cf
from pathlib import Path
from urllib.parse import quote

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SEARCH_PATH = "/search"
EXTRACT_PATH = "/extract"
UA = {"User-Agent": "news-kg-book-enrichment/1.0"}
_Y = r"(?:1[5-9]\d{2}|20[0-2]\d)"


def norm_title(t: str) -> set[str]:
    stop = {"a", "an", "the", "of", "in", "on", "and", "de", "la", "le"}
    words = re.findall(r"[a-z]+", (t or "").lower())
    return {w for w in words if w not in stop and len(w) > 2}


def tavily(path: str, payload: dict, key: str, timeout: int = 90, retries: int = 3) -> dict:
    payload = {"api_key": key, **payload}
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(f"https://api.tavily.com{path}", json=payload, timeout=timeout)
            if r.status_code == 429:
                raise RuntimeError("rate_limited")
            r.raise_for_status()
            return r.json()
        except RuntimeError:
            time.sleep(6 + 4 * attempt)
        except Exception as e:
            last = e
            time.sleep(2 + 3 * attempt)
    raise last if last else RuntimeError("tavily_failed")


def opensearch(query: str) -> list[tuple[str, str]]:
    """返回 [(title, url)]，失败空表。"""
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": 6,
                    "format": "json", "redirects": "resolve"},
            headers=UA, timeout=20)
        r.raise_for_status()
        data = r.json()
        return list(zip(data[1], data[3])) if len(data) > 3 else []
    except Exception:
        return []


def strip_md(t: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)


def find_pub_year(raw: str) -> str:
    """infobox 出版行优先（注意：维基标签用不换行空格 \xa0，先归一化），其次导语括号年份。"""
    t = strip_md(raw).replace("\xa0", " ")
    labels = (r"Publica[tion]*[ _]date", r"Published", r"出版时间", r"出版年", r"出版信息", r"发表")
    for label in labels:
        m = re.search(rf"\|\s*{label}\s*\|[^\n|]{{0,80}}", t)
        if m:
            y = re.search(_Y, m.group(0))
            if y and 1450 <= int(y.group(0)) <= 2026:
                return y.group(0)
    m = re.search(rf"[（(][^()（）]{{0,60}}({_Y})[^()（）]{{0,60}}[)）]", t[:600])
    if m and 1450 <= int(m.group(1)) <= 2026:
        return m.group(1)
    return ""


def enrich_one(book: dict, scholar: dict | None, key: str, cache_dir: Path) -> dict:
    out = {"book_id": book["book_id"], "wikipedia_url": "", "found_year": "",
           "year_status": "", "evidence_url": "", "enriched_at": time.strftime("%Y-%m-%d")}
    title = (book.get("title_en") or book.get("title_zh") or "").strip()
    if not title:
        out["year_status"] = "no_title"
        return out
    author = ""
    if scholar and scholar.get("name_en"):
        author = re.split(r"\s+", scholar["name_en"].strip())[-1]  # 姓氏粗取
    recorded = (book.get("year") or "").strip()

    # 1) 定位：opensearch（本机直连不稳，两轮重试）→ Tavily search 兜底（服务端稳定）
    #    词覆盖匹配防同名电影/事件误配
    want = norm_title(title)

    def pick(cands):
        for wt, wu in cands:
            if not re.match(r"https://(?:en|zh)\.wikipedia\.org/wiki/", wu):
                continue
            seg = wu.split("/wiki/")[-1]
            try:
                from urllib.parse import unquote
                wt2 = unquote(seg).replace("_", " ")
            except Exception:
                wt2 = wt
            got = norm_title(wt2) | norm_title(wt2.split("(")[0])
            if want and want <= got:
                return wu
        return ""

    wiki_url = pick(opensearch(f"{title} {author}".strip()) if author else opensearch(title))
    if not wiki_url:
        wiki_url = pick(opensearch(f"{title} book"))
    if not wiki_url:
        try:
            data = tavily(SEARCH_PATH, {
                "query": f"{title} {author} book".strip(),
                "include_domains": ["wikipedia.org"],
                "search_depth": "basic", "max_results": 8,
            }, key, timeout=60, retries=2)
            cands = [(r.get("url", ""), r.get("url", "")) for r in data.get("results", [])]
            wiki_url = pick(cands)
        except Exception:
            pass
    if not wiki_url:
        out["year_status"] = "no_wiki_hit"
        return out
    out["wikipedia_url"] = out["evidence_url"] = wiki_url

    # 2) 出版年（extract 缓存）
    cache_f = cache_dir / (hashlib.md5(wiki_url.encode()).hexdigest() + ".txt")
    raw = ""
    if cache_f.exists():
        try:
            raw = cache_f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw = ""
    if not raw:
        try:
            ex = tavily(EXTRACT_PATH, {"urls": [wiki_url], "extract_depth": "basic"}, key)
            raw = (ex.get("results") or [{}])[0].get("raw_content", "") or ""
            if raw:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_f.write_text(raw, encoding="utf-8")
        except Exception:
            pass
    if not raw:
        out["year_status"] = "wiki_found_extract_failed"
        return out
    found = find_pub_year(raw)
    out["found_year"] = found
    if not found:
        out["year_status"] = "no_year_found"
    elif not recorded:
        out["year_status"] = "found_no_record"
    elif int(found) == int(recorded):
        out["year_status"] = "verified"
    elif abs(int(found) - int(recorded)) <= 2:
        out["year_status"] = "near_match"
    else:
        out["year_status"] = "mismatch"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "csv" / "books.csv"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        sys.exit("缺少 TAVILY_API_KEY")

    csv_path = Path(args.csv)
    data_dir = csv_path.parent
    out_path = data_dir / "books_external.csv"
    cache_dir = data_dir.parent / "enrich_cache"

    books = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        books = [r for r in _csv.DictReader(f) if r.get("title_en") or r.get("title_zh")]
    scholars = {}
    sch_path = data_dir / "scholars.csv"
    if sch_path.exists():
        with open(sch_path, encoding="utf-8-sig", newline="") as f:
            scholars = {r["scholar_id"]: r for r in _csv.DictReader(f)}
    done = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8-sig", newline="") as f:
            done = {r["book_id"] for r in _csv.DictReader(f)}
    todo = [b for b in books if b["book_id"] not in done]
    print(f"[books] {len(books)} 本，待补 {len(todo)}（缓存跳过 {len(done)}）", flush=True)

    t0 = time.time()
    counters = {}

    def run(b):
        try:
            r = enrich_one(b, scholars.get(b.get("scholar_id")), key, cache_dir)
        except Exception as e:
            r = {"book_id": b["book_id"], "wikipedia_url": "", "found_year": "",
                 "year_status": f"error:{type(e).__name__}", "evidence_url": "",
                 "enriched_at": time.strftime("%Y-%m-%d")}
        counters[r["year_status"].split(":")[0]] = counters.get(r["year_status"].split(":")[0], 0) + 1
        return r

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run, b) for b in todo]
        for n, fut in enumerate(cf.as_completed(futs), 1):
            results.append(fut.result())
            if n % 20 == 0:
                print(f"[books] {n}/{len(todo)} | {time.time()-t0:.0f}s | {counters}", flush=True)

    all_rows = []
    if out_path.exists():
        with open(out_path, encoding="utf-8-sig", newline="") as f:
            old = {r["book_id"]: r for r in _csv.DictReader(f)}
        all_rows = list(old.values())
    by_id = {r["book_id"]: r for r in all_rows}
    for r in results:
        by_id[r["book_id"]] = r
    fields = ["book_id", "wikipedia_url", "found_year", "year_status", "evidence_url", "enriched_at"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for bid in sorted(by_id):
            w.writerow({k: by_id[bid].get(k, "") for k in fields})

    n = len(by_id)
    has_url = sum(1 for r in by_id.values() if r.get("wikipedia_url"))
    print(f"[done] {out_path.name}: wiki链接 {has_url}/{n} | 状态分布 {counters} | {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
