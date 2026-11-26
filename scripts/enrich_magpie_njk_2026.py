# -*- coding: utf-8 -*-
"""Live Magpie MCP multi-channel enrichment for news-journalism-kg (2026).

Tracks:
  1. Books (`data/csv/books_external.csv`):
     Enrich all `no_wiki_hit` books plus any row with blank `wikipedia_url`,
     `evidence_url`, or `found_year`.
  2. Scholars (`data/csv/scholars_external.csv` & `data/csv/scholars.csv`):
     - Enrich all scholars missing `wikipedia_url`, `birth_year`, or `evidence_url`.
     - Verify and fill `death_year` ONLY for genuinely deceased, verified scholars.
     - Fill missing `name_en` in `scholars.csv` when unambiguously verified.

Hard invariants:
  - ZERO OVERWRITE of any existing non-empty substantive field.
  - Strict local hostname validation (`urlparse(url).netloc.lower()`).
  - Strict person-name + domain/field matching and life-span sanity guards:
      death_year > birth_year, death_year - birth_year <= 115,
      birth_year <= active_year - 12 (when active_year exists).
  - Exact preservation of CSV column order, row order, UTF-8/BOM, and line endings.
  - Zero Chinese 11-digit mobile numbers; 100% valid http(s) URLs.
  - Full provenance logs written to:
      data/repair/magpie_njk_enrichment_audit_2026.json
      data/repair/magpie_njk_enrichment_log_2026.csv

Usage:
  python scripts/enrich_magpie_njk_2026.py              # dry-run (writes audit logs only)
  python scripts/enrich_magpie_njk_2026.py --write      # write CSVs + audit logs
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from magpie_search_client import (  # noqa: E402
    MagpieWebSearchClient,
    SearchHit,
    contains_cn_mobile,
    extract_hostname,
    extract_netloc,
    host_matches_any,
    is_canonical_wikipedia_url,
    is_valid_http_url,
    mask_cn_mobile,
    normalize_canonical_wikipedia_url,
)
from wiki_match import (  # noqa: E402
    en_name_in_labels,
    norm_latin,
    reject_person_title,
    titles_compatible,
    tokens,
    year_status as compute_year_status,
    zh_name_in_labels,
    zh_title_exact,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ENRICHED_AT = "2026-11-26"
CURRENT_YEAR = 2026
CSV_DIR = ROOT / "data" / "csv"
REPAIR_DIR = ROOT / "data" / "repair"
CACHE_DIR = ROOT / "data" / "enrich_cache" / "magpie_njk_2026"

# Extended Traditional -> Simplified map for matching zh.wikipedia.org titles/text.
_EXT_T2S = str.maketrans({
    "黃": "黄", "鵬": "鹏", "斂": "敛", "張": "张", "強": "强", "國": "国",
    "學": "学", "體": "体", "會": "会", "來": "来", "對": "对", "開": "开",
    "門": "门", "書": "书", "長": "长", "東": "东", "車": "车", "見": "见",
    "貝": "贝", "馬": "马", "鳥": "鸟", "魚": "鱼", "龍": "龙", "雲": "云",
    "電": "电", "點": "点", "經": "经", "線": "线", "網": "网", "報": "报",
    "說": "说", "話": "话", "語": "语", "認": "认", "識": "识", "議": "议",
    "這": "这", "還": "还", "過": "过", "進": "进", "選": "选", "運": "运",
    "爾": "尔", "為": "为", "時": "时", "與": "与", "發": "发", "後": "后",
    "從": "从", "種": "种", "實": "实", "現": "现", "關": "关", "問": "问",
    "間": "间", "無": "无", "產": "产", "業": "业", "廣": "广", "傳": "传",
    "統": "统", "繼": "继", "續": "续", "應": "应", "該": "该", "個": "个",
    "們": "们", "樣": "样", "機": "机", "構": "构", "歷": "历", "圖": "图",
    "華": "华", "萬": "万", "靈": "灵", "齊": "齐", "盧": "卢",
    "論": "论", "韜": "韬", "奮": "奋", "聞": "闻", "絡": "络", "講": "讲",
    "慶": "庆", "榮": "荣", "蘭": "兰", "濤": "涛", "擇": "择", "響": "响",
    "濾": "滤", "氣": "气", "眾": "众", "數": "数", "資": "资", "義": "义",
    "類": "类", "導": "导", "視": "视", "編": "编", "碼": "码", "聯": "联",
    "維": "维", "漢": "汉", "陳": "陈", "鄧": "邓", "範": "范", "鄒": "邹",
    "鄭": "郑", "觀": "观", "際": "际", "濟": "济", "權": "权", "專": "专",
    "雜": "杂", "驗": "验", "組": "组", "織": "织", "舊": "旧", "衝": "冲",
    "處": "处", "歲": "岁", "隱": "隐", "沒": "没", "訴": "诉", "亞": "亚",
    "歐": "欧", "羅": "罗", "諾": "诺", "費": "费", "麥": "麦", "澤": "泽",
    "衛": "卫", "傑": "杰", "邁": "迈", "愛": "爱", "約": "约", "湯": "汤",
    "裡": "里", "裏": "里", "係": "系", "於": "于", "檯": "台", "臺": "台",
    "黨": "党", "戰": "战", "爭": "争", "區": "区", "域": "域", "場": "场",
    "獨": "独", "錄": "录", "譯": "译", "評": "评", "讀": "读", "寫": "写",
    "術": "术", "藝": "艺", "館": "馆", "園": "园", "總": "总", "號": "号",
    "筆": "笔", "記": "记", "紀": "纪", "訊": "讯", "採": "采", "訪": "访",
    "設": "设", "計": "计", "稱": "称", "標": "标", "準": "准", "則": "则",
    "創": "创", "辦": "办", "執": "执", "監": "监", "師": "师", "授": "授",
    "獎": "奖", "賞": "赏", "譽": "誉", "級": "级", "極": "极", "條": "条",
    "雙": "双", "層": "层", "階": "阶", "變": "变", "遷": "迁", "轉": "转",
    "換": "换", "調": "调", "查": "查", "歸": "归", "納": "纳", "釋": "释",
    "辭": "辞", "詞": "词", "典": "典", "冊": "册", "卷": "卷", "頁": "页",
    "印": "印", "製": "制", "造": "造", "銷": "销", "買": "买", "賣": "卖",
    "貨": "货", "結": "结", "餘": "余",
})


def to_simplified(text: str) -> str:
    return (text or "").translate(_EXT_T2S)


# Strictly validated authoritative domains (checked locally via urlparse().netloc.lower()).
BLOCKED_DOMAINS = (
    "pinterest.com",
    "reddit.com",
    "quora.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
    "bilibili.com",
    "zhihu.com",
    "tieba.baidu.com",
    "baijiahao.baidu.com",
    "sohu.com",
    "163.com",
    "sina.com.cn",
    "sina.com",
    "csdn.net",
    "jianshu.com",
    "scribd.com",
    "coursehero.com",
    "studocu.com",
    "chegg.com",
    "amazon.com",
    "amazon.co.uk",
    "amazon.cn",
    "ebay.com",
    "taobao.com",
    "tmall.com",
    "jd.com",
    "dangdang.com",
    "kongfz.com",
    "goodreads.com",
    "abebooks.com",
    "thriftbooks.com",
    "barnesandnoble.com",
    "alibris.com",
    "betterworldbooks.com",
    "bookfinder.com",
    "walmart.com",
    "target.com",
    "imdb.com",
    "rottentomatoes.com",
    "fandom.com",
    "everipedia.org",
    "grokipedia.com",
    "wikibin.org",
    "alchetron.com",
    "prabook.com",
    "famousbirthdays.com",
    "celebsagewiki.com",
    "findagrave.com",
    "linkedin.com",
    "tavily.com",
    "rocketreach.co",
    "leadiq.com",
    "yelp.com",
    "slideshare.net",
    "weread.qq.com",
    "book345.com",
    "scirp.org",
    "academia.edu",
    "toutiao.com",
    "ifeng.com",
    "yahoo.com",
)

# Tiered authoritative NON-WIKIPEDIA book evidence domains (Wikipedia is handled separately).
BOOK_DOMAIN_TIERS: List[Tuple[int, Tuple[str, ...]]] = [
    (1, (
        "britannica.com",
        "zgbk.com",
        "gutenberg.org",
        "plato.stanford.edu",
        "oxfordreference.com",
        "wikisource.org",
        "ctext.org",
    )),
    (2, (
        "mitpress.mit.edu",
        "nyupress.org",
        "press.uchicago.edu",
        "hup.harvard.edu",
        "press.princeton.edu",
        "yalebooks.yale.edu",
        "cup.columbia.edu",
        "utppublishing.com",
        "utorontopress.com",
        "oup.com",
        "cambridge.org",
        "routledge.com",
        "taylorfrancis.com",
        "tandfonline.com",
        "sagepub.com",
        "socialsciencespace.com",
        "wiley.com",
        "springer.com",
        "bloomsbury.com",
        "politybooks.com",
        "dukeupress.edu",
        "sup.org",
        "ucpress.edu",
        "press.jhu.edu",
        "press.umich.edu",
        "press.illinois.edu",
        "intellectbooks.com",
        "simonandschuster.com",
        "penguinrandomhouse.com",
        "harpercollins.com",
        "macmillan.com",
        "versobooks.com",
        "brill.com",
        "degruyter.com",
        "crup.com.cn",
        "fudanpress.com",
        "cp.com.cn",
        "pup.cn",
        "tsinghuapress.com.cn",
        "xinhuapress.com",
        "phei.com.cn",
        "hep.com.cn",
    )),
    (3, (
        "worldcat.org",
        "loc.gov",
        "hathitrust.org",
        "openlibrary.org",
        "archive.org",
        "jstor.org",
        "muse.jhu.edu",
        "apa.org",
        "doi.org",
        "semanticscholar.org",
        "eric.ed.gov",
        "books.google.com",
        "scholar.google.com",
        "nlc.cn",
        "las.ac.cn",
        "cnki.net",
        "wanfangdata.com.cn",
        "monoskop.org",
        "fadedpage.com",
        "chsi.com.cn",
    )),
    (4, (
        "xinhuanet.com",
        "news.cn",
        "gmw.cn",
        "people.com.cn",
        "chinawriter.com.cn",
        "ccphistory.org.cn",
        "zgjx.cn",
        "book.douban.com",
        "baike.baidu.com",
    )),
]

# Tiered authoritative NON-WIKIPEDIA scholar evidence domains.
SCHOLAR_DOMAIN_TIERS: List[Tuple[int, Tuple[str, ...]]] = [
    (1, (
        "britannica.com",
        "zgbk.com",
        "plato.stanford.edu",
        "oxfordreference.com",
        "ae-info.org",
        "prsa.org",
        "prmuseum.org",
        "instituteforpr.org",
        "media-ecology.org",
        "media-ecology.net",
        "icahdq.org",
        "aejmc.org",
        "asanet.org",
        "britac.ac.uk",
    )),
    (2, (
        "ruc.edu.cn",
        "fudan.edu.cn",
        "tsinghua.edu.cn",
        "pku.edu.cn",
        "cuc.edu.cn",
        "nju.edu.cn",
        "whu.edu.cn",
        "zju.edu.cn",
        "lboro.ac.uk",
        "lborolondon.ac.uk",
        "gold.ac.uk",
        "unc.edu",
        "umn.edu",
        "nyu.edu",
        "illinois.edu",
        "bu.edu",
        "uiowa.edu",
        "usc.edu",
        "upenn.edu",
        "columbia.edu",
        "harvard.edu",
        "princeton.edu",
        "yale.edu",
        "stanford.edu",
        "uchicago.edu",
        "cam.ac.uk",
        "ox.ac.uk",
        "lse.ac.uk",
        "utoronto.ca",
        "carleton.ca",
        "sfu.ca",
        "uoc.edu",
        "intellectbooks.com",
        "mitpress.mit.edu",
        "nyupress.org",
        "press.uchicago.edu",
        "sagepub.com",
        "routledge.com",
        "oup.com",
        "cambridge.org",
        "monoskop.org",
    )),
    (3, (
        "nytimes.com",
        "washingtonpost.com",
        "theguardian.com",
        "bbc.com",
        "bbc.co.uk",
        "reuters.com",
        "apnews.com",
        "npr.org",
        "startribune.com",
        "koopfuneralhome.com",
        "people.com.cn",
        "xinhuanet.com",
        "news.cn",
        "gmw.cn",
        "thepaper.cn",
        "chinanews.com.cn",
        "cssn.cn",
        "nopss.gov.cn",
        "jstor.org",
        "worldcat.org",
        "loc.gov",
        "archive.org",
        "openlibrary.org",
    )),
    (4, (
        "baike.baidu.com",
        "book.douban.com",
    )),
]

ACADEMIC_GOV_SUFFIXES = (
    ".edu", ".edu.cn", ".ac.uk", ".ac.cn", ".ac.id", ".gov", ".gov.cn"
)


def book_evidence_tier(url: str) -> int:
    """Return authority tier (1 best .. 4), or 0 if disallowed/Wikipedia."""
    if not is_valid_http_url(url):
        return 0
    if host_matches_any(url, BLOCKED_DOMAINS):
        return 0
    # Wikipedia MUST go exclusively through the strict wiki_candidates path!
    if host_matches_any(url, ("wikipedia.org",)):
        return 0
    for tier, domains in BOOK_DOMAIN_TIERS:
        if host_matches_any(url, domains):
            return tier
    host = extract_hostname(url)
    if any(host.endswith(suf) for suf in ACADEMIC_GOV_SUFFIXES):
        return 2
    return 0


def scholar_evidence_tier(url: str) -> int:
    """Return authority tier (1 best .. 4), or 0 if disallowed/Wikipedia."""
    if not is_valid_http_url(url):
        return 0
    if host_matches_any(url, BLOCKED_DOMAINS):
        return 0
    if host_matches_any(url, ("wikipedia.org",)):
        return 0
    for tier, domains in SCHOLAR_DOMAIN_TIERS:
        if host_matches_any(url, domains):
            return tier
    host = extract_hostname(url)
    if any(host.endswith(suf) for suf in ACADEMIC_GOV_SUFFIXES):
        return 2
    return 0


# ---------------------------------------------------------------------------
# Caching wrapper around MagpieWebSearchClient (gitignored under data/enrich_cache)
# ---------------------------------------------------------------------------
class CachedMagpie:
    def __init__(self, client: MagpieWebSearchClient, use_cache: bool = True) -> None:
        self.client = client
        self.use_cache = use_cache
        self.cache_dir = CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.queries_log: List[Dict[str, Any]] = []
        self.fetches_log: List[Dict[str, Any]] = []

    def _cache_path(self, prefix: str, key_obj: Any) -> Path:
        h = hashlib.sha256(
            json.dumps(key_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        return self.cache_dir / f"{prefix}_{h}.json"

    def search(
        self,
        query: str,
        *,
        count: int = 8,
        allowed_domains: Optional[Sequence[str]] = None,
        enforce_local_domain_filter: bool = True,
    ) -> List[SearchHit]:
        key = {
            "q": query,
            "count": count,
            "allowed": list(allowed_domains) if allowed_domains else [],
            "local_filter": enforce_local_domain_filter,
        }
        cp = self._cache_path("search", key)
        if self.use_cache and cp.is_file():
            try:
                raw = json.loads(cp.read_text(encoding="utf-8"))
                hits = [SearchHit(**item) for item in raw.get("hits", [])]
                self.queries_log.append({
                    "query": query,
                    "allowed_domains": key["allowed"],
                    "cached": True,
                    "hit_count": len(hits),
                })
                return hits
            except Exception:
                pass
        try:
            hits = self.client.search(
                query,
                count=count,
                allowed_domains=allowed_domains,
                enforce_local_domain_filter=enforce_local_domain_filter,
            )
            if self.use_cache:
                cp.write_text(
                    json.dumps(
                        {"query": query, "hits": [h.to_dict() for h in hits]},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            self.queries_log.append({
                "query": query,
                "allowed_domains": key["allowed"],
                "cached": False,
                "hit_count": len(hits),
            })
            return hits
        except Exception as exc:
            self.queries_log.append({
                "query": query,
                "allowed_domains": key["allowed"],
                "cached": False,
                "error": str(exc)[:240],
                "hit_count": 0,
            })
            return []

    def fetch(self, url: str, *, max_chars: int = 24000, offset: int = 0) -> Dict[str, Any]:
        key = {"url": url, "max_chars": max_chars, "offset": offset}
        cp = self._cache_path("fetch", key)
        if self.use_cache and cp.is_file():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                self.fetches_log.append({
                    "url": url,
                    "offset": offset,
                    "cached": True,
                    "ok": bool(data.get("ok")),
                    "chars": len(data.get("text", "")),
                })
                return data
            except Exception:
                pass
        try:
            data = self.client.fetch(url, max_chars=max_chars, offset=offset)
            if self.use_cache and data.get("ok") and data.get("text"):
                cp.write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
            self.fetches_log.append({
                "url": url,
                "offset": offset,
                "cached": False,
                "ok": bool(data.get("ok")),
                "chars": len(data.get("text", "")),
            })
            return data
        except Exception as exc:
            res = {"ok": False, "url": url, "text": "", "error": str(exc)[:240]}
            self.fetches_log.append({
                "url": url,
                "offset": offset,
                "cached": False,
                "ok": False,
                "chars": 0,
                "error": str(exc)[:240],
            })
            return res

    def fetch_wikipedia_body(self, url: str) -> Tuple[bool, str]:
        """Fetch a Wikipedia page and return (ok, article_body_markdown)."""
        res = self.fetch(url, max_chars=28000, offset=0)
        if not res.get("ok") or not res.get("text"):
            return False, ""
        text = res["text"]
        body = slice_wikipedia_body(text)
        if body is None:
            res2 = self.fetch(url, max_chars=28000, offset=22000)
            if res2.get("ok") and res2.get("text"):
                combined = text + "\n" + res2["text"]
                body = slice_wikipedia_body(combined)
                if body is None:
                    res3 = self.fetch(url, max_chars=28000, offset=44000)
                    if res3.get("ok") and res3.get("text"):
                        body = slice_wikipedia_body(combined + "\n" + res3["text"])
        if body is None:
            return True, text[-8000:]
        return True, body


def slice_wikipedia_body(raw: str) -> Optional[str]:
    idx = raw.find("From Wikipedia, the free encyclopedia")
    if idx >= 0:
        return raw[idx:]
    body_search_start = min(len(raw), 400)
    for marker in (
        "维基百科，自由的百科全书",
        "維基百科，自由的百科全書",
        "查论编",
        "查看历史",
        "檢視歷史",
    ):
        idx = raw.find(marker, body_search_start)
        if idx >= 0:
            return raw[idx:]
    return None


def strip_site_nav(raw: str) -> str:
    """Drop website navigation bullet menus while preserving article prose & headings."""
    out: List[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s:
            out.append("")
            continue
        # Drop pure nav link bullets like `- [About](https://...)`
        if re.match(r"^(?:[-*]|\d+\.)\s+\[[^\]]{1,80}\]\([^)]+\)\s*$", s):
            continue
        # Drop pure nav multi-link bars (`[Home](...) [About](...) [Contact](...)`)
        if s.startswith("[") and len(re.findall(r"\[[^\]]+\]\([^)]+\)", s)) >= 3:
            plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
            if len(plain) < 120:
                continue
        out.append(ln)
    return "\n".join(out)


def strip_markdown(text: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text or "")
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    # Strip markdown emphasis markers (`_title_`, `*title*`, `**bold**`, `` `code` ``)
    t = re.sub(r"(?<!\w)[_*`]+|[_*`]+(?!\w)", "", t)
    t = t.replace("\xa0", " ").replace("\u200b", "")
    return t


def wiki_title_from_url(url: str) -> str:
    if "/wiki/" not in (url or ""):
        return ""
    path = urllib.parse.urlparse(url).path
    return urllib.parse.unquote(path.split("/wiki/", 1)[-1]).replace("_", " ")


def is_missing_or_disambig_wiki(body: str) -> bool:
    head = (body or "")[:2500]
    head_l = head.lower()
    return (
        "wikipedia does not have an article with this exact name" in head_l
        or "维基百科目前还没有与上述标题相同的条目" in head
        or "維基百科目前還沒有與上述標題相同的條目" in head
        or "may refer to:" in head_l
        or "can refer to:" in head_l
        or "可以指：" in head
        or "消歧义" in head[:600]
        or "消歧義" in head[:600]
    )


# ---------------------------------------------------------------------------
# Text matching helpers (books & scholars)
# ---------------------------------------------------------------------------
def zh_book_core(title_zh: str) -> str:
    t = to_simplified((title_zh or "").strip())
    t = re.sub(r"（[^）]*）|\([^)]*\)", "", t)
    t = t.split("：")[0].split(":")[0]
    t = re.sub(r"[《》\s·/]", "", t)
    return t.strip()


def zh_title_compatible(title_zh: str, candidate: str) -> bool:
    if not title_zh or not candidate:
        return False
    s_title = to_simplified(title_zh)
    s_cand = to_simplified(candidate)
    if zh_title_exact(s_title, s_cand):
        return True
    core = zh_book_core(s_title)
    cand_core = zh_book_core(s_cand)
    return bool(core and cand_core and core == cand_core)


def en_book_title_matches(title_en: str, candidate: str) -> bool:
    if not title_en or not candidate:
        return False
    if titles_compatible(title_en, candidate):
        return True
    cand_main = re.sub(r"\([^)]*\)", " ", candidate).split(":")[0]
    if titles_compatible(title_en, cand_main):
        return True
    q_toks = tokens(title_en.split(":")[0], drop_stop=True)
    c_toks = tokens(cand_main, drop_stop=True)
    return bool(q_toks and c_toks and q_toks == c_toks)


def author_mentioned(scholar: Dict[str, str], text: str) -> bool:
    if not text:
        return False
    name_zh = to_simplified(scholar.get("name_zh", "").strip())
    name_en = (scholar.get("name_en", "") or "").strip()
    s_text = to_simplified(text)
    if name_zh:
        if name_zh in s_text:
            return True
        if "·" in name_zh:
            last_zh = name_zh.split("·")[-1]
            if len(last_zh) >= 2 and last_zh in s_text:
                return True
    if name_en:
        folded = norm_latin(text)
        name_toks = tokens(name_en)
        if name_toks:
            surname = name_toks[-1]
            if len(surname) >= 3 and re.search(rf"\b{re.escape(surname)}\b", folded):
                return True
    return False


def looks_like_person_bio_wiki(body: str) -> bool:
    head = strip_markdown(body[:3000])
    if re.search(r"(?m)^(?:Born|Died|出生|逝世)\s*\|", head):
        return True
    first_para = head[:1200]
    if re.search(
        r"\b(?:was|is)\s+(?:an?\s+)?(?:American|British|Canadian|German|French|Chinese|Australian|Spanish|Italian|Austrian|Polish)\s+"
        r"(?:sociologist|philosopher|journalist|writer|author|professor|theorist|historian|scholar|economist|psychologist|activist)",
        first_para,
        re.I,
    ):
        return True
    return False


def looks_like_company_wiki(body: str) -> bool:
    head = strip_markdown(body[:2000])
    if re.search(r"(?m)^(?:Headquarters|Founders?|Number of employees|Parent|Revenue|Industry)\s*\|", head):
        return True
    if re.search(r"\b(?:public relations firm|multinational|corporation|company)\b", head[:400], re.I):
        return True
    return False


def looks_like_work_page(body: str) -> bool:
    head = strip_markdown(body[:4000]).lower()
    en_markers = (
        "book", "monograph", "published", "publication date", "author",
        "isbn", "essay", "treatise", "paper", "volume", "textbook", "publisher",
        "edition", "pages", "press", "theory", "model",
    )
    zh_markers = (
        "著作", "书籍", "专著", "教材", "文集", "杂文集", "通讯集", "出版",
        "出版社", "作者", "初版", "首版", "书号", "isbn", "全书", "主编",
    )
    s_head = to_simplified(head)
    return any(w in head for w in en_markers) or any(w in s_head for w in zh_markers)


# ---------------------------------------------------------------------------
# Book publication year extraction
# ---------------------------------------------------------------------------
_YEAR_RE = r"(?<!\d)(?:1[5-9]\d{2}|20[0-2]\d)(?!\d)"
_MONTH_EN = (
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan\.?|Feb\.?|Mar\.?|"
    r"Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)"
)

# Only strip modern site footer/boilerplate copyright lines (2020-2026), NOT book frontmatter copyright (e.g. © 1963)!
_SITE_FOOTER_RE = re.compile(
    r"(?:(?:©|\bcopyright\b)\s*202[0-6]|\ball rights reserved\b|\blast edited\b|"
    r"\bretrieved\b|\baccessed\b|\bterms of use\b|\bprivacy policy\b|\bcookie\b|icp备|版权所有)",
    re.IGNORECASE,
)


def clean_for_book_year(raw: str) -> str:
    t = strip_markdown(strip_site_nav(raw))
    lines = []
    for ln in t.splitlines():
        if _SITE_FOOTER_RE.search(ln):
            continue
        lines.append(ln)
    return "\n".join(lines)


def _valid_book_year(y_str: str) -> bool:
    if not y_str or not y_str.isdigit():
        return False
    y = int(y_str)
    return 1450 <= y <= 2025


def _title_anchored_windows(text: str, title_en: str, title_zh: str, *, before_chars: int = 100) -> List[str]:
    """Return windows around occurrences of the book title in text."""
    s_text = to_simplified(text)
    probes = []
    en_short = (title_en or "").split(":")[0].strip()
    zh_core = zh_book_core(title_zh)
    if len(zh_core) >= 2:
        probes.append(f"《{zh_core}》")
        if len(zh_core) >= 4:
            probes.append(zh_core)
    if len(en_short) >= 4:
        probes.append(en_short)
    windows: List[str] = []
    for p in probes:
        for m in re.finditer(re.escape(p), s_text, re.IGNORECASE):
            seg = s_text[max(0, m.start() - before_chars) : min(len(s_text), m.end() + 220)]
            if before_chars <= 30:
                # On multi-book lists/tables, truncate right before the next row/book entry
                after_title_pos = (m.end() - max(0, m.start() - before_chars))
                tail = seg[after_title_pos:]
                cut = re.search(r"(?:\|\s*\d+\s*\||(?<=。)[^。]{0,4}《)", tail)
                if cut:
                    seg = seg[: after_title_pos + cut.start()]
            windows.append(seg)
            if len(windows) >= 8:
                break
        if windows:
            break
    return windows


def extract_book_pub_year(
    raw: str,
    title_en: str = "",
    title_zh: str = "",
    is_wikipedia: bool = False,
    page_is_dedicated_to_book: bool = True,
) -> Tuple[str, str]:
    """Extract (found_year, evidence_excerpt) from fetched page text or snippet."""
    cleaned = clean_for_book_year(raw)
    head = cleaned[:16000]
    s_head = to_simplified(head)

    # On non-dedicated pages (e.g. author bio / multi-book list), keep tight `before_chars=25`
    # so we never accidentally capture the publication year of a DIFFERENT book listed just before!
    title_windows = _title_anchored_windows(
        s_head,
        title_en,
        title_zh,
        before_chars=80 if page_is_dedicated_to_book else 25,
    )
    search_scopes = title_windows if (not page_is_dedicated_to_book and title_windows) else [s_head]

    # 1. Explicit "first/originally published" / "Originally YYYY" / "[1902/22]" / "初版"
    first_pub_patterns = [
        rf"(?:first|originally|initially)\s+published(?:\s+(?:in|by\b[^.\n]{{0,70}}?\bin))?\s+(?:{_MONTH_EN}\s+\d{{1,2}},?\s+)?({_YEAR_RE})\b",
        rf"first\s+edition\s+published\s+(?:in\s+)?({_YEAR_RE})\b",
        rf"\[\s*(?:originally\s+)?({_YEAR_RE})(?:/\d{{2,4}})?\s*[\];]",
        rf"(?:first|original)\s+(?:edition|publication|published)[^.\n]{{0,45}}?({_YEAR_RE})\b",
        rf"(?:于|於)\s*({_YEAR_RE})\s*年[^。\n]{{0,18}}?(?:完成|出版|写成)[^。\n]{{0,14}}?第一本",
        rf"({_YEAR_RE})\s*年(?:\d{{1,2}}月)?\s*(?:初版|首版|首次出版)",
        rf"(?:初版|首版|首次出版|原版)(?:于|於|时间|年份|日期)?\s*[:：]?\s*({_YEAR_RE})\s*年?",
    ]
    for scope in (title_windows + [s_head]) if page_is_dedicated_to_book else search_scopes:
        for pat in first_pub_patterns:
            m = re.search(pat, scope, re.IGNORECASE)
            if m and _valid_book_year(m.group(1)):
                excerpt = scope[max(0, m.start() - 35) : min(len(scope), m.end() + 45)]
                return m.group(1), re.sub(r"\s+", " ", excerpt).strip()

    # 2. Title-anchored parenthetical or explicit publication sentence (beats later-edition infoboxes!)
    en_short = (title_en or "").split(":")[0].strip()
    zh_core = zh_book_core(title_zh)
    for w in title_windows:
        for pat in (
            rf"《{re.escape(zh_core)}》[^。\n]{{0,40}}?[（(]\s*({_YEAR_RE})\s*年" if zh_core else r"(?!x)x",
            rf"《{re.escape(zh_core)}》[^。\n]{{0,45}}?(?:于|於)\s*({_YEAR_RE})\s*年" if zh_core else r"(?!x)x",
            rf"《{re.escape(zh_core)}》[^。\n]{{0,55}}?({_YEAR_RE})\s*年(?:\d{{1,2}}月)?(?:结集|出版|由[^。\n]{{0,24}}?出版|发行|完成)" if zh_core else r"(?!x)x",
            rf"《{re.escape(zh_core)}》[^|\n]{{0,50}}\|\s*({_YEAR_RE})\s*年?" if zh_core else r"(?!x)x",
            rf"{re.escape(en_short)}[^.\n]{{0,45}}?\(\s*(?:[^()]{{0,35}},\s*)?({_YEAR_RE})\s*\)" if len(en_short) >= 4 else r"(?!x)x",
            rf"\(\s*({_YEAR_RE})\s*\)\s*{re.escape(en_short)}" if len(en_short) >= 4 else r"(?!x)x",
            rf"{re.escape(en_short)}[^.\n]{{0,65}}?(?:published|in)\s+({_YEAR_RE})\b" if len(en_short) >= 4 else r"(?!x)x",
            rf"\b({_YEAR_RE})\s+work,?\s+{re.escape(en_short)}" if len(en_short) >= 4 else r"(?!x)x",
        ):
            m = re.search(pat, w, re.IGNORECASE)
            if m and _valid_book_year(m.group(1)):
                excerpt = w[max(0, m.start() - 25) : min(len(w), m.end() + 45)]
                return m.group(1), re.sub(r"\s+", " ", excerpt).strip()

    if not page_is_dedicated_to_book:
        # On non-dedicated pages (e.g. author bio or news article), do NOT fall back to arbitrary years on the page!
        for w in title_windows:
            m = re.search(
                rf"(?:published|出版|发行|结集|完成)[^。\n.]{{0,35}}?({_YEAR_RE})\b",
                w,
                re.IGNORECASE,
            )
            if m and _valid_book_year(m.group(1)):
                excerpt = w[max(0, m.start() - 25) : min(len(w), m.end() + 45)]
                return m.group(1), re.sub(r"\s+", " ", excerpt).strip()
        return "", ""

    # 3. Dedicated book page: Infobox / metadata key-value lines (supports `:`, `|`, OR newline like Archive.org!)
    infobox_labels = [
        r"Publication\s+date",
        r"First\s+published",
        r"Pub(?:lication)?\s+date",
        r"Published",
        r"Sales\s+Date",
        r"Release\s+date",
        r"出版时间",
        r"出版日期",
        r"出版年",
        r"发行时间",
        r"成书年代",
        r"成书时间",
    ]
    for lbl in infobox_labels:
        m = re.search(
            rf"(?m)(?:^|\||[-*]\s*){lbl}\s*(?:\||[:：]|\n\s*)\s*([^\n]{{0,120}})",
            s_head[:12000],
            re.IGNORECASE,
        )
        if m:
            val = m.group(1)
            if not re.search(_YEAR_RE, val):
                val = s_head[m.start() : m.end() + 140]
            yrs = [y for y in re.findall(_YEAR_RE, val) if _valid_book_year(y)]
            if yrs:
                y = str(min(int(x) for x in yrs))
                excerpt = re.sub(r"\s+", " ", val[:140]).strip()
                return y, excerpt

    # 4. Dedicated book page: Lead prose & historical frontmatter copyright (`© 1963 by ...`)
    lead = s_head[:6000]
    lead_patterns = [
        rf"\bis\s+an?\s+({_YEAR_RE})\s+(?:non-?fiction\s+|seminal\s+|classic\s+|influential\s+|academic\s+|sociological\s+|political\s+|communication\s+|media\s+|groundbreaking\s+)?(?:book|monograph|work|study|essay|paper|treatise|textbook|collection|volume|reportage|trilogy)\b",
        rf"\b(?:published|released|appeared)\s+(?:in|by\b[^.\n]{{0,70}}?\bin)\s+(?:{_MONTH_EN}\s+\d{{1,2}},?\s+)?({_YEAR_RE})\b",
        rf"\b(?:published|printed)\s+(?:in\s+)?(?:{_MONTH_EN}\s+\d{{1,2}},?\s+)?({_YEAR_RE})\b",
        rf"©\s*(1[6-9]\d{{2}}|20[01]\d)\s+by\b",
        rf"(?:出版于|发表于|问世于|刊行于|发行于|成书于|出版时间[为是]?)\s*({_YEAR_RE})\s*年",
        rf"({_YEAR_RE})\s*年(?:\d{{1,2}}月)?(?:[^。\n]{{0,24}}?)(?:出版|发行|刊行|问世|发表|印行|正式出版|结集)",
    ]
    for pat in lead_patterns:
        m = re.search(pat, lead, re.IGNORECASE)
        if m and _valid_book_year(m.group(1)):
            excerpt = lead[max(0, m.start() - 35) : min(len(lead), m.end() + 45)]
            return m.group(1), re.sub(r"\s+", " ", excerpt).strip()

    # 5. Bibliographic publisher/year pattern on dedicated catalog/publisher pages
    if not is_wikipedia:
        bib_patterns = [
            rf"(?:University\s+Press|MIT\s+Press|NYU\s+Press|Routledge|SAGE|Unwin\s+Hyman|Wiley|Springer|Blackwell|Polity|Harvard|Oxford|Cambridge|Columbia|Chicago|Yale|Princeton|Penguin|Random\s+House|Free\s+Press|Basic\s+Books|出版社|书局|印书馆)[^.\n]{{0,40}}?({_YEAR_RE})\b",
            rf"(?:19\d{{2}}|18\d{{2}}|20[01]\d)\s*-\s*\d+\s*(?:pages|页|페이지)",
        ]
        for pat in bib_patterns:
            m = re.search(pat, lead, re.IGNORECASE)
            if m:
                ym = re.search(rf"({_YEAR_RE})", m.group(0))
                if ym and _valid_book_year(ym.group(1)):
                    excerpt = lead[max(0, m.start() - 25) : min(len(lead), m.end() + 35)]
                    return ym.group(1), re.sub(r"\s+", " ", excerpt).strip()

    return "", ""


# ---------------------------------------------------------------------------
# Scholar identity & life-span year extraction
# ---------------------------------------------------------------------------
_SCHOLAR_FIELD_EN = {
    "journalism", "journalist", "communication", "communications", "media",
    "sociologist", "sociology", "public relations", "propaganda", "newspaper",
    "broadcasting", "broadcast", "television", "press", "cultural studies",
    "political economy", "agenda-setting", "agenda setting", "gatekeeping",
    "gatekeeper", "knowledge gap", "media ecology",
    "burson-marsteller", "goldsmiths", "loughborough", "nyu",
    "minnesota", "north carolina", "boston university", "illinois", "uiowa",
    "iowa", "digital capitalism", "nationwide audience", "mr. gates",
    "rural sociology", "mass culture", "prsa",
}
_SCHOLAR_FIELD_ZH = (
    "新闻学", "传播学", "新闻传播", "媒介", "媒体", "记者", "报人", "报刊",
    "公关", "社会学", "文化研究", "舆论", "复旦大学",
    "中国人民大学", "清华大学", "北京大学", "大公报", "综合新闻学",
    "应用新闻学", "网络传播", "传播与国家治理",
)
_BAD_PERSON_WIKI_TITLE = re.compile(
    r"\((?:company|firm|organization|corporation|project|book|theory|model|swimmer|"
    r"footballer|basketball|actor|actress|musician|singer|poet|novelist|politician|general|"
    r"paediatrician|pediatrician|physician|doctor|surgeon|bishop|clergyman|soldier)\)",
    re.IGNORECASE,
)


def scholar_field_relevance(scholar: Dict[str, str], text: str) -> Tuple[bool, List[str]]:
    if not text:
        return False, []
    s_text = to_simplified(strip_site_nav(text)[:10000])
    low = s_text.lower()
    hits: List[str] = []
    for kw in _SCHOLAR_FIELD_EN:
        if kw in low:
            hits.append(kw)
    for kw in _SCHOLAR_FIELD_ZH:
        if kw in s_text:
            hits.append(kw)
    desc = to_simplified(scholar.get("description_zh", ""))
    for bm in re.findall(r"《([^》]{2,20})》", desc):
        if bm in s_text:
            hits.append(f"book:{bm}")
    for term in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", desc):
        if term.lower() in low:
            hits.append(f"desc:{term}")
    # Require >=2 strong domain cues OR explicit book/unique cue
    return (len(hits) >= 2 or any(h.startswith(("book:", "desc:")) for h in hits)), hits[:10]


def scholar_wiki_title_matches(scholar: Dict[str, str], wtitle: str) -> bool:
    """Strict Wikipedia article title match: title ITSELF must be the person's name."""
    if not wtitle or reject_person_title(wtitle) or _BAD_PERSON_WIKI_TITLE.search(wtitle):
        return False
    name_en = (scholar.get("name_en", "") or "").strip()
    name_zh = (scholar.get("name_zh", "") or "").strip()
    s_wtitle = to_simplified(wtitle)
    if name_zh and zh_name_in_labels(to_simplified(name_zh), [s_wtitle]):
        return True
    if name_en and en_name_in_labels(name_en, [wtitle]):
        return True
    return False


