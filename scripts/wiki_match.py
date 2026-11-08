# -*- coding: utf-8 -*-
"""Pure checks for Wikipedia titles, person identity, and book-title overlap.

Network lookups live in repair_external.py. These functions stay free of I/O
so a wrong page can be rejected without calling an API.
"""
from __future__ import annotations

import re
import unicodedata

_SUFFIX = {"jr", "sr", "ii", "iii", "iv"}
_STOP = {"a", "an", "the", "of", "in", "on", "and", "de", "la", "le", "to", "for"}
_MEDIA = re.compile(
    r"\((?:film|album|song|band|tv series|television series|television|video game|miniseries)\)",
    re.IGNORECASE,
)
_BAD_PERSON = re.compile(
    r"high school|fictitious|disambiguation|campaigns of|\(film\)|\(album\)|\(band\)|\(tv |\(television",
    re.IGNORECASE,
)
_HUMAN = "Q5"
_FICTIONAL = "Q15632617"
# book, literary work, written work, novel, scholarly article, article,
# short story, essay
_WORK_Q = {"Q571", "Q7725634", "Q47461344", "Q8261", "Q13442814", "Q191067", "Q49084", "Q35760"}
_NOT_WORK_Q = {"Q11424", "Q5398426", "Q7889", "Q15416"}
_WORK_WORDS = ("book", "novel", "essay", "treatise", "monograph", "著作", "小说", "小說", "文集", "書籍", "书")


def norm_latin(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.casefold().replace("'", " ")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def tokens(text: str, drop_stop: bool = False) -> list[str]:
    out: list[str] = []
    for token in norm_latin(text).split():
        if token in _SUFFIX or len(token) <= 1:
            continue
        if drop_stop and token in _STOP:
            continue
        out.append(token)
    return out


def reject_person_title(title: str) -> bool:
    return bool(_BAD_PERSON.search(title or ""))


def en_name_in_labels(name_en: str, labels: list[str]) -> bool:
    wanted = tokens(name_en)
    if not wanted:
        return False
    for label in labels:
        have = set(tokens(label))
        if all(token in have for token in wanted):
            return True
    return False


# Enough traditional forms to compare a simplified scholar name with a zh-wiki label.
_T2S = str.maketrans({
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
})


def _simplified(text: str) -> str:
    return (text or "").translate(_T2S)


def zh_name_in_labels(name_zh: str, labels: list[str]) -> bool:
    name = _simplified((name_zh or "").strip())
    if len(name) < 2:
        return False
    for label in labels:
        text = _simplified((label or "").strip())
        if not text:
            continue
        if name == text:
            return True
        if len(name) >= 3 and name in text:
            return True
    return False


def identity_match(name_en: str, name_zh: str, labels_en: list[str], labels_zh: list[str]) -> bool:
    if zh_name_in_labels(name_zh, labels_zh):
        return True
    return en_name_in_labels(name_en, labels_en)


def lifespan_ok(birth: str, active: str) -> bool:
    if not birth or not active or not str(birth).isdigit() or not str(active).isdigit():
        return True
    return int(birth) <= int(active)


def titles_compatible(query: str, candidate: str) -> bool:
    """True when a wiki title is the same work, not a longer awards/film page."""
    if not query or not candidate:
        return False
    if _MEDIA.search(candidate) or re.search(r"disambiguation", candidate, re.IGNORECASE):
        return False
    query_main = (query or "").split(":")[0]
    candidate_main = re.sub(r"\([^)]*\)", " ", candidate)
    wanted = tokens(query_main, drop_stop=True)
    got = tokens(candidate_main, drop_stop=True)
    if not wanted or not got:
        return False
    if got == wanted:
        return True
    # Wiki often drops a subtitle: "The Filter Bubble" vs the long English title.
    if set(got) < set(wanted) and (len(got) >= 2 or (len(got) == 1 and len(got[0]) >= 8)):
        return True
    return False


def zh_title_exact(title_zh: str, candidate: str) -> bool:
    core = re.sub(r"[《》\s]", "", title_zh or "")
    base = re.sub(r"（[^）]*）|\([^)]*\)", "", candidate or "")
    base = re.sub(r"[《》\s]", "", base)
    return bool(core) and core == base


def rank_person_titles(name: str, titles: list[str]) -> list[str]:
    """Opensearch hits that actually contain the name. Never falls back to hit 0."""
    name_cf = (name or "").casefold()
    kept = [
        title
        for title in titles
        if title and not reject_person_title(title) and (
            name_cf in title.casefold() or en_name_in_labels(name, [title]) or zh_name_in_labels(name, [title])
        )
    ]

    def sort_key(title: str) -> tuple:
        exact = 0 if norm_latin(title) == norm_latin(name) else 1
        extra = abs(len(tokens(title)) - len(tokens(name)))
        return (exact, extra, titles.index(title))

    return sorted(kept, key=sort_key)


def claim_ids(entity: dict, pid: str) -> list[str]:
    found: list[str] = []
    for claim in entity.get("claims", {}).get(pid, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak.get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            found.append(value["id"])
    return found


def claim_year(entity: dict, pid: str, *, reject_year: int | None = None) -> str:
    claims = list(entity.get("claims", {}).get(pid, []))
    claims.sort(key=lambda claim: 0 if claim.get("rank") == "preferred" else 1)
    for claim in claims:
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak.get("datavalue", {}).get("value")
        if not isinstance(value, dict) or "time" not in value:
            continue
        match = re.search(r"([+-]?\d{4})", value.get("time") or "")
        if not match:
            continue
        year = int(match.group(1))
        if year < 1000 or year > 2026:
            continue
        if reject_year is not None and year == reject_year:
            continue
        return str(year)
    return ""


def label_list(entity: dict, lang: str) -> list[str]:
    labels = []
    value = entity.get("labels", {}).get(lang, {}).get("value")
    if value:
        labels.append(value)
    for alias in entity.get("aliases", {}).get(lang, []):
        text = alias.get("value")
        if text:
            labels.append(text)
    return labels


def description_text(entity: dict) -> str:
    parts = []
    for lang in ("en", "zh"):
        value = entity.get("descriptions", {}).get(lang, {}).get("value")
        if value:
            parts.append(value)
    return " ".join(parts)


def is_human(entity: dict) -> bool:
    ids = set(claim_ids(entity, "P31"))
    return _HUMAN in ids and _FICTIONAL not in ids


def is_work(entity: dict) -> bool:
    if is_human(entity):
        return False
    ids = set(claim_ids(entity, "P31"))
    if ids & _NOT_WORK_Q:
        return False
    if ids & _WORK_Q:
        return True
    blob = description_text(entity).casefold()
    return any(word.casefold() in blob for word in _WORK_WORDS)


def year_status(recorded: str, found: str) -> str:
    if not found:
        return "no_year_found"
    try:
        recorded_year = int(recorded) if recorded else None
        found_year = int(found)
    except ValueError:
        return "bad_recorded_year" if recorded else "found_no_record"
    if recorded_year is None:
        return "found_no_record"
    if found_year == recorded_year:
        return "verified"
    if abs(found_year - recorded_year) <= 2:
        return "near_match"
    return "mismatch"
