import json
import shutil
import sys
from pathlib import Path

# Add current directory to sys.path to import validate_csv
sys.path.append(str(Path(__file__).parent))
import validate_csv


def _merge_sidecar(rows, sidecar_path, key, fields):
    """按 key 把旁挂表（*_external.csv）字段并进主表行；旁挂表缺失时原样返回。

    与 workbench/server.py 的 merge_scholar_ext / merge_book_ext 保持同一口径，
    否则静态站点会比本地工作台少掉维基链接与生卒年。
    """
    if not sidecar_path.exists():
        return rows
    ext = {r.get(key): r for r in validate_csv.read_csv(sidecar_path) if r.get(key)}
    for row in rows:
        e = ext.get(row.get(key))
        if not e:
            continue
        for f in fields:
            if e.get(f):
                row[f] = e[f]
    return rows


def build():
    root = Path(__file__).parent.parent
    dist = root / 'dist'

    # Clean and recreate dist
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True, exist_ok=True)

    # 1. Copy Static Assets (HTML/CSS/JS)
    static_src = root / 'workbench' / 'static'
    static_dest = dist / 'static'
    static_dest.mkdir()

    for item in static_src.iterdir():
        if item.name == 'index.html':
            # Index goes to root
            shutil.copy2(item, dist / item.name)
        elif item.is_dir():
            # Subdirectories (if any) go to dist/static/subdir
            shutil.copytree(item, static_dest / item.name)
        else:
            # CSS/JS go to dist/static/
            shutil.copy2(item, static_dest / item.name)

    # 2. Build Database (JSON)
    csv_dir = root / 'data' / 'csv'
    quotes_path = csv_dir / 'quotes.csv'
    db = {
        'scholars': _merge_sidecar(validate_csv.read_csv(csv_dir / 'scholars.csv'),
                                   csv_dir / 'scholars_external.csv', 'scholar_id',
                                   ('birth_year', 'death_year', 'wikipedia_url')),
        'propositions': validate_csv.read_csv(csv_dir / 'propositions.csv'),
        'passages': validate_csv.read_csv(csv_dir / 'passages.csv'),
        'concepts': validate_csv.read_csv(csv_dir / 'concepts.csv'),
        'years': validate_csv.read_csv(csv_dir / 'years.csv'),
        'influences': validate_csv.read_csv(csv_dir / 'influences.csv'),
        'books': _merge_sidecar(validate_csv.read_csv(csv_dir / 'books.csv'),
                                csv_dir / 'books_external.csv', 'book_id',
                                ('wikipedia_url', 'found_year', 'year_status')),
        'relations': validate_csv.read_csv(csv_dir / 'relations.csv'),
        'quotes': validate_csv.read_csv(quotes_path) if quotes_path.exists() else [],
        'meta': {
            'build_time': __import__('datetime').date.today().isoformat(),
            'version': '1.0.0-static'
        }
    }

    # Ensure api directory exists
    (dist / 'api').mkdir()

    # Write db.json
    with open(dist / 'api' / 'db.json', 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    # Also write 'list' endpoint mock for static mode compatibility
    with open(dist / 'api' / 'list.json', 'w', encoding='utf-8') as f:
        json.dump({'files': [], 'static_mode': True}, f)

    # 3. Copy .well-known directory (site verification)
    well_known_src = root / '.well-known'
    if well_known_src.exists():
        shutil.copytree(well_known_src, dist / '.well-known')

    print(f"Build complete! Output directory: {dist}")
    print(f"  - Copied static assets")
    print(f"  - Generated api/db.json ({len(db['scholars'])} scholars, {len(db['propositions'])} propositions)")
    if well_known_src.exists():
        print(f"  - Copied .well-known verification directory")

if __name__ == "__main__":
    build()