def scholar_person_name_matches(scholar: Dict[str, str], title: str, text: str) -> bool:
    name_en = (scholar.get("name_en", "") or "").strip()
    name_zh = (scholar.get("name_zh", "") or "").strip()
    if reject_person_title(title):
        return False
    lead_head = (title or "") + " " + strip_site_nav(text or "")[:1500]
    s_title = to_simplified(title)
    s_lead = to_simplified(lead_head)
    if name_zh and (
        zh_name_in_labels(to_simplified(name_zh), [s_title])
        or to_simplified(name_zh) in s_lead
    ):
        return True
    if name_en:
        if en_name_in_labels(name_en, [title]):
            return True
        req = tokens(name_en)
        lead_toks = set(tokens(lead_head))
        if req and all(tk in lead_toks for tk in req):
            return True
    return False


def lifespan_sanity_ok(
    birth_year: str, death_year: str, active_year: str
) -> Tuple[bool, str]:
    """Enforce mandatory life-span sanity guards."""
    b: Optional[int] = None
    d: Optional[int] = None
    a: Optional[int] = None
    if birth_year:
        if not birth_year.isdigit():
            return False, f"non_digit_birth:{birth_year}"
        b = int(birth_year)
        if not (1400 <= b <= 2005):
            return False, f"birth_out_of_range:{b}"
    if death_year:
        if not death_year.isdigit():
            return False, f"non_digit_death:{death_year}"
        d = int(death_year)
        if not (1450 <= d <= CURRENT_YEAR):
            return False, f"death_out_of_range:{d}"
    if active_year and active_year.isdigit():
        a = int(active_year)
    if b is not None and a is not None:
        if b > a - 12:
            return False, f"birth_too_late_for_active:{b}>{a}-12"
    if b is not None and d is not None:
        if d <= b:
            return False, f"death_not_after_birth:{d}<={b}"
        if d - b > 115:
            return False, f"lifespan_gt_115:{d}-{b}={d - b}"
    if d is not None and a is not None:
        # Guard against matching an earlier deceased namesake (allow up to 5 yrs posthumous work)
        if d < a - 5:
            return False, f"death_too_early_for_active:{d}<{a}-5"
    return True, "ok"


