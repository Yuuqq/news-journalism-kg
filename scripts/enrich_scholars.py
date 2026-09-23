# -*- coding: utf-8 -*-
"""
学者条目外部补全（Tavily search + extract）。

对 scholars.csv 的每位学者（按 name_en）：
  1. Tavily search（include_domains=wikipedia.org）定位维基百科页面
  2. 从结果摘要解析生卒年；解析不到时对该页做一次 Tavily extract 再解析
产物：data/csv/scholars_external.csv（旁挂表，不改核心 CSV）——
  scholar_id, wikipedia_url, birth_year, death_year, evidence_url, method, enriched_at
  每行事实的证据 URL 即 evidence_url/wikipedia_url，符合本 KG「断言可溯源」口径。
幂等：sidecar 里已有的 scholar_id 跳过。

用法：
  TAVILY_API_KEY=xxx python enrich_scholars.py [--csv data/csv/scholars.csv] [--workers 6] [--limit N]
  # 也适用于父项目：--csv ../news-history-kg/data/csv/scholars.csv --out ../news-history-kg/data/csv/scholars_external.csv
"""
from __future__ import annotations

import argparse
import csv
import concurrent.futures as cf
import csv as _csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE_DIR = ROOT / "data" / "enrich_cache"

SEARCH_PATH = "/search"
EXTRACT_PATH = "/extract"

# include_domains 不能完全信（会漏进 apsanet.org / 其他语言维基等），客户端严格过滤
WIKI_LANG_PRIORITY = ("en", "zh", "simple")


def pick_wiki_result(results: list[dict]) -> tuple[str, str]:
    """按语言优先级挑维基 URL；返回 (url, snippet_content)。无合格项返回 ('','')."""
    best_url, best_content, best_rank = "", "", 99
    for res in results:
        u = res.get("url", "")
        m = re.match(r"https://([a-z]+)\.wikipedia\.org/wiki/([^:]+)$", u.split("?")[0].split("#")[0])
        if not m:
            continue
        lang = m.group(1)
        if lang not in WIKI_LANG_PRIORITY:
            continue
        rank = WIKI_LANG_PRIORITY.index(lang)
        if rank < best_rank:
            best_url, best_content, best_rank = u.split("?")[0].split("#")[0], res.get("content", "") or "", rank
            if rank == 0:
                break
    return best_url, best_content


_MONTH_ALT = "January|February|March|April|May|June|July|August|September|October|November|December"
_Y = r"(?:1[4-9]\d{2}|20[0-2]\d)"


def _strip_md(t: str) -> str:
    """剥掉 Markdown 图片/链接（保留链接文字），避免嵌套括号破坏日期解析。"""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)


def tavily(path: str, payload: dict, key: str, timeout: int = 60, retries: int = 3) -> dict:
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
        except Exception as e:  # ConnectionError / SSLError / 超时等瞬态错误
            last = e
            time.sleep(2 + 3 * attempt)
    raise last if last else RuntimeError("tavily_failed")


_NAV_LINE = re.compile(r"^\s*\*?\s*[A-Za-zА-Яа-я一-鿿À-ÿā-ſ āăēĕīĭōŏūŭ'’\-]{1,25}\s*$")
_NAV_WORDS = {"edit", "talk", "read", "tools", "actions", "article", "history",
              "links", "print/export", "download", "pdf", "printable", "version",
              "general", "information", "cite", "page", "shortened", "legacy",
              "parser", "switch", "upload", "file", "related", "changes", "permanent",
              "what", "here", "view"}


def _clean_nav(t: str) -> str:
    """去掉维基页面 extract 里的语言切换/导航列表行，防止淹没导语。"""
    kept = []
    for ln in t.splitlines():
        s = ln.strip().lstrip("*").strip()
        if s and (s.lower() in _NAV_WORDS or _NAV_LINE.match(ln)):
            continue
        kept.append(ln)
    return "\n".join(kept)


_CUR_YEAR = 2026  # 卒年等于当前年份多为解析伪影（在世学者的裸范围误配），保守清空


def _match_tiers(window: str, strict_paren: bool = False) -> tuple[str, str]:
    m = re.search(rf"(?:{_MONTH_ALT})\s+\d{{1,2}},?\s+({_Y})\s*[–—-]\s*"
                  rf"(?:(?:{_MONTH_ALT})\s+\d{{1,2}},?\s+)?({_Y})?", window)
    if m:
        b, d = m.group(1), (m.group(2) or "")
        if d and int(d) == _CUR_YEAR:
            d = ""
        if 1400 <= int(b) <= 2026 and (not d or int(b) <= int(d) <= 2026):
            return b, d
    m = re.search(rf"({_Y})\s*年?\s*[–—-]\s*({_Y})\s*年?", window)
    if m:
        b, d = m.group(1), ("" if int(m.group(2)) == _CUR_YEAR else m.group(2))
        ok = 1400 <= int(b) <= 2026 and int(b) <= int(d) <= 2026
        if ok and not strict_paren:
            return b, d
        if ok and strict_paren:
            before, after = window[:m.start()][-60:], window[m.end():][:60]
            if "(" in before.split(")")[-1] and ")" in after.split("(")[0]:
                return b, d
    m = re.search(rf"[Bb]orn\s+(?:(?:{_MONTH_ALT})\s+\d{{1,2}},\s+)?({_Y})", window)
    if m and 1400 <= int(m.group(1)) <= 2026:
        return m.group(1), ""
    return "", ""


