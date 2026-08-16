from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CJK_RE = re.compile(r"[\u4E00-\u9FFF]")


def contains_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise
    rows: list[dict[str, str]] = []
    # io.StringIO keeps the csv module's handling of quoted fields intact,
    # including embedded newlines that splitlines() would break apart
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None:
        return rows
    for row in reader:
        cleaned = {k: (v or "").strip() for k, v in row.items() if k is not None}
        if all(v == "" for v in cleaned.values()):
            continue
        rows.append(cleaned)
    return rows


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "context": self.context,
        }


def require_columns(
    *,
    issues: list[Issue],
    table: str,
    rows: list[dict[str, str]],
    required: Iterable[str],
) -> bool:
    if not rows:
        issues.append(
            Issue(
                level="error",
                code="E3001",
                message="CSV is empty or unreadable",
                context={"table": table},
            )
        )
        return False
    columns = set(rows[0].keys())
    ok = True
    for col in required:
        if col not in columns:
            ok = False
            issues.append(
                Issue(
                    level="error",
                    code="E3002",
                    message="Missing required column",
                    context={"table": table, "column": col},
                )
            )
    return ok


def split_multi(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def validate(base_dir: Path) -> list[Issue]:
    issues: list[Issue] = []

    scholars = read_csv(base_dir / "scholars.csv")
    concepts = read_csv(base_dir / "concepts.csv")
    propositions = read_csv(base_dir / "propositions.csv")
    passages = read_csv(base_dir / "passages.csv")
    influences = read_csv(base_dir / "influences.csv")
    years = read_csv(base_dir / "years.csv")
    books = read_csv(base_dir / "books.csv")

    require_columns(
        issues=issues,
        table="scholars.csv",
        rows=scholars,
        required=["scholar_id", "name_zh"],
    )
    require_columns(
        issues=issues,
        table="books.csv",
        rows=books,
        required=["book_id", "title_zh", "scholar_id", "year"],
    )
    require_columns(
        issues=issues,
        table="concepts.csv",
        rows=concepts,
        required=["concept_id", "name_zh"],
    )
    require_columns(
        issues=issues,
        table="propositions.csv",
        rows=propositions,
        required=[
            "proposition_id",
            "scholar_id",
            "proposition_text_zh",
            "year",
            "evidence_passage_ids",
        ],
    )
    require_columns(
        issues=issues,
        table="passages.csv",
        rows=passages,
        required=["passage_id", "source_id", "source_title", "source_type", "passage_text"],
    )
    require_columns(
        issues=issues,
        table="influences.csv",
        rows=influences,
        required=[
            "influence_id",
            "subject_type",
            "subject_id",
            "object_type",
            "object_id",
            "evidence_passage_id",
        ],
    )
    require_columns(
        issues=issues,
        table="years.csv",
        rows=years,
        required=["year_id", "year_value"],
    )

    scholar_ids = {r.get("scholar_id", "") for r in scholars if r.get("scholar_id")}
    concept_ids = {r.get("concept_id", "") for r in concepts if r.get("concept_id")}
    proposition_ids = {r.get("proposition_id", "") for r in propositions if r.get("proposition_id")}
    passage_ids = {r.get("passage_id", "") for r in passages if r.get("passage_id")}
    year_values = {r.get("year_value", "") for r in years if r.get("year_value")}

    def require_field(table: str, row_id_field: str, row: dict[str, str], field: str) -> None:
        if not row.get(field, "").strip():
            issues.append(
                Issue(
                    level="error",
                    code="E3003",
                    message="Missing required value",
                    context={"table": table, row_id_field: row.get(row_id_field, ""), "field": field},
                )
            )

    # Books Validation
    for row in books:
        require_field("books.csv", "book_id", row, "book_id")
        require_field("books.csv", "book_id", row, "title_zh")
        require_field("books.csv", "book_id", row, "scholar_id")
        require_field("books.csv", "book_id", row, "year")

        sid = row.get("scholar_id", "")
        if sid and sid not in scholar_ids:
             issues.append(
                Issue(
                    level="error",
                    code="E2001",
                    message="ForeignKeyNotFound",
                    context={
                        "table": "books.csv",
                        "book_id": row.get("book_id", ""),
                        "field": "scholar_id",
                        "value": sid,
                    },
                )
            )

    # Relations Validation
    relations = read_csv(base_dir / "relations.csv")
    for row in relations:
        require_field("relations.csv", "subject_id", row, "subject_id")
        require_field("relations.csv", "relation", row, "relation")
        require_field("relations.csv", "object_id", row, "object_id")

        sub = row.get("subject_id", "")
        obj = row.get("object_id", "")

        if sub and sub not in scholar_ids:
             issues.append(Issue(level="error", code="E2001", message="ForeignKeyNotFound", context={"table": "relations.csv", "field": "subject_id", "value": sub}))
        if obj and obj not in scholar_ids:
             issues.append(Issue(level="error", code="E2001", message="ForeignKeyNotFound", context={"table": "relations.csv", "field": "object_id", "value": obj}))

    # Passage: R3
    for row in passages:
        require_field("passages.csv", "passage_id", row, "passage_id")
        require_field("passages.csv", "passage_id", row, "source_id")
        require_field("passages.csv", "passage_id", row, "source_title")
        require_field("passages.csv", "passage_id", row, "source_type")
        require_field("passages.csv", "passage_id", row, "passage_text")
        text = row.get("passage_text", "")
        if text and not contains_cjk(text):
            issues.append(
                Issue(
                    level="error",
                    code="E1003",
                    message="PassageMissingChineseText",
                    context={"passage_id": row.get("passage_id", "")},
                )
            )

    # Proposition: R1 + foreign keys
    for row in propositions:
        require_field("propositions.csv", "proposition_id", row, "proposition_id")
        require_field("propositions.csv", "proposition_id", row, "scholar_id")
        require_field("propositions.csv", "proposition_id", row, "proposition_text_zh")
        require_field("propositions.csv", "proposition_id", row, "year")
        require_field("propositions.csv", "proposition_id", row, "evidence_passage_ids")

        proposition_id = row.get("proposition_id", "")
        scholar_id = row.get("scholar_id", "")
        if scholar_id and scholar_id not in scholar_ids:
            issues.append(
                Issue(
                    level="error",
                    code="E2001",
                    message="ForeignKeyNotFound",
                    context={
                        "table": "propositions.csv",
                        "proposition_id": proposition_id,
                        "field": "scholar_id",
                        "value": scholar_id,
                    },
                )
            )

        evidence_ids = split_multi(row.get("evidence_passage_ids", ""))
        if not evidence_ids:
            issues.append(
                Issue(
                    level="error",
                    code="E1001",
                    message="PropositionMissingPassage",
                    context={"proposition_id": proposition_id},
                )
            )
        else:
            for pid in evidence_ids:
                if pid not in passage_ids:
                    issues.append(
                        Issue(
                            level="error",
                            code="E2001",
                            message="ForeignKeyNotFound",
                            context={
                                "table": "propositions.csv",
                                "proposition_id": proposition_id,
                                "field": "evidence_passage_ids",
                                "value": pid,
                            },
                        )
                    )

        year = row.get("year", "")
        if year and not re.fullmatch(r"-?\d{1,4}", year):
            issues.append(
                Issue(
                    level="error",
                    code="E3004",
                    message="InvalidYearFormat",
                    context={"table": "propositions.csv", "proposition_id": proposition_id, "value": year},
                )
            )
        elif year and year_values and year not in year_values:
            issues.append(
                Issue(
                    level="warning",
                    code="W4001",
                    message="YearNotInYearsTable",
                    context={"table": "propositions.csv", "proposition_id": proposition_id, "year": year},
                )
            )

        # Optional: concept ids exist
        for cid in split_multi(row.get("concept_ids", "")):
            if cid not in concept_ids:
                issues.append(
                    Issue(
                        level="warning",
                        code="W4002",
                        message="ConceptIdNotFound",
                        context={"table": "propositions.csv", "proposition_id": proposition_id, "concept_id": cid},
                    )
                )

    # Influence: R2 + foreign keys
    for row in influences:
        require_field("influences.csv", "influence_id", row, "influence_id")
        require_field("influences.csv", "influence_id", row, "subject_type")
        require_field("influences.csv", "influence_id", row, "subject_id")
        require_field("influences.csv", "influence_id", row, "object_type")
        require_field("influences.csv", "influence_id", row, "object_id")
        require_field("influences.csv", "influence_id", row, "evidence_passage_id")

        influence_id = row.get("influence_id", "")
        evidence_passage_id = row.get("evidence_passage_id", "")
        if not evidence_passage_id:
            issues.append(
                Issue(
                    level="error",
                    code="E1002",
                    message="InfluenceMissingEvidence",
                    context={"influence_id": influence_id},
                )
            )
        elif evidence_passage_id not in passage_ids:
            issues.append(
                Issue(
                    level="error",
                    code="E2001",
                    message="ForeignKeyNotFound",
                    context={
                        "table": "influences.csv",
                        "influence_id": influence_id,
                        "field": "evidence_passage_id",
                        "value": evidence_passage_id,
                    },
                )
            )

        subject_type = row.get("subject_type", "")
        subject_id = row.get("subject_id", "")
        object_type = row.get("object_type", "")
        object_id = row.get("object_id", "")

        valid_types = {"Scholar", "Proposition"}
        if subject_type and subject_type not in valid_types:
            issues.append(
                Issue(
                    level="error",
                    code="E3005",
                    message="InvalidType",
                    context={"table": "influences.csv", "influence_id": influence_id, "field": "subject_type"},
                )
            )
        if object_type and object_type not in valid_types:
            issues.append(
                Issue(
                    level="error",
                    code="E3005",
                    message="InvalidType",
                    context={"table": "influences.csv", "influence_id": influence_id, "field": "object_type"},
                )
            )

        if subject_type == "Scholar" and subject_id and subject_id not in scholar_ids:
            issues.append(
                Issue(
                    level="error",
                    code="E2001",
                    message="ForeignKeyNotFound",
                    context={
                        "table": "influences.csv",
                        "influence_id": influence_id,
                        "field": "subject_id",
                        "value": subject_id,
                    },
                )
            )
        if subject_type == "Proposition" and subject_id and subject_id not in proposition_ids:
            issues.append(
                Issue(
                    level="error",
                    code="E2001",
                    message="ForeignKeyNotFound",
                    context={
                        "table": "influences.csv",
                        "influence_id": influence_id,
                        "field": "subject_id",
                        "value": subject_id,
                    },
                )
            )
        if object_type == "Scholar" and object_id and object_id not in scholar_ids:
            issues.append(
                Issue(
                    level="error",
                    code="E2001",
                    message="ForeignKeyNotFound",
                    context={
                        "table": "influences.csv",
                        "influence_id": influence_id,
                        "field": "object_id",
                        "value": object_id,
                    },
                )
            )
        if object_type == "Proposition" and object_id and object_id not in proposition_ids:
            issues.append(
                Issue(
                    level="error",
                    code="E2001",
                    message="ForeignKeyNotFound",
                    context={
                        "table": "influences.csv",
                        "influence_id": influence_id,
                        "field": "object_id",
                        "value": object_id,
                    },
                )
            )

    return issues


def main() -> int:
    config = {}
    config_path = Path("config.json")
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print("Warning: config.json exists but is invalid JSON.", file=sys.stderr)

    default_csv_dir = config.get("paths", {}).get("csv_dir", "data/csv")

    parser = argparse.ArgumentParser(description="Validate CSV templates against P3 rules.")
    parser.add_argument("--dir", default=str(Path(default_csv_dir)), help=f"CSV directory (default: {default_csv_dir})")
    parser.add_argument("--json", action="store_true", help="Output issues as JSON")
    args = parser.parse_args()

    base_dir = Path(args.dir)
    try:
        issues = validate(base_dir)
    except FileNotFoundError as e:
        print(f"Missing file: {e.filename}", file=sys.stderr)
        return 2

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    if args.json:
        payload = {"errors": [e.to_dict() for e in errors], "warnings": [w.to_dict() for w in warnings]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for issue in issues:
            ctx = " ".join(f"{k}={v}" for k, v in issue.context.items() if v != "")
            print(f"[{issue.level.upper()}] {issue.code} {issue.message} {ctx}".rstrip())
        print(f"Summary: errors={len(errors)} warnings={len(warnings)} tables_dir={base_dir}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