def extract_scholar_lifespan(
    raw: str,
    scholar: Dict[str, str],
    is_wikipedia: bool = False,
) -> Dict[str, Any]:
    """Extract birth_year, death_year, living_confirmed, and evidence excerpt."""
    cleaned = strip_markdown(strip_site_nav(raw))
    s_text = to_simplified(cleaned)
    head = s_text[:14000]
    lead = s_text[:3500]
    birth = ""
    death = ""
    birth_excerpt = ""
    death_excerpt = ""
    living_confirmed = False
    death_note = ""

    # 0. Infobox Born / Died rows (handles Magpie markdown `Born | ...` and `| Born | ...`)
    m_born = re.search(
        r"(?m)(?:^|\|)\s*(?:Born|出生|生于|诞辰)\s*(?:\||[:：])\s*([^\n]{0,120})",
        head,
    )
    if m_born:
        chunk = m_born.group(1)
        if not re.search(_YEAR_RE, chunk):
            chunk = head[m_born.start() : m_born.end() + 200]
            chunk = re.split(
                r"(?m)^(?:Died|逝世|去世|失踪|Nationality|Education|Alma|Occupation|Known|Spouse|Children|Academic|国籍|毕业|职业)\s*\|",
                chunk,
            )[0]
        ym = re.search(rf"({_YEAR_RE})", chunk)
        if ym and 1400 <= int(ym.group(1)) <= 2005:
            birth = ym.group(1)
            birth_excerpt = re.sub(r"\s+", " ", chunk[:140]).strip()
            if re.search(r"\(age\s*\d{2,3}\)|（\d{2,3}岁）", chunk, re.I):
                living_confirmed = True

    m_died = re.search(
        r"(?m)(?:^|\|)\s*(?:Died|逝世|去世|卒于|死亡)\s*(?:\||[:：])\s*([^\n]{0,120})",
        head,
    )
    if m_died:
        chunk = m_died.group(1)
        if not re.search(_YEAR_RE, chunk):
            chunk = head[m_died.start() : m_died.end() + 200]
            chunk = re.split(
                r"(?m)^(?:Resting|Burial|Cause|Nationality|Education|Occupation|Known|Spouse|Children|国籍|安葬|死因)\s*\|",
                chunk,
            )[0]
        ym = re.search(rf"({_YEAR_RE})", chunk)
        if ym and 1450 <= int(ym.group(1)) <= CURRENT_YEAR:
            if re.search(r"disappeared|presumed|失踪|下落不明", chunk, re.I):
                death_note = "disappeared_or_presumed:" + re.sub(r"\s+", " ", chunk[:120]).strip()
            else:
                death = ym.group(1)
                death_excerpt = re.sub(r"\s+", " ", chunk[:140]).strip()
                living_confirmed = False

    # Check explicit "失踪" / "disappeared" note in Wikipedia infobox (e.g., Chu Anping)
    m_disp = re.search(r"(?m)(?:^|\|)\s*(?:Disappeared|失踪)\s*(?:\||[:：])\s*([^\n]{0,120})", head)
    if m_disp and not death:
        death_note = "disappeared:" + re.sub(r"\s+", " ", head[m_disp.start() : m_disp.end() + 80]).strip()

    # Wikipedia short description right under "From Wikipedia, the free encyclopedia"
    if is_wikipedia:
        sd_lines = [ln.strip() for ln in lead.splitlines()[:12] if ln.strip()]
        for ln in sd_lines[:6]:
            if len(ln) > 140:
                continue
            m_sd_born = re.search(rf"\(\s*born\s+({_YEAR_RE})\s*\)", ln, re.I)
            if m_sd_born:
                if not birth:
                    birth = m_sd_born.group(1)
                    birth_excerpt = ln
                if not death:
                    living_confirmed = True
            m_sd_span = re.search(rf"\(\s*({_YEAR_RE})\s*[–—\-]\s*({_YEAR_RE})\s*\)", ln)
            if m_sd_span and not re.search(r"disappeared|\?", ln, re.I):
                b2, d2 = m_sd_span.group(1), m_sd_span.group(2)
                if 1400 <= int(b2) <= 2005 and int(b2) < int(d2) <= CURRENT_YEAR and (int(d2) - int(b2)) <= 115:
                    if not birth:
                        birth = b2
                        birth_excerpt = ln
                    if not death:
                        death = d2
                        death_excerpt = ln
                        living_confirmed = False

    # 1. Anchored windows around scholar's name in cleaned text
    anchors = []
    name_en = (scholar.get("name_en", "") or "").strip()
    name_zh = to_simplified((scholar.get("name_zh", "") or "").strip())
    if name_en:
        anchors.append(name_en)
        toks = tokens(name_en)
        if len(toks) >= 2:
            anchors.append(f"{toks[0]} {toks[-1]}")
        if toks:
            anchors.append(toks[-1])
    if name_zh:
        anchors.append(name_zh)

    windows: List[str] = []
    for anc in anchors:
        if len(anc) < 2:
            continue
        for m in re.finditer(re.escape(anc), head[:12000], re.IGNORECASE):
            # Start directly at the scholar's name so line-anchored lifespan parens `(1921-2020)` match!
            windows.append(head[m.start() : m.start() + 420])
            if len(windows) >= 10:
                break
    if not windows:
        windows = [lead[:1500]]

    # Full EN date span (including `c. 1911 – 17 May 1966`, `(Month D, YYYY – Month D, YYYY)`, `Jul 31, 1931 — Mar 12, 2025`)
    full_en_span = re.compile(
        rf"(?:(?:{_MONTH_EN}\s+\d{{1,2}},?\s+|\d{{1,2}}\s+{_MONTH_EN}\s+|(?:c\.|ca\.)\s*)?({_YEAR_RE})"
        rf"\s*[–—\-]\s*(?:{_MONTH_EN}\s+\d{{1,2}},?\s+|\d{{1,2}}\s+{_MONTH_EN}\s+)({_YEAR_RE}))",
        re.IGNORECASE,
    )
    en_born_only = re.compile(
        rf"\(\s*(?:born|b\.)\s+(?:{_MONTH_EN}\s+\d{{1,2}},?\s+|\d{{1,2}}\s+{_MONTH_EN}\s+)?({_YEAR_RE})\s*(?:[,;][^)]*)?\)",
        re.IGNORECASE,
    )
    en_year_span_paren = re.compile(
        rf"(?:^|[\s#]){re.escape(anchors[0]) if anchors else r'\w+'}[^.\n]{{0,50}}?\(\s*({_YEAR_RE})\s*[–—\-]\s*({_YEAR_RE})\s*\)",
        re.IGNORECASE,
    )
    # Also match `Name (1921-2020)` / `Christine L. Nystrom (1941–2010)` anywhere near start of window
    near_year_span_paren = re.compile(
        rf"^[^.\n]{{0,85}}?\(\s*({_YEAR_RE})\s*[–—\-]\s*({_YEAR_RE})\s*\)"
    )
    zh_full_span = re.compile(
        rf"^[^。\n]{{0,18}}?[（(]\s*({_YEAR_RE})\s*年?(?:\d{{1,2}}月(?:\d{{1,2}}日)?)?\s*[–—\-至~]\s*({_YEAR_RE})\s*年?(?:\d{{1,2}}月(?:\d{{1,2}}日)?)?\s*[)）]"
    )
    zh_born_open = re.compile(
        rf"^[^。\n]{{0,18}}?[（(]\s*({_YEAR_RE})\s*年(?:\d{{1,2}}月(?:\d{{1,2}}日)?)?\s*(?:[–—\-]\s*[)）]|生\s*[)）])"
    )

    for w in windows:
        if not (birth and death):
            m = full_en_span.search(w)
            if m:
                b2, d2 = m.group(1), m.group(2)
                if 1400 <= int(b2) <= 2005 and int(b2) < int(d2) <= CURRENT_YEAR and (int(d2) - int(b2)) <= 115:
                    ctx = w[max(0, m.start() - 35) : min(len(w), m.end() + 35)]
                    if not re.search(r"disappeared|presumed|\?|father|mother|wife|husband", ctx, re.I):
                        if not birth:
                            birth, birth_excerpt = b2, re.sub(r"\s+", " ", ctx).strip()
                        if not death:
                            death, death_excerpt = d2, re.sub(r"\s+", " ", ctx).strip()
                            living_confirmed = False
        if not (birth and death):
            m = zh_full_span.search(w)
            if m:
                b2, d2 = m.group(1), m.group(2)
                if 1400 <= int(b2) <= 2005 and int(b2) < int(d2) <= CURRENT_YEAR and (int(d2) - int(b2)) <= 115:
                    if not re.search(r"失踪|下落不明|\?|？", m.group(0)):
                        if not birth:
                            birth, birth_excerpt = b2, re.sub(r"\s+", " ", w[:160]).strip()
                        if not death:
                            death, death_excerpt = d2, re.sub(r"\s+", " ", w[:160]).strip()
                            living_confirmed = False
        if not birth:
            m = en_born_only.search(w)
            if m and 1400 <= int(m.group(1)) <= 2005:
                birth = m.group(1)
                birth_excerpt = re.sub(r"\s+", " ", w[:180]).strip()
                if not death:
                    living_confirmed = True
        if not birth:
            m = zh_born_open.search(w)
            if m and 1400 <= int(m.group(1)) <= 2005:
                birth = m.group(1)
                birth_excerpt = re.sub(r"\s+", " ", w[:160]).strip()
                if not death:
                    living_confirmed = True
        if not (birth and death):
            m = near_year_span_paren.search(w)
            if m:
                # Inspect ONLY the text BEFORE the parenthesis (never past the paren into "taught at NYU" or "Fellow PRSA"!)
                paren_pos = w.find("(", m.start())
                prefix = w[: paren_pos if paren_pos >= 0 else m.start()].lower()
                if not re.search(
                    r"\b(?:professor|editor|president|director|chair|dean|served|member|worked|taught|fellow|lecturer|report)\b|主任|院长|报告|工程|职务",
                    prefix,
                ):
                    b2, d2 = m.group(1), m.group(2)
                    if 1400 <= int(b2) <= 2005 and int(b2) < int(d2) <= CURRENT_YEAR and (int(d2) - int(b2)) <= 115:
                        if not re.search(r"disappeared|presumed|\?", m.group(0), re.I):
                            if not birth:
                                birth, birth_excerpt = b2, re.sub(r"\s+", " ", w[:160]).strip()
                            if not death:
                                death, death_excerpt = d2, re.sub(r"\s+", " ", w[:160]).strip()
                                living_confirmed = False

    # 2. Chinese & English prose birth year
    if not birth:
        for pat in (
            rf"({_YEAR_RE})\s*年(?:\d{{1,2}}月(?:\d{{1,2}}日)?)?\s*(?:生|出生)",
            rf"(?:出生于|生于|出生日期[：:]?|出生年月[：:]?)\s*({_YEAR_RE})\s*年",
            rf"\b(?:was\s+)?born\s+(?:on\s+(?:{_MONTH_EN}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+|\d{{1,2}}\s+{_MONTH_EN}\s+)|in\s+(?:{_MONTH_EN}\s+)?)?({_YEAR_RE})\b",
        ):
            for w in windows:
                m = re.search(pat, w, re.IGNORECASE)
                if m and 1400 <= int(m.group(1)) <= 2005:
                    birth = m.group(1)
                    birth_excerpt = re.sub(r"\s+", " ", w[max(0, m.start() - 20) : m.end() + 50]).strip()
                    break
            if birth:
                break

    # 3. Explicit death prose in anchored windows (ONLY if not living_confirmed!)
    if not death and not living_confirmed:
        death_pats = [
            rf"(?:died|passed\s+away)\s+(?:[^.\n]{{0,45}}?)?(?:on\s+(?:{_MONTH_EN}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+|\d{{1,2}}\s+{_MONTH_EN}\s+)|in\s+(?:{_MONTH_EN}\s+)?)({_YEAR_RE})\b",
            rf"({_YEAR_RE})\s*年(?:\d{{1,2}}月(?:\d{{1,2}}日)?)?\s*(?:[^。\n]{{0,18}}?)(?:逝世|去世|病逝|离世|辞世|遇害|卒)",
            rf"(?:逝世于|去世于|病逝于|卒于|死于)\s*({_YEAR_RE})\s*年",
        ]
        for pat in death_pats:
            for w in windows:
                m = re.search(pat, w, re.IGNORECASE)
                if m and 1450 <= int(m.group(1)) <= CURRENT_YEAR:
                    ctx = w[max(0, m.start() - 45) : min(len(w), m.end() + 45)]
                    if re.search(
                        r"\b(?:father|mother|wife|husband|spouse|son|daughter|brother|sister|grandmother|grandfather|mentor|teacher|hoax|rumor|rumour|falsely|disappeared)\b|父亲|母亲|妻子|丈夫|祖母|祖父|外祖母|外祖父|谣言|误传|失踪",
                        ctx,
                        re.I,
                    ):
                        continue
                    death = m.group(1)
                    death_excerpt = re.sub(r"\s+", " ", ctx).strip()
                    break
            if death:
                break

    if is_wikipedia and not death:
        if "Living people" in cleaned or "在世人物" in s_text:
            living_confirmed = True

    return {
        "birth_year": birth,
        "death_year": death,
        "living_confirmed": living_confirmed,
        "birth_excerpt": mask_cn_mobile(birth_excerpt),
        "death_excerpt": mask_cn_mobile(death_excerpt),
        "death_note": mask_cn_mobile(death_note),
    }


