import json
import shutil
import sys
from pathlib import Path

# Add current directory to sys.path to import validate_csv
sys.path.append(str(Path(__file__).parent))
import validate_csv

def build():
    root = Path(__file__).parent.parent
    dist = root / 'dist'

    # Clean and recreate dist
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir()

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
    db = {
        'scholars': validate_csv.read_csv(csv_dir / 'scholars.csv'),
        'propositions': validate_csv.read_csv(csv_dir / 'propositions.csv'),
        'passages': validate_csv.read_csv(csv_dir / 'passages.csv'),
        'concepts': validate_csv.read_csv(csv_dir / 'concepts.csv'),
        'years': validate_csv.read_csv(csv_dir / 'years.csv'),
        'influences': validate_csv.read_csv(csv_dir / 'influences.csv'),
        'books': validate_csv.read_csv(csv_dir / 'books.csv'),
        'relations': validate_csv.read_csv(csv_dir / 'relations.csv'),
        'meta': {
            'build_time': '2026-01-31',
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

    print(f"Build complete! Output directory: {dist}")
    print(f"  - Copied static assets")
    print(f"  - Generated api/db.json ({len(db['scholars'])} scholars, {len(db['propositions'])} propositions)")

if __name__ == "__main__":
    build()