def _match_infobox(t: str) -> tuple[str, str]:
    """tier-0：解析 infobox 表格行 | Born | ... / | Died | ...（含中文维基 出生/逝世）。"""
    birth = death = ""
    m = re.search(r"\|\s*(?:Born|出生|生卒|诞辰)\s*\|\s*([^\n|]{0,140})", t)
    if m:
        y = re.search(_Y, m.group(1))
        if y and 1400 <= int(y.group(0)) <= 2026:
            birth = y.group(0)
    m = re.search(r"\|\s*(?:Died|逝世|去世|死亡)\s*\|\s*([^\n|]{0,140})", t)
    if m:
        y = re.search(_Y, m.group(1))
        if y and 1400 <= int(y.group(0)) <= 2026:
            if not birth or int(birth) <= int(y.group(0)):
                death = "" if int(y.group(0)) == _CUR_YEAR else y.group(0)
    return birth, death


def parse_years(text: str, anchor: str | None = None) -> tuple[str, str]:
    """解析生卒年：剥 Markdown + 导航列表后，遍历人名出现的各窗口；
    先扫 tier-1（月份日期），再扫 tier-2（裸范围，须在括号内）。"""
    if not text:
        return "", ""
    t = _clean_nav(_strip_md(text)).replace(" ", " ")
    # tier-0：infobox Born/Died 行（最可靠，生卒分列两行时只有这里能拿到）
    b, d = _match_infobox(t)
    if b:
        return b, d
    if anchor:
        starts = [m.start() for m in re.finditer(re.escape(anchor.lower()), t.lower())][:10] or [0]
    else:
        starts = [0]
    windows = [t[s: s + 700] for s in starts]
    for w in windows:                       # 第一轮：完整日期范围优先
        r = _match_tiers(w, strict_paren=True)
        if r and r[0]:
            return r
    for w in windows:                       # 第二轮：放宽到括号内裸范围
        r = _match_tiers(w, strict_paren=False)
        if r and r[0]:
            return r
    return "", ""


def clean_wiki_url(u: str) -> str:
    u = u.split("?")[0].split("#")[0]
    for junk in ("/wiki/Special:", "/wiki/Talk:", "/wiki/File:", "/w/"):
        if junk in u:
            return ""
    return u


def opensearch_wiki(name: str, lang: str = "en") -> str:
    """维基官方 opensearch：按标题精确匹配，返回规范 URL；无命中返回 ''。"""
    for _ in range(2):  # 本机直连 wikipedia 间歇不稳，轻量重试
        try:
            r = requests.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": name, "limit": 3,
                        "format": "json", "redirects": "resolve"},
                headers={"User-Agent": "news-kg-scholar-enrichment/1.0"},
                timeout=20)
            r.raise_for_status()
            data = r.json()
            titles = data[1] if len(data) > 1 else []
            if not titles:
                return ""
            name_l = name.lower()
            title = next((t for t in titles if name_l in t.lower()), titles[0])
            from urllib.parse import quote
            return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        except Exception:
            time.sleep(2)
    return ""