# ---------------------------------------------------------------------------
# CSV I/O preserving exact encoding, BOM, line endings, columns, and row order
# ---------------------------------------------------------------------------
def read_csv_preserve(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    lineterminator = "\r\n" if b"\r\n" in raw else "\n"
    encoding = "utf-8-sig" if has_bom else "utf-8"
    text = raw.decode(encoding)
    reader = csv.DictReader(text.splitlines())
    fieldnames = list(reader.fieldnames or [])
    rows = [{k: (v or "") for k, v in r.items() if k is not None} for r in reader]
    return {
        "path": path,
        "has_bom": has_bom,
        "encoding": encoding,
        "lineterminator": lineterminator,
        "fieldnames": fieldnames,
        "rows": rows,
    }


def write_csv_preserve(meta: Dict[str, Any], rows: List[Dict[str, str]]) -> None:
    path: Path = meta["path"]
    with path.open("w", encoding=meta["encoding"], newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=meta["fieldnames"],
            lineterminator=meta["lineterminator"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in meta["fieldnames"]})


# ---------------------------------------------------------------------------
# Track 1: Books Enrichment
# ---------------------------------------------------------------------------
def is_chinese_origin_book(book: Dict[str, str], scholar: Dict[str, str]) -> bool:
    sid = (scholar.get("school_id") or "").upper()
    if sid.startswith("SCHOOL_CN_") or sid == "SCHOOL_HK_PRESS":
        return True
    bid = book.get("book_id", "")
    return bid in {
        "BOOK_CHINA_NEWS_HISTORY",
        "BOOK_CHINESE_PRESS_HISTORY",
        "BOOK_COMMUNICATION_COURSE",
        "BOOK_INTERNET_COMMUNICATION_INTRO",
        "BOOK_JOURNALISM_INTRODUCTION",
        "BOOK_NEWS_THEORY_TEN_LECTURES",
        "BOOK_NORTHWEST_CORNER",
        "BOOK_SHENGSHI_WEIYAN",
        "BOOK_TAOFEN_WENJI",
        "BOOK_XINWENXUE",
        "BOOK_YANSHAN_YEHUA",
    }


def _page_dedicated_to_book(hit_title: str, title_en: str, title_zh: str) -> bool:
    """True when the page title itself is primarily about this book (not an author bio/list)."""
    zh_core = zh_book_core(title_zh)
    if zh_core:
        s_ht = to_simplified(hit_title or "")
        if len(zh_core) <= 3:
            if zh_title_exact(zh_core, s_ht) or f"《{zh_core}》" in s_ht:
                return True
        elif zh_core in s_ht:
            return True
    en_short = (title_en or "").split(":")[0].strip()
    en_toks = tokens(en_short, drop_stop=True)
    if en_toks and all(tk in set(tokens(hit_title or "", drop_stop=True)) for tk in en_toks):
        return True
    return False


def enrich_book_row(
    magpie: CachedMagpie,
    ext_row: Dict[str, str],
    book: Dict[str, str],
    scholar: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, Any], List[Dict[str, str]]]:
    """Enrich a single books_external.csv row with ZERO OVERWRITE of non-empty fields."""
    bid = ext_row["book_id"]
    title_zh = (book.get("title_zh") or "").strip()
    title_en = (book.get("title_en") or "").strip()
    short_en = title_en.split(":")[0].strip()
    recorded_year = (book.get("year") or "").strip()
    author_zh = (scholar.get("name_zh") or "").strip()
    author_en = (scholar.get("name_en") or "").strip()
    author_surname = tokens(author_en)[-1] if tokens(author_en) else ""
    cn_origin = is_chinese_origin_book(book, scholar)

    old = dict(ext_row)
    new = dict(ext_row)
    audit: Dict[str, Any] = {
        "book_id": bid,
        "title_zh": title_zh,
        "title_en": title_en,
        "author_zh": author_zh,
        "author_en": author_en,
        "recorded_year": recorded_year,
        "before": old,
        "queries": [],
        "candidates_checked": [],
        "evidence_excerpt": "",
        "status": "unchanged",
    }

    # Case A: Already has wikipedia_url, just missing found_year
    if old.get("wikipedia_url") and not old.get("found_year"):
        wurl = old["wikipedia_url"]
        ok, body = magpie.fetch_wikipedia_body(wurl)
        found_y, excerpt = ("", "")
        if ok and body:
            found_y, excerpt = extract_book_pub_year(
                body,
                title_en=title_en,
                title_zh=title_zh,
                is_wikipedia=True,
                page_is_dedicated_to_book=True,
            )
            audit["candidates_checked"].append({
                "url": wurl,
                "kind": "existing_wikipedia_fetch",
                "ok": ok,
                "found_year": found_y,
            })
        if found_y:
            new["found_year"] = found_y
            new["year_status"] = compute_year_status(recorded_year, found_y)
            new["enriched_at"] = ENRICHED_AT
            audit["evidence_excerpt"] = mask_cn_mobile(excerpt)
            audit["status"] = "filled_year_only"
        audit["after"] = new
        return new, audit, _diff_log("books", bid, title_zh, old, new, new.get("evidence_url", ""), excerpt)

    # Case B: Missing wikipedia_url / evidence_url / found_year (the 30 no_wiki_hit rows)
    wiki_candidates: List[Tuple[int, SearchHit, str]] = []
    auth_candidates: List[Tuple[int, SearchHit]] = []
    seen_urls: set[str] = set()

    zh_core = zh_book_core(title_zh)
    if cn_origin:
        queries = [
            f'"{zh_core}" {author_zh} 维基百科',
            f'"{zh_core}" {author_zh} 出版社 出版',
            f'"{zh_core}" {author_zh} {recorded_year} 初版 OR 维基文库 OR 百度百科',
        ]
        if short_en and len(tokens(short_en, drop_stop=True)) >= 2:
            queries.append(f'"{short_en}" {author_surname} wikipedia')
    else:
        queries = [
            f'"{short_en}" {author_surname} wikipedia',
            f'"{short_en}" {author_surname} book published',
            f'"{short_en}" {author_surname} "first published" OR {recorded_year}',
        ]

    for q in queries:
        audit["queries"].append(q)
        hits = magpie.search(q, count=8, enforce_local_domain_filter=False)
        for h in hits:
            canon_wiki = normalize_canonical_wikipedia_url(h.url)
            if canon_wiki:
                if canon_wiki not in seen_urls:
                    seen_urls.add(canon_wiki)
                    wtitle = wiki_title_from_url(canon_wiki)
                    is_en = extract_netloc(canon_wiki) == "en.wikipedia.org"
                    title_ok = (
                        en_book_title_matches(title_en, wtitle)
                        or zh_title_compatible(title_zh, wtitle)
                    )
                    if title_ok:
                        wiki_candidates.append((0 if is_en else 1, h, canon_wiki))
                    else:
                        audit["candidates_checked"].append({
                            "url": canon_wiki,
                            "kind": "wiki_title_rejected",
                            "wiki_title": wtitle,
                        })
                continue

            tier = book_evidence_tier(h.url)
            if tier > 0 and h.url not in seen_urls:
                seen_urls.add(h.url)
                auth_candidates.append((tier, h))

    chosen_wiki = ""
    chosen_evidence = ""
    chosen_year = ""
    chosen_excerpt = ""

    # 1) Canonical Wikipedia candidates first
    wiki_candidates.sort(key=lambda x: (x[0], x[1].rank))
    for _prio, hit, canon_url in wiki_candidates[:3]:
        ok, body = magpie.fetch_wikipedia_body(canon_url)
        if not ok or not body or is_missing_or_disambig_wiki(body):
            audit["candidates_checked"].append({
                "url": canon_url,
                "kind": "wiki_fetch_missing_or_disambig",
                "ok": ok,
            })
            continue
        if looks_like_person_bio_wiki(body) or looks_like_company_wiki(body):
            audit["candidates_checked"].append({
                "url": canon_url,
                "kind": "wiki_rejected_person_or_company",
            })
            continue
        if not author_mentioned(scholar, body[:12000]):
            audit["candidates_checked"].append({
                "url": canon_url,
                "kind": "wiki_rejected_author_mismatch",
            })
            continue
        if not looks_like_work_page(body):
            audit["candidates_checked"].append({
                "url": canon_url,
                "kind": "wiki_rejected_not_work",
            })
            continue
        fy, ex = extract_book_pub_year(
            body,
            title_en=title_en,
            title_zh=title_zh,
            is_wikipedia=True,
            page_is_dedicated_to_book=True,
        )
        audit["candidates_checked"].append({
            "url": canon_url,
            "kind": "wiki_verified",
            "found_year": fy,
        })
        chosen_wiki = canon_url
        chosen_evidence = canon_url
        chosen_year = fy
        chosen_excerpt = ex
        break

    # 2) Authoritative non-Wikipedia candidates (if no wiki OR wiki lacked pub year)
    def _score_auth_hit(item: Tuple[int, SearchHit]) -> Tuple[int, int, int, int]:
        tier, h = item
        blob = f"{h.title} {h.snippet}"
        s_blob = to_simplified(blob)
        ded = 0 if _page_dedicated_to_book(h.title, title_en, title_zh) else 1
        has_author = 0 if author_mentioned(scholar, blob) else 1
        # Prefer hits whose snippet already shows an explicit publication year (especially first-edition cues)
        fy_snip, _ = extract_book_pub_year(
            blob,
            title_en=title_en,
            title_zh=title_zh,
            is_wikipedia=False,
            page_is_dedicated_to_book=(ded == 0),
        )
        has_yr = 0 if fy_snip else 1
        # Prefer non-edition-specific pages (avoid "（第五版）" if first-edition evidence exists)
        later_ed = 1 if re.search(r"第[二三四五六七八九十\d]版|2nd|3rd|4th|5th|6th|7th|edition", h.title, re.I) else 0
        return (has_yr, has_author, later_ed, tier)

    if not chosen_wiki or not chosen_year:
        auth_candidates.sort(key=_score_auth_hit)
        checked_auth = 0
        fallback_ev_url = ""
        fallback_ev_excerpt = ""
        for tier, h in auth_candidates:
            if checked_auth >= 7:
                break
            blob = f"{h.title} {h.snippet}"
            s_blob = to_simplified(blob)
            title_in_hit = (
                (len(zh_core) >= 2 and zh_core in s_blob)
                or (
                    bool(tokens(short_en, drop_stop=True))
                    and all(
                        tk in set(tokens(blob, drop_stop=True))
                        for tk in tokens(short_en, drop_stop=True)
                    )
                )
            )
            if not title_in_hit:
                continue
            if re.search(r"/search\b|[?&]q=|[?&]query=|/tag/|/category/|/series/", h.url, re.I):
                continue
            checked_auth += 1
            fres = magpie.fetch(h.url, max_chars=24000)
            page_text = fres.get("text", "") if fres.get("ok") else ""
            verify_text = page_text if len(page_text) >= 250 else blob
            if not author_mentioned(scholar, verify_text[:12000]):
                audit["candidates_checked"].append({
                    "url": h.url,
                    "tier": tier,
                    "kind": "auth_rejected_author_mismatch",
                    "fetched_ok": bool(fres.get("ok")),
                })
                continue
            s_verify = to_simplified(verify_text[:12000])
            title_confirmed = (
                (len(zh_core) >= 2 and zh_core in s_verify)
                or (
                    bool(tokens(short_en, drop_stop=True))
                    and all(
                        tk in set(tokens(verify_text[:12000], drop_stop=True))
                        for tk in tokens(short_en, drop_stop=True)
                    )
                )
            )
            if not title_confirmed:
                audit["candidates_checked"].append({
                    "url": h.url,
                    "tier": tier,
                    "kind": "auth_rejected_title_mismatch",
                })
                continue

            is_ded = _page_dedicated_to_book(h.title, title_en, title_zh)
            fy, ex = extract_book_pub_year(
                verify_text,
                title_en=title_en,
                title_zh=title_zh,
                is_wikipedia=False,
                page_is_dedicated_to_book=is_ded,
            )
            if not fy and verify_text is not blob:
                fy, ex = extract_book_pub_year(
                    blob,
                    title_en=title_en,
                    title_zh=title_zh,
                    is_wikipedia=False,
                    page_is_dedicated_to_book=is_ded,
                )
            audit["candidates_checked"].append({
                "url": h.url,
                "tier": tier,
                "kind": "auth_verified",
                "dedicated": is_ded,
                "fetched_ok": bool(fres.get("ok")),
                "found_year": fy,
            })

            if not fallback_ev_url:
                fallback_ev_url = h.url
                fallback_ev_excerpt = ex or mask_cn_mobile(h.snippet[:160])

            if fy and not chosen_year:
                chosen_year = fy
                chosen_excerpt = ex
                if not chosen_wiki:
                    chosen_evidence = h.url
                else:
                    # Keep canonical Wikipedia URL as wikipedia_url, and use the year-verifying authoritative URL as evidence_url if wiki didn't have the year!
                    chosen_evidence = h.url
                break

        if not chosen_evidence and fallback_ev_url:
            chosen_evidence = fallback_ev_url
            chosen_excerpt = fallback_ev_excerpt

    # Apply strictly with ZERO OVERWRITE of non-empty fields
    changed = False
    if chosen_wiki and not new.get("wikipedia_url"):
        new["wikipedia_url"] = chosen_wiki
        changed = True
    if chosen_evidence and not new.get("evidence_url"):
        new["evidence_url"] = chosen_evidence
        changed = True
    if chosen_year and not new.get("found_year"):
        new["found_year"] = chosen_year
        changed = True

    if changed:
        if new.get("found_year"):
            new["year_status"] = compute_year_status(recorded_year, new["found_year"])
        elif new.get("wikipedia_url") or new.get("evidence_url"):
            new["year_status"] = "no_year_found"
        new["enriched_at"] = ENRICHED_AT
        audit["status"] = "enriched_wiki" if chosen_wiki else "enriched_evidence"
    audit["evidence_excerpt"] = mask_cn_mobile(chosen_excerpt)
    audit["after"] = new
    return new, audit, _diff_log("books", bid, title_zh, old, new, new.get("evidence_url", ""), chosen_excerpt)


