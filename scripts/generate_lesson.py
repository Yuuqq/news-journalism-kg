import argparse
import csv
import random
import sys
from pathlib import Path
from typing import List, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / 'data' / 'csv'

def read_csv(file_name: str) -> List[Dict[str, str]]:
    path = DATA_DIR / file_name
    if not path.exists():
        sys.stderr.write(f"Error: {file_name} not found in {DATA_DIR}\n")
        sys.exit(1)

    with open(path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        return list(reader)

def generate_discussion_questions(scholar_name: str, proposition: str) -> List[str]:
    return [
        f"结合当前的媒介环境，你认为{scholar_name}的这一观点在今天是否依然适用？为什么？",
        f"在日常生活中，你能找到哪些具体的例子来印证或反驳“{proposition}”？",
        f"如果这个理论是正确的，它对我们的新闻实践和信息消费有什么启示？"
    ]

def main():
    parser = argparse.ArgumentParser(description="Generate a lesson card (Markdown) from the Knowledge Graph.")
    parser.add_argument('--scholar', type=str, help="Filter by scholar_id (e.g., SCH_MCLUHAN)")
    parser.add_argument('--school', type=str, help="Filter by school_id (e.g., SCHOOL_TORONTO)")
    parser.add_argument('--random', action='store_true', help="Randomly select one proposition from the filtered list")
    parser.add_argument('--seed', type=int, help="Random seed for reproducibility")

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    scholars_data = read_csv('scholars.csv')
    propositions_data = read_csv('propositions.csv')
    passages_data = read_csv('passages.csv')

    scholars_dict = {s['scholar_id']: s for s in scholars_data}
    passages_dict = {p['passage_id']: p for p in passages_data}

    filtered_props = propositions_data

    if args.school:
        valid_scholars = {s['scholar_id'] for s in scholars_data if s.get('school_id') == args.school}
        filtered_props = [p for p in filtered_props if p.get('scholar_id') in valid_scholars]

    if args.scholar:
        filtered_props = [p for p in filtered_props if p.get('scholar_id') == args.scholar]

    if not filtered_props:
        sys.stderr.write("No propositions found matching the given criteria.\n")
        sys.exit(1)

    if args.random:
        selected_prop = random.choice(filtered_props)
    else:
        # Just pick the first one if not random
        selected_prop = filtered_props[0]

    scholar = scholars_dict.get(selected_prop.get('scholar_id', ''), {})
    scholar_name = scholar.get('name_zh', '未知学者')
    scholar_en = scholar.get('name_en', '')
    scholar_desc = scholar.get('description_zh', '')

    prop_text = selected_prop.get('proposition_text_zh', '')
    evidence_ids = [eid.strip() for eid in selected_prop.get('evidence_passage_ids', '').split(';') if eid.strip()]

    evidences = [passages_dict.get(eid) for eid in evidence_ids if eid in passages_dict]

    questions = generate_discussion_questions(scholar_name, prop_text)

    # Output Markdown
    print(f"# 📖 备课卡片：{scholar_name} 的核心命题\n")

    print("## 👨‍🏫 学者简介")
    print(f"**姓名**：{scholar_name}" + (f" ({scholar_en})" if scholar_en else ""))
    if scholar_desc:
        print(f"**简介**：{scholar_desc}")
    print()

    print("## 💡 核心命题")
    print(f"> {prop_text}")
    print()

    print("## 📚 证据与原文出处")
    if not evidences:
        print("*暂无文献证据*")
    else:
        for idx, ev in enumerate(evidences, 1):
            source = ev.get('source_title', '未知来源')
            url = ev.get('source_url', '')
            text = ev.get('passage_text', '')
            source_display = f"[{source}]({url})" if url else source
            print(f"### 证据 {idx}：{source_display}")
            print(f"**原文段落**：\n{text}")
            print()

    print("## 💬 课堂讨论题")
    for idx, q in enumerate(questions, 1):
        print(f"{idx}. {q}")
    print()

if __name__ == '__main__':
    main()