def enrich_one(row: dict, key: str) -> dict:
    out = {"scholar_id": row["scholar_id"], "wikipedia_url": "", "birth_year": "",
           "death_year": "", "evidence_url": "", "method": "", "enriched_at": ""}
    name = (row.get("name_en") or row.get("name_zh") or "").strip()
    if not name:
        out["method"] = "no_name"
        return out

    # 1) 维基 opensearch 精确定位（en 优先，zh 兜底）
    wiki_url = opensearch_wiki(name, "en")
    how = "opensearch_en"
    if not wiki_url and row.get("name_zh"):
        wiki_url = opensearch_wiki(row["name_zh"].strip(), "zh")
        how = "opensearch_zh"

    # 2) 兜底：Tavily search（严格过滤 wiki 域 + 标题须含姓名）
    if not wiki_url:
        try:
            data = tavily(SEARCH_PATH, {
                "query": f"{name} wikipedia", "include_domains": ["wikipedia.org"],
                "search_depth": "basic", "max_results": 5,
            }, key)
        except Exception as e:
            out["method"] = f"search_failed:{type(e).__name__}"
            return out
        for res in data.get("results", []):
            u = res.get("url", "").split("?")[0].split("#")[0]
            m = re.match(r"https://([a-z]+)\.wikipedia\.org/wiki/([^:]+)$", u)
            if not m or m.group(1) not in WIKI_LANG_PRIORITY:
                continue
            title_seg = m.group(2).replace("_", " ")
            if name.lower() in title_seg.lower():
                wiki_url = u
                how = "tavily_search"
                break
    if not wiki_url:
        out["method"] = "no_wiki_hit"
        return out
    out["wikipedia_url"] = wiki_url
    out["evidence_url"] = wiki_url

    # 3) 生卒年：主力 Tavily extract（服务端抓取，不受本机直连 wikipedia 不稳影响），
    #    结果按 URL 缓存到 data/enrich_cache/；REST summary 仅作补充
    birth = death = ""
    anchors = [a for a in (name, row.get("name_zh")) if a]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_f = CACHE_DIR / (hashlib.md5(wiki_url.encode()).hexdigest() + ".txt")
    raw = ""
    if cache_f.exists():
        try:
            raw = cache_f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw = ""
    if not raw:
        try:
            ex = tavily(EXTRACT_PATH, {"urls": [wiki_url], "extract_depth": "basic"}, key, timeout=90)
            raw = (ex.get("results") or [{}])[0].get("raw_content", "") or ""
            if raw:
                cache_f.write_text(raw, encoding="utf-8")
        except Exception:
            pass
    if raw:
        for a in anchors:
            birth, death = parse_years(raw, anchor=a)
            if birth:
                break
        if birth:
            how += "+tavily_extract"
    if not birth:
        try:
            r = requests.get(
                wiki_url.replace("/wiki/", "/api/rest_v1/page/summary/"),
                headers={"User-Agent": "news-kg-scholar-enrichment/1.0"}, timeout=20)
            if r.status_code == 200:
                ext = r.json().get("extract", "") or ""
                for a in anchors:
                    birth, death = parse_years(ext[:800], anchor=a)
                    if birth:
                        break
                if birth:
                    how += "+rest_summary"
        except Exception:
            pass
    if birth:
        out["birth_year"], out["death_year"] = birth, death
    else:
        how = how.split("+")[0] + "+no_years"
    out["method"] = how
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "csv" / "scholars.csv"))
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        sys.exit("缺少 TAVILY_API_KEY")

    global CACHE_DIR
    csv_path = Path(args.csv)
    out_path = Path(args.out) if args.out else csv_path.parent / "scholars_external.csv"
    CACHE_DIR = csv_path.parent.parent / "enrich_cache"   # 缓存跟随数据所在项目

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in _csv.DictReader(f) if (r.get("name_en") or r.get("name_zh"))]
    done_ids = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8-sig", newline="") as f:
            # 临时失败 (method 以 error: 开头) 不计入完成，下次重试
            done_ids = {
                r["scholar_id"]
                for r in _csv.DictReader(f)
                if r.get("scholar_id") and not (r.get("method") or "").startswith("error:")
            }
    todo = [r for r in rows if r["scholar_id"] not in done_ids]
    if args.limit:
        todo = todo[: args.limit]
    print(f"[scholars] {csv_path.parent.parent.name}: 共 {len(rows)} 位，待补 {len(todo)}（缓存跳过 {len(done_ids)}）", flush=True)

    t0 = time.time()
    results = []
    counters = {}

    def run(row):
        try:
            res = enrich_one(row, key)
        except Exception as e:
            res = {"scholar_id": row["scholar_id"], "wikipedia_url": "", "birth_year": "",
                   "death_year": "", "evidence_url": "", "method": f"error:{type(e).__name__}", "enriched_at": ""}
        res["enriched_at"] = time.strftime("%Y-%m-%d")
        return res

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run, r) for r in todo]
        for n, fut in enumerate(cf.as_completed(futs), 1):
            res = fut.result()
            results.append(res)
            counters[res["method"].split(":")[0]] = counters.get(res["method"].split(":")[0], 0) + 1
            if n % 20 == 0:
                print(f"[scholars] {n}/{len(todo)} | {time.time()-t0:.0f}s | {counters}", flush=True)

    # 写回 sidecar（合并旧数据）
    all_rows = []
    if out_path.exists():
        with open(out_path, encoding="utf-8-sig", newline="") as f:
            old = {r["scholar_id"]: r for r in _csv.DictReader(f)}
        all_rows = list(old.values())
    by_id = {r["scholar_id"]: r for r in all_rows}
    for r in results:
        by_id[r["scholar_id"]] = r
    fieldnames = ["scholar_id", "wikipedia_url", "birth_year", "death_year", "evidence_url", "method", "enriched_at"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for sid in sorted(by_id):
            w.writerow({k: by_id[sid].get(k, "") for k in fieldnames})

    n = len(by_id)
    has_url = sum(1 for r in by_id.values() if r.get("wikipedia_url"))
    has_years = sum(1 for r in by_id.values() if r.get("birth_year"))
    print(f"[done] {out_path.name}: wiki链接 {has_url}/{n} | 生年 {has_years}/{n} | 方法分布 {counters} | {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