# ---------------------------------------------------------------------------
# Track 2: Scholars Enrichment
# ---------------------------------------------------------------------------
def _scholar_query_hints(scholar: Dict[str, str]) -> Tuple[str, str]:
    desc = scholar.get("description_zh", "")
    zh_books = re.findall(r"《([^》]+)》", desc)
    zh_hint_parts = []
    for kw in ("复旦", "人民大学", "清华", "新闻学", "传播学", "网络传播", "大公报", "公关"):
        if kw in desc:
            zh_hint_parts.append(kw)
    if zh_books:
        zh_hint_parts.append(zh_books[0])
    zh_hint = " ".join(zh_hint_parts[:2]) or "新闻 传播"

    school_map = {
        "SCHOOL_PR": "public relations",
        "SCHOOL_POLITICAL_ECO": "media political economy communication",
        "SCHOOL_BIRMINGHAM": "media cultural studies sociology",
        "SCHOOL_MEDIA_ECOLOGY": "media ecology NYU",
        "SCHOOL_AGENDA": "agenda setting journalism",
        "SCHOOL_COLUMBIA": "communication journalism",
        "SCHOOL_CN_ACADEMIC": "journalism communication professor",
        "SCHOOL_CN_REFORM": "Ta Kung Pao newspaper",
    }
    en_hint = school_map.get(scholar.get("school_id", ""), "journalism communication")
    if "Mr. Gates" in desc:
        en_hint = "gatekeeper journalism"
    elif "知识沟" in desc:
        en_hint = "knowledge gap Minnesota"
    elif "Burson-Marsteller" in desc:
        en_hint = "Burson-Marsteller public relations"
    elif "《数字资本主义》" in desc:
        en_hint = "Digital Capitalism communication Illinois"
    elif "《全国观众》" in desc:
        en_hint = "Goldsmiths media communications"
    return en_hint, zh_hint


def enrich_scholar_core(
    magpie: CachedMagpie,
    ext_row: Dict[str, str],
    scholar: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, Any], List[Dict[str, str]]]:
    """Enrich a scholar missing wikipedia_url / birth_year / evidence_url."""
    sid = ext_row["scholar_id"]
    name_zh = (scholar.get("name_zh") or "").strip()
    name_en = (scholar.get("name_en") or "").strip()
    active_year = (scholar.get("active_year") or "").strip()
    en_hint, zh_hint = _scholar_query_hints(scholar)
    cn_scholar = (scholar.get("school_id") or "").startswith("SCHOOL_CN_")

    old = dict(ext_row)
    new = dict(ext_row)
    audit: Dict[str, Any] = {
        "scholar_id": sid,
        "name_zh": name_zh,
        "name_en": name_en,
        "active_year": active_year,
        "subtrack": "missing_core",
        "before": old,
        "queries": [],
        "candidates_checked": [],
        "status": "unchanged",
    }

    if cn_scholar:
        queries = [
            f'"{name_zh}" {zh_hint} 维基百科',
            f'"{name_zh}" {zh_hint} 出生 OR 简历 OR 教授',
            f'"{name_zh}" {zh_hint} 百度百科',
        ]
    else:
        # Handle initials like "Phillip J. Tichenor" -> also search without middle initial
        no_mid = re.sub(r"\s+[A-Z]\.\s+", " ", name_en).strip()
        name_clause = f'"{name_en}" OR "{no_mid}"' if no_mid != name_en else f'"{name_en}"'
        queries = [
            f'{name_clause} {en_hint} wikipedia',
            f'{name_clause} {en_hint} professor OR biography OR obituary',
            f'{name_clause} obituary OR "in memoriam" OR remembering OR biography',
        ]

    wiki_candidates: List[Tuple[int, SearchHit, str]] = []
    auth_candidates: List[Tuple[int, SearchHit]] = []
    seen_urls: set[str] = set()

    for q in queries:
        audit["queries"].append(q)
        hits = magpie.search(q, count=8, enforce_local_domain_filter=False)
        for h in hits:
            canon_wiki = normalize_canonical_wikipedia_url(h.url)
            if canon_wiki:
                if canon_wiki not in seen_urls:
                    seen_urls.add(canon_wiki)
                    wtitle = wiki_title_from_url(canon_wiki)
                    if scholar_wiki_title_matches(scholar, wtitle):
                        is_en = extract_netloc(canon_wiki) == "en.wikipedia.org"
                        wiki_candidates.append((0 if is_en else 1, h, canon_wiki))
                    else:
                        audit["candidates_checked"].append({
                            "url": canon_wiki,
                            "kind": "wiki_title_rejected",
                            "wiki_title": wtitle,
                        })
                continue
            tier = scholar_evidence_tier(h.url)
            if tier > 0 and h.url not in seen_urls:
                seen_urls.add(h.url)
                auth_candidates.append((tier, h))

    chosen_wiki = ""
    chosen_evidence = ""
    chosen_birth = ""
    chosen_death = ""
    chosen_excerpt = ""

    # 1) Canonical Wikipedia first (strict title + person + field + lifespan checks)
    wiki_candidates.sort(key=lambda x: (x[0], x[1].rank))
    for _prio, h, canon_url in wiki_candidates[:3]:
        ok, body = magpie.fetch_wikipedia_body(canon_url)
        if not ok or not body or is_missing_or_disambig_wiki(body):
            audit["candidates_checked"].append({
                "url": canon_url,
                "kind": "wiki_fetch_missing_or_disambig",
            })
            continue
        if looks_like_company_wiki(body):
            audit["candidates_checked"].append({
                "url": canon_url,
                "kind": "wiki_rejected_company",
            })
            continue
        rel_ok, rel_hits = scholar_field_relevance(scholar, body)
        if not rel_ok:
            audit["candidates_checked"].append({
                "url": canon_url,
                "kind": "wiki_rejected_field_mismatch",
                "rel_hits": rel_hits,
            })
            continue
        ls = extract_scholar_lifespan(body, scholar, is_wikipedia=True)
        b_ok, b_msg = lifespan_sanity_ok(ls["birth_year"], ls["death_year"], active_year)
        audit["candidates_checked"].append({
            "url": canon_url,
            "kind": "wiki_verified" if b_ok else f"wiki_sanity_failed:{b_msg}",
            "rel_hits": rel_hits,
            "lifespan": ls,
        })
        if not b_ok:
            continue
        chosen_wiki = canon_url
        chosen_evidence = canon_url
        chosen_birth = ls["birth_year"]
        chosen_death = ls["death_year"]
        chosen_excerpt = ls["birth_excerpt"] or ls["death_excerpt"]
        break

    # 2) Authoritative non-Wikipedia bio/obituary (if no wiki OR wiki lacked birth_year)
    if not chosen_wiki or not chosen_birth:
        # Sort: prefer hits whose title/snippet already mention a birth/death year, then authority tier
        def _score_scholar_auth(item: Tuple[int, SearchHit]) -> Tuple[int, int, int]:
            tier, h = item
            blob = f"{h.title} {h.snippet}"
            ls_snip = extract_scholar_lifespan(blob, scholar, is_wikipedia=False)
            has_yr = 0 if ls_snip["birth_year"] else 1
            in_title = 0 if (
                (name_zh and to_simplified(name_zh) in to_simplified(h.title))
                or (name_en and tokens(name_en)[-1] in set(tokens(h.title)))
            ) else 1
            return (has_yr, in_title, tier)

        auth_candidates.sort(key=_score_scholar_auth)
        checked = 0
        fallback_ev = ""
        fallback_ex = ""
        for tier, h in auth_candidates:
            if checked >= 7:
                break
            blob = f"{h.title} {h.snippet}"
            if not scholar_person_name_matches(scholar, h.title, h.snippet):
                continue
            checked += 1
            fres = magpie.fetch(h.url, max_chars=24000)
            page_text = fres.get("text", "") if fres.get("ok") else ""
            verify_text = page_text if len(page_text) >= 250 else blob
            if not scholar_person_name_matches(scholar, h.title, verify_text[:3000]):
                audit["candidates_checked"].append({
                    "url": h.url,
                    "tier": tier,
                    "kind": "auth_name_rejected",
                })
                continue
            rel_ok, rel_hits = scholar_field_relevance(scholar, verify_text)
            if not rel_ok:
                audit["candidates_checked"].append({
                    "url": h.url,
                    "tier": tier,
                    "kind": "auth_field_rejected",
                    "rel_hits": rel_hits,
                })
                continue
            ls = extract_scholar_lifespan(verify_text, scholar, is_wikipedia=False)
            if not ls["birth_year"] and verify_text is not blob:
                ls2 = extract_scholar_lifespan(blob, scholar, is_wikipedia=False)
                if ls2["birth_year"]:
                    ls = ls2
            b_ok, b_msg = lifespan_sanity_ok(ls["birth_year"], ls["death_year"], active_year)
            audit["candidates_checked"].append({
                "url": h.url,
                "tier": tier,
                "kind": "auth_verified" if b_ok else f"auth_sanity_failed:{b_msg}",
                "fetched_ok": bool(fres.get("ok")),
                "rel_hits": rel_hits,
                "lifespan": ls,
            })
            if not b_ok:
                continue
            if not fallback_ev:
                fallback_ev = h.url
                fallback_ex = ls["birth_excerpt"] or ls["death_excerpt"] or mask_cn_mobile(h.snippet[:160])
            if ls["birth_year"] and not chosen_birth:
                chosen_birth = ls["birth_year"]
                chosen_excerpt = ls["birth_excerpt"] or chosen_excerpt
                if not chosen_wiki:
                    chosen_evidence = h.url
            if ls["death_year"] and not chosen_death:
                ok_d, _ = lifespan_sanity_ok(chosen_birth or ls["birth_year"], ls["death_year"], active_year)
                if ok_d:
                    chosen_death = ls["death_year"]
                    if not chosen_excerpt:
                        chosen_excerpt = ls["death_excerpt"]
                    if not chosen_wiki and not chosen_evidence:
                        chosen_evidence = h.url
            if chosen_birth and (chosen_death or ls["living_confirmed"]):
                break

        if not chosen_evidence and fallback_ev:
            chosen_evidence = fallback_ev
            chosen_excerpt = fallback_ex

    changed = False
    if chosen_wiki and not new.get("wikipedia_url"):
        new["wikipedia_url"] = chosen_wiki
        changed = True
    if chosen_evidence and not new.get("evidence_url"):
        new["evidence_url"] = chosen_evidence
        changed = True
    if chosen_birth and not new.get("birth_year"):
        new["birth_year"] = chosen_birth
        changed = True
    if chosen_death and not new.get("death_year"):
        ok_final, _ = lifespan_sanity_ok(new.get("birth_year", ""), chosen_death, active_year)
        if ok_final:
            new["death_year"] = chosen_death
            changed = True

    if changed:
        new["method"] = "magpie_wiki_fetch" if chosen_wiki else "magpie_web_fetch"
        new["enriched_at"] = ENRICHED_AT
        audit["status"] = "enriched_wiki" if chosen_wiki else "enriched_evidence"
    audit["after"] = new
    return new, audit, _diff_log("scholars_core", sid, name_zh, old, new, new.get("evidence_url", ""), chosen_excerpt)


def verify_scholar_death_year(
    magpie: CachedMagpie,
    ext_row: Dict[str, str],
    scholar: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, Any], List[Dict[str, str]]]:
    """Verify whether a scholar with existing wikipedia_url & blank death_year is deceased."""
    sid = ext_row["scholar_id"]
    name_zh = (scholar.get("name_zh") or "").strip()
    name_en = (scholar.get("name_en") or "").strip()
    birth_year = (ext_row.get("birth_year") or "").strip()
    active_year = (scholar.get("active_year") or "").strip()
    existing_wiki = (ext_row.get("wikipedia_url") or "").strip()

    old = dict(ext_row)
    new = dict(ext_row)
    audit: Dict[str, Any] = {
        "scholar_id": sid,
        "name_zh": name_zh,
        "name_en": name_en,
        "birth_year": birth_year,
        "existing_wikipedia_url": existing_wiki,
        "subtrack": "verify_death_year",
        "before": old,
        "checks": [],
        "status": "living_or_unconfirmed",
    }

    urls_to_check = [existing_wiki]
    if existing_wiki.startswith("https://simple.wikipedia.org/wiki/"):
        en_equiv = existing_wiki.replace("https://simple.wikipedia.org/", "https://en.wikipedia.org/", 1)
        urls_to_check.insert(0, en_equiv)

    found_death = ""
    death_excerpt = ""
    death_source_url = existing_wiki
    living_confirmed = False
    zh_interwiki: Optional[str] = None

    for wurl in urls_to_check:
        if not wurl:
            continue
        ok, body = magpie.fetch_wikipedia_body(wurl)
        if not ok or not body:
            audit["checks"].append({"url": wurl, "ok": False})
            continue
        # Capture linked zh.wikipedia counterpart if present (e.g. translation banner on Deng Tuo / Chu Anping)
        m_zh = re.search(r"https://zh\.wikipedia\.org/wiki/[^\s)\]]+", body[:3000])
        if m_zh and not zh_interwiki:
            zh_interwiki = normalize_canonical_wikipedia_url(m_zh.group(0))
        ls = extract_scholar_lifespan(body, scholar, is_wikipedia=True)
        audit["checks"].append({
            "url": wurl,
            "ok": True,
            "lifespan": ls,
        })
        if ls["death_year"]:
            s_ok, s_msg = lifespan_sanity_ok(birth_year, ls["death_year"], active_year)
            if s_ok:
                found_death = ls["death_year"]
                death_excerpt = ls["death_excerpt"]
                death_source_url = wurl
                break
            audit["checks"][-1]["sanity_rejected"] = s_msg
        if ls["living_confirmed"] and not wurl.startswith("https://simple.wikipedia.org/"):
            living_confirmed = True
            break

    # Cross-check interwiki zh.wikipedia page if linked and death still unconfirmed
    if not found_death and not living_confirmed and zh_interwiki and zh_interwiki not in urls_to_check:
        ok, body = magpie.fetch_wikipedia_body(zh_interwiki)
        if ok and body and not is_missing_or_disambig_wiki(body):
            ls = extract_scholar_lifespan(body, scholar, is_wikipedia=True)
            audit["checks"].append({
                "url": zh_interwiki,
                "kind": "interwiki_zh_check",
                "lifespan": ls,
            })
            if ls["death_year"]:
                s_ok, _ = lifespan_sanity_ok(birth_year, ls["death_year"], active_year)
                if s_ok:
                    found_death = ls["death_year"]
                    death_excerpt = ls["death_excerpt"]
                    death_source_url = zh_interwiki
            elif ls["living_confirmed"]:
                living_confirmed = True

    if found_death and not new.get("death_year"):
        new["death_year"] = found_death
        prev_method = (new.get("method") or "").strip()
        if "magpie_death_year" not in prev_method:
            new["method"] = f"{prev_method}+magpie_death_year" if prev_method else "magpie_death_year"
        new["enriched_at"] = ENRICHED_AT
        audit["status"] = "filled_death_year"
    elif living_confirmed:
        audit["status"] = "confirmed_living"

    audit["after"] = new
    return new, audit, _diff_log("scholars_death", sid, name_zh, old, new, death_source_url, death_excerpt)


def verify_missing_scholar_name_en(
    magpie: CachedMagpie,
    scholar_row: Dict[str, str],
    ext_row: Dict[str, str],
) -> Tuple[Dict[str, str], Dict[str, Any], List[Dict[str, str]]]:
    sid = scholar_row["scholar_id"]
    name_zh = (scholar_row.get("name_zh") or "").strip()
    old = dict(scholar_row)
    new = dict(scholar_row)
    audit: Dict[str, Any] = {
        "scholar_id": sid,
        "name_zh": name_zh,
        "subtrack": "missing_name_en",
        "before": old,
        "queries": [],
        "checks": [],
        "status": "unchanged",
    }
    if old.get("name_en"):
        audit["after"] = new
        return new, audit, []

    queries = [f'"{name_zh}" wikipedia', f'"{name_zh}" "Ta Kung Pao" OR "L\'Impartial" wikipedia']
    found_en = ""
    evidence_url = ""
    excerpt = ""

    for q in queries:
        audit["queries"].append(q)
        hits = magpie.search(q, count=8, enforce_local_domain_filter=False)
        for h in hits:
            canon_w = normalize_canonical_wikipedia_url(h.url)
            if not canon_w or extract_netloc(canon_w) != "en.wikipedia.org":
                continue
            ok, body = magpie.fetch_wikipedia_body(canon_w)
            if not ok or not body or is_missing_or_disambig_wiki(body):
                continue
            s_body = to_simplified(body[:3000])
            if to_simplified(name_zh) in s_body and (
                "大公报" in s_body
                or "ta kung pao" in s_body.lower()
                or "journalist" in s_body.lower()
                or "catholic" in s_body.lower()
            ):
                wtitle = wiki_title_from_url(canon_w)
                clean_en = re.sub(r"\s*\([^)]*\)\s*$", "", wtitle).strip()
                if clean_en and re.fullmatch(r"[A-Za-z][A-Za-z\s\.\-']{2,50}", clean_en):
                    found_en = clean_en
                    evidence_url = canon_w
                    excerpt = f"en.wikipedia title={wtitle}; verified zh={name_zh} in lead"
                    audit["checks"].append({
                        "url": canon_w,
                        "wiki_title": wtitle,
                        "verified_name_en": found_en,
                    })
                    break
        if found_en:
            break

    if found_en and not new.get("name_en"):
        new["name_en"] = found_en
        audit["status"] = "filled_name_en"
    audit["after"] = new
    return new, audit, _diff_log("scholars_name_en", sid, name_zh, old, new, evidence_url, excerpt)


# ---------------------------------------------------------------------------
# Provenance logging & post-checks
# ---------------------------------------------------------------------------
def _diff_log(
    track: str,
    entity_id: str,
    label: str,
    old: Dict[str, str],
    new: Dict[str, str],
    evidence_url: str,
    excerpt: str,
) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    domain = extract_hostname(evidence_url) if evidence_url else ""
    safe_excerpt = mask_cn_mobile(re.sub(r"\s+", " ", excerpt or "").strip())[:220]
    for field in new:
        ov = old.get(field, "")
        nv = new.get(field, "")
        if ov != nv:
            entries.append({
                "track": track,
                "entity_id": entity_id,
                "label": label,
                "action": "fill_blank" if not ov else "update_status_meta",
                "field": field,
                "old_value": ov,
                "new_value": nv,
                "evidence_url": evidence_url,
                "source_domain": domain,
                "evidence_excerpt": safe_excerpt,
                "enriched_at": ENRICHED_AT,
            })
    return entries


def verify_invariants(
    old_books_ext: List[Dict[str, str]],
    new_books_ext: List[Dict[str, str]],
    old_scholars_ext: List[Dict[str, str]],
    new_scholars_ext: List[Dict[str, str]],
    old_scholars: List[Dict[str, str]],
    new_scholars: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Assert ZERO overwrite of substantive non-empty values, valid URLs, 0 mobile leaks."""
    overwrites: List[str] = []
    bad_urls: List[str] = []
    mobile_leaks: List[str] = []
    lifespan_errors: List[str] = []

    scholar_active = {r["scholar_id"]: r.get("active_year", "") for r in new_scholars}

    # 1. books_external.csv
    assert len(old_books_ext) == len(new_books_ext), "books_external row count mismatch"
    for o, n in zip(old_books_ext, new_books_ext):
        assert o["book_id"] == n["book_id"], "books_external row order changed"
        for col in ("wikipedia_url", "found_year", "evidence_url"):
            if o.get(col) and n.get(col) != o.get(col):
                overwrites.append(f"books_external:{o['book_id']}.{col}: {o[col]!r} -> {n[col]!r}")
        for ucol in ("wikipedia_url", "evidence_url"):
            u = n.get(ucol, "")
            if u:
                if not is_valid_http_url(u):
                    bad_urls.append(f"books_external:{n['book_id']}.{ucol}={u}")
                elif ucol == "wikipedia_url" and not o.get(ucol) and not is_canonical_wikipedia_url(u):
                    bad_urls.append(f"books_external_non_canonical_wiki:{n['book_id']}={u}")
        for col, val in n.items():
            if contains_cn_mobile(val):
                mobile_leaks.append(f"books_external:{n['book_id']}.{col}")

    # 2. scholars_external.csv
    assert len(old_scholars_ext) == len(new_scholars_ext), "scholars_external row count mismatch"
    for o, n in zip(old_scholars_ext, new_scholars_ext):
        assert o["scholar_id"] == n["scholar_id"], "scholars_external row order changed"
        for col in ("wikipedia_url", "birth_year", "death_year", "evidence_url"):
            if o.get(col) and n.get(col) != o.get(col):
                overwrites.append(f"scholars_external:{o['scholar_id']}.{col}: {o[col]!r} -> {n[col]!r}")
        for ucol in ("wikipedia_url", "evidence_url"):
            u = n.get(ucol, "")
            if u:
                if not is_valid_http_url(u):
                    bad_urls.append(f"scholars_external:{n['scholar_id']}.{ucol}={u}")
                elif ucol == "wikipedia_url" and not o.get(ucol) and not is_canonical_wikipedia_url(u):
                    bad_urls.append(f"scholars_external_non_canonical_wiki:{n['scholar_id']}={u}")
        if (not o.get("birth_year") and n.get("birth_year")) or (not o.get("death_year") and n.get("death_year")):
            ok, msg = lifespan_sanity_ok(
                n.get("birth_year", ""),
                n.get("death_year", ""),
                scholar_active.get(n["scholar_id"], ""),
            )
            if not ok:
                lifespan_errors.append(f"scholars_external:{n['scholar_id']}:{msg}")
        for col, val in n.items():
            if contains_cn_mobile(val):
                mobile_leaks.append(f"scholars_external:{n['scholar_id']}.{col}")

    # 3. scholars.csv
    assert len(old_scholars) == len(new_scholars), "scholars row count mismatch"
    for o, n in zip(old_scholars, new_scholars):
        assert o["scholar_id"] == n["scholar_id"], "scholars row order changed"
        for col in o:
            if o.get(col) and n.get(col) != o.get(col):
                overwrites.append(f"scholars:{o['scholar_id']}.{col}: {o[col]!r} -> {n[col]!r}")
        for col, val in n.items():
            if contains_cn_mobile(val):
                mobile_leaks.append(f"scholars:{n['scholar_id']}.{col}")

    return {
        "overwrites": overwrites,
        "bad_urls": bad_urls,
        "mobile_leaks": mobile_leaks,
        "lifespan_errors": lifespan_errors,
        "passed": not (overwrites or bad_urls or mobile_leaks or lifespan_errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Magpie enrichment for news-journalism-kg (2026).")
    parser.add_argument("--write", action="store_true", help="Write enriched CSVs (default: dry-run).")
    parser.add_argument("--track", choices=["books", "scholars", "all"], default="all")
    parser.add_argument("--no-cache", action="store_true", help="Ignore local Magpie cache.")
    args = parser.parse_args()

    books_meta = read_csv_preserve(CSV_DIR / "books.csv")
    books_ext_meta = read_csv_preserve(CSV_DIR / "books_external.csv")
    scholars_meta = read_csv_preserve(CSV_DIR / "scholars.csv")
    scholars_ext_meta = read_csv_preserve(CSV_DIR / "scholars_external.csv")

    books_by_id = {r["book_id"]: r for r in books_meta["rows"]}
    scholars_by_id = {r["scholar_id"]: r for r in scholars_meta["rows"]}

    old_books_ext = [dict(r) for r in books_ext_meta["rows"]]
    new_books_ext = [dict(r) for r in books_ext_meta["rows"]]
    old_scholars_ext = [dict(r) for r in scholars_ext_meta["rows"]]
    new_scholars_ext = [dict(r) for r in scholars_ext_meta["rows"]]
    old_scholars = [dict(r) for r in scholars_meta["rows"]]
    new_scholars = [dict(r) for r in scholars_meta["rows"]]

    client = MagpieWebSearchClient(config_path=ROOT / "config.json")
    magpie = CachedMagpie(client, use_cache=not args.no_cache)

    book_audits: List[Dict[str, Any]] = []
    scholar_audits: List[Dict[str, Any]] = []
    log_rows: List[Dict[str, str]] = []

    # --- TRACK 1: BOOKS ---
    if args.track in ("books", "all"):
        target_indices = [
            i
            for i, r in enumerate(new_books_ext)
            if r.get("year_status") == "no_wiki_hit"
            or not r.get("wikipedia_url")
            or not r.get("evidence_url")
            or not r.get("found_year")
        ]
        print(f"[Track 1: Books] Targeting {len(target_indices)} rows in books_external.csv...", flush=True)
        for idx_num, row_idx in enumerate(target_indices, 1):
            ext_row = new_books_ext[row_idx]
            bid = ext_row["book_id"]
            book = books_by_id.get(bid, {"book_id": bid})
            scholar = scholars_by_id.get(book.get("scholar_id", ""), {})
            updated, audit, logs = enrich_book_row(magpie, ext_row, book, scholar)
            new_books_ext[row_idx] = updated
            book_audits.append(audit)
            log_rows.extend(logs)
            print(
                f"  [{idx_num:02d}/{len(target_indices)}] {bid}: {audit['status']} | "
                f"wiki={'Y' if updated.get('wikipedia_url') else 'N'} | "
                f"ev={extract_hostname(updated.get('evidence_url', '')) or '-'} | "
                f"year={updated.get('found_year') or '-'} ({updated.get('year_status')})",
                flush=True,
            )

    # --- TRACK 2: SCHOLARS ---
    if args.track in ("scholars", "all"):
        core_indices = [
            i
            for i, r in enumerate(new_scholars_ext)
            if not r.get("wikipedia_url") or not r.get("birth_year") or not r.get("evidence_url")
        ]
        print(f"[Track 2A: Scholars Missing Core] Targeting {len(core_indices)} rows...", flush=True)
        for idx_num, row_idx in enumerate(core_indices, 1):
            ext_row = new_scholars_ext[row_idx]
            sid = ext_row["scholar_id"]
            scholar = scholars_by_id.get(sid, {"scholar_id": sid})
            updated, audit, logs = enrich_scholar_core(magpie, ext_row, scholar)
            new_scholars_ext[row_idx] = updated
            scholar_audits.append(audit)
            log_rows.extend(logs)
            print(
                f"  [{idx_num:02d}/{len(core_indices)}] {sid} ({scholar.get('name_zh','')}): {audit['status']} | "
                f"wiki={'Y' if updated.get('wikipedia_url') else 'N'} | "
                f"ev={extract_hostname(updated.get('evidence_url', '')) or '-'} | "
                f"years={updated.get('birth_year') or '?'}-{updated.get('death_year') or ''}",
                flush=True,
            )

        death_indices = [
            i
            for i, r in enumerate(new_scholars_ext)
            if r.get("wikipedia_url") and r.get("birth_year") and r.get("evidence_url") and not r.get("death_year")
            and i not in set(core_indices)
        ]
        print(f"[Track 2B: Scholars Missing Death Year] Verifying {len(death_indices)} rows...", flush=True)
        for idx_num, row_idx in enumerate(death_indices, 1):
            ext_row = new_scholars_ext[row_idx]
            sid = ext_row["scholar_id"]
            scholar = scholars_by_id.get(sid, {"scholar_id": sid})
            updated, audit, logs = verify_scholar_death_year(magpie, ext_row, scholar)
            new_scholars_ext[row_idx] = updated
            scholar_audits.append(audit)
            log_rows.extend(logs)
            print(
                f"  [{idx_num:02d}/{len(death_indices)}] {sid} ({scholar.get('name_zh','')}): {audit['status']} | "
                f"years={updated.get('birth_year') or '?'}-{updated.get('death_year') or 'living'}",
                flush=True,
            )

        ext_by_id = {r["scholar_id"]: r for r in new_scholars_ext}
        name_en_indices = [i for i, r in enumerate(new_scholars) if not r.get("name_en")]
        print(f"[Track 2C: Scholars Missing name_en] Checking {len(name_en_indices)} rows...", flush=True)
        for row_idx in name_en_indices:
            srow = new_scholars[row_idx]
            sid = srow["scholar_id"]
            updated, audit, logs = verify_missing_scholar_name_en(
                magpie, srow, ext_by_id.get(sid, {})
            )
            new_scholars[row_idx] = updated
            scholar_audits.append(audit)
            log_rows.extend(logs)
            print(
                f"  {sid} ({srow.get('name_zh','')}): {audit['status']} -> name_en={updated.get('name_en')!r}",
                flush=True,
            )

    checks = verify_invariants(
        old_books_ext,
        new_books_ext,
        old_scholars_ext,
        new_scholars_ext,
        old_scholars,
        new_scholars,
    )
    print(f"[Invariants] passed={checks['passed']} | {checks}", flush=True)
    if not checks["passed"]:
        print("ABORTING WRITE: invariant check failed!", file=sys.stderr)
        return 2

    summary = {
        "enriched_at": ENRICHED_AT,
        "mode": "write" if args.write else "dry_run",
        "mcp_url": client.mcp_url,
        "live_search_calls": client.search_calls,
        "live_fetch_calls": client.fetch_calls,
        "total_queries_logged": len(magpie.queries_log),
        "total_fetches_logged": len(magpie.fetches_log),
        "books": {
            "total_rows": len(new_books_ext),
            "targets_processed": len(book_audits),
            "wiki_before": sum(1 for r in old_books_ext if r.get("wikipedia_url")),
            "wiki_after": sum(1 for r in new_books_ext if r.get("wikipedia_url")),
            "evidence_before": sum(1 for r in old_books_ext if r.get("evidence_url")),
            "evidence_after": sum(1 for r in new_books_ext if r.get("evidence_url")),
            "found_year_before": sum(1 for r in old_books_ext if r.get("found_year")),
            "found_year_after": sum(1 for r in new_books_ext if r.get("found_year")),
            "year_status_counts_after": _count_field(new_books_ext, "year_status"),
        },
        "scholars_external": {
            "total_rows": len(new_scholars_ext),
            "wiki_before": sum(1 for r in old_scholars_ext if r.get("wikipedia_url")),
            "wiki_after": sum(1 for r in new_scholars_ext if r.get("wikipedia_url")),
            "evidence_before": sum(1 for r in old_scholars_ext if r.get("evidence_url")),
            "evidence_after": sum(1 for r in new_scholars_ext if r.get("evidence_url")),
            "birth_year_before": sum(1 for r in old_scholars_ext if r.get("birth_year")),
            "birth_year_after": sum(1 for r in new_scholars_ext if r.get("birth_year")),
            "death_year_before": sum(1 for r in old_scholars_ext if r.get("death_year")),
            "death_year_after": sum(1 for r in new_scholars_ext if r.get("death_year")),
        },
        "scholars_core": {
            "total_rows": len(new_scholars),
            "name_en_before": sum(1 for r in old_scholars if r.get("name_en")),
            "name_en_after": sum(1 for r in new_scholars if r.get("name_en")),
        },
        "invariants": checks,
    }

    REPAIR_DIR.mkdir(parents=True, exist_ok=True)
    audit_payload = {
        "summary": summary,
        "books": book_audits,
        "scholars": scholar_audits,
    }
    audit_json_text = mask_cn_mobile(
        json.dumps(audit_payload, ensure_ascii=False, indent=2)
    )
    assert not contains_cn_mobile(audit_json_text), "Mobile leak in audit JSON"
    (REPAIR_DIR / "magpie_njk_enrichment_audit_2026.json").write_text(
        audit_json_text + "\n", encoding="utf-8"
    )

    log_fields = [
        "track",
        "entity_id",
        "label",
        "action",
        "field",
        "old_value",
        "new_value",
        "evidence_url",
        "source_domain",
        "evidence_excerpt",
        "enriched_at",
    ]
    log_path = REPAIR_DIR / "magpie_njk_enrichment_log_2026.csv"
    with log_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=log_fields)
        w.writeheader()
        for row in log_rows:
            safe_row = {k: mask_cn_mobile(str(row.get(k, ""))) for k in log_fields}
            w.writerow(safe_row)

    if args.write:
        if new_books_ext != old_books_ext:
            write_csv_preserve(books_ext_meta, new_books_ext)
        if new_scholars_ext != old_scholars_ext:
            write_csv_preserve(scholars_ext_meta, new_scholars_ext)
        if new_scholars != old_scholars:
            write_csv_preserve(scholars_meta, new_scholars)
        print("[WRITE COMPLETE] Updated CSVs preserving exact encoding/BOM/EOL.", flush=True)
    else:
        print("[DRY RUN] CSVs not modified. Pass --write to save.", flush=True)

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def _count_field(rows: List[Dict[str, str]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in rows:
        v = r.get(field, "") or "(blank)"
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    raise SystemExit(main())
