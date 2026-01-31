from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {module_name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


ROOT = repo_root()

# Load config
CONFIG_PATH = ROOT / 'config.json'
CONFIG = {}
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            CONFIG = json.load(f)
    except Exception:
        pass

PATHS = CONFIG.get('paths', {})
DATA_CSV_DIR = ROOT / PATHS.get('csv_dir', 'data/csv')
DATA_VALIDATED_DIR = ROOT / 'data' / 'validated'
if 'rdf_output' in PATHS:
     DATA_VALIDATED_DIR = (ROOT / PATHS['rdf_output']).parent

STATIC_DIR = ROOT / 'workbench' / 'static'

VALIDATE_MOD = load_module('validate_csv', ROOT / 'scripts' / 'validate_csv.py')
RDF_MOD = load_module('csv_to_rdf', ROOT / 'scripts' / 'csv_to_rdf.py') if (ROOT / 'scripts' / 'csv_to_rdf.py').exists() else None


def json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')


def safe_inside_root(candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(ROOT.resolve())
        return True
    except Exception:
        return False


def list_csv_files():
    files = []
    for name in sorted(p.name for p in DATA_CSV_DIR.glob('*.csv')):
        p = DATA_CSV_DIR / name
        try:
            size = p.stat().st_size
        except FileNotFoundError:
            continue
        files.append({'name': name, 'bytes': size})
    return files


FRIENDLY = {
    'E1001': '命题缺少证据段落 (Proposition 必须至少绑定一个 Passage)',
    'E1002': '影响关系缺少证据段落 (influencedBy 必须绑定 Passage)',
    'E1003': '证据段落缺少中文文本 (Passage 必须包含中文)',
    'E2001': '外键引用不存在 (引用了不存在的 *_id)',
    'E3001': 'CSV 为空或无法读取',
    'E3002': 'CSV 缺少必需列',
    'E3003': 'CSV 某行缺少必填字段',
    'E3004': '年份格式不正确 (应为 YYYY)',
    'E3005': '类型字段不正确 (应为 Scholar 或 Proposition)',
    'W4001': 'year 不在 years.csv 中 (可选提示)',
    'W4002': 'concept_id 不存在 (可选提示)',
}


class Handler(BaseHTTPRequestHandler):
    server_version = 'news-journalism-kg-workbench/0.1'

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            return

    def _send_json(self, code: int, payload) -> None:
        self._send(code, 'application/json; charset=utf-8', json_bytes(payload))

    def _read_json(self):
        length = int(self.headers.get('Content-Length', '0') or '0')
        raw = self.rfile.read(length) if length else b''
        if not raw:
            return {}
        return json.loads(raw.decode('utf-8'))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == '/':
            body = (STATIC_DIR / 'index.html').read_bytes()
            self._send(200, 'text/html; charset=utf-8', body)
            return

        if parsed.path.startswith('/static/'):
            rel = parsed.path.removeprefix('/static/')
            f = (STATIC_DIR / rel)
            if not f.exists():
                self._send(404, 'text/plain; charset=utf-8', b'Not Found')
                return
            if rel.endswith('.css'):
                ct = 'text/css; charset=utf-8'
            elif rel.endswith('.js'):
                ct = 'application/javascript; charset=utf-8'
            else:
                ct = 'application/octet-stream'
            self._send(200, ct, f.read_bytes())
            return

        if parsed.path == '/api/status':
            self._send_json(200, {
                'ok': True,
                'root': str(ROOT),
                'csv_dir': str(DATA_CSV_DIR),
                'validated_dir': str(DATA_VALIDATED_DIR),
                'rdf_export_enabled': (ROOT / 'scripts' / 'csv_to_rdf.py').exists(),
            })
            return

        if parsed.path == '/api/list':
            self._send_json(200, {'csv_dir': 'data/csv', 'files': list_csv_files()})
            return

        if parsed.path == '/api/csv':
            qs = parse_qs(parsed.query)
            name = (qs.get('name', [''])[0] or '').strip()
            if not name or '/' in name or '\\' in name or not name.endswith('.csv'):
                self._send_json(400, {'error': 'invalid name'})
                return
            p = (DATA_CSV_DIR / name)
            if not p.exists():
                self._send_json(404, {'error': 'not found'})
                return
            self._send_json(200, {'name': name, 'content': p.read_text(encoding='utf-8-sig')})
            return

        if parsed.path == '/api/search':
            qs = parse_qs(parsed.query)
            q = (qs.get('q', [''])[0] or '').strip()
            if not q:
                self._send_json(200, {'q': q, 'scholars': [], 'passages': [], 'propositions': []})
                return
            scholars = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'scholars.csv')
            passages = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'passages.csv')
            props = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'propositions.csv')
            ql = q.lower()
            scholar_hits = []
            for r in scholars:
                name_zh = r.get('name_zh', '')
                name_en = r.get('name_en', '')
                school = r.get('school', '')
                if q in name_zh or ql in name_en.lower() or ql in school.lower():
                    scholar_hits.append({
                        'scholar_id': r.get('scholar_id', ''),
                        'name_zh': name_zh,
                        'name_en': name_en,
                        'school': school,
                    })
                    if len(scholar_hits) >= 10:
                        break
            pass_hits = []
            for r in passages:
                text = r.get('passage_text', '')
                if q in text or ql in text.lower():
                    pass_hits.append({
                        'passage_id': r.get('passage_id', ''),
                        'source_title': r.get('source_title', ''),
                        'source_url': r.get('source_url', ''),
                        'passage_text': text,
                    })
                    if len(pass_hits) >= 20:
                        break
            prop_hits = []
            for r in props:
                text = r.get('proposition_text_zh', '')
                if q in text or ql in text.lower():
                    prop_hits.append({
                        'proposition_id': r.get('proposition_id', ''),
                        'scholar_id': r.get('scholar_id', ''),
                        'year': r.get('year', ''),
                        'proposition_text_zh': text,
                        'evidence_passage_ids': r.get('evidence_passage_ids', ''),
                    })
                    if len(prop_hits) >= 20:
                        break
            self._send_json(200, {'q': q, 'scholars': scholar_hits, 'passages': pass_hits, 'propositions': prop_hits})
            return

        if parsed.path == '/download/knowledge.ttl':
            p = DATA_VALIDATED_DIR / 'knowledge.ttl'
            if not p.exists():
                self._send(404, 'text/plain; charset=utf-8', b'Not Found')
                return
            self._send(200, 'text/turtle; charset=utf-8', p.read_bytes())
            return

        if parsed.path == '/api/browse':
            scholars = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'scholars.csv')
            props = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'propositions.csv')
            passages = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'passages.csv')
            books = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'books.csv')
            relations = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'relations.csv')
            influences = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'influences.csv')
            self._send_json(200, {
                'scholars': scholars,
                'propositions': props,
                'passages': passages,
                'books': books,
                'relations': relations,
                'influences': influences,
            })
            return

        self._send(404, 'text/plain; charset=utf-8', b'Not Found')

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == '/api/csv':
            payload = self._read_json()
            name = (payload.get('name') or '').strip()
            content = payload.get('content') or ''
            if not name or '/' in name or '\\' in name or not name.endswith('.csv'):
                self._send_json(400, {'error': 'invalid name'})
                return
            p = (DATA_CSV_DIR / name)
            if not safe_inside_root(p):
                self._send_json(400, {'error': 'invalid path'})
                return
            p.write_text(content.replace('\r\n', '\n'), encoding='utf-8')
            self._send_json(200, {'ok': True, 'name': name, 'bytes': p.stat().st_size})
            return

        if parsed.path == '/api/validate':
            issues = VALIDATE_MOD.validate(DATA_CSV_DIR)
            errors = [i.to_dict() for i in issues if i.level == 'error']
            warnings = [i.to_dict() for i in issues if i.level == 'warning']

            def decorate(items):
                for it in items:
                    it['friendly'] = FRIENDLY.get(it.get('code', ''), '')
                return items

            self._send_json(200 if not errors else 400, {
                'errors': decorate(errors),
                'warnings': decorate(warnings),
                'summary': {'errors': len(errors), 'warnings': len(warnings)},
            })
            return

        if parsed.path == '/api/export/rdf':
            if RDF_MOD is None:
                self._send_json(400, {'error': 'RDF exporter not installed'})
                return

            issues = VALIDATE_MOD.validate(DATA_CSV_DIR)
            errors = [i.to_dict() for i in issues if i.level == 'error']
            warnings = [i.to_dict() for i in issues if i.level == 'warning']
            if errors:
                self._send_json(400, {'errors': errors, 'warnings': warnings, 'summary': {'errors': len(errors), 'warnings': len(warnings)}})
                return

            DATA_VALIDATED_DIR.mkdir(parents=True, exist_ok=True)
            ttl = RDF_MOD.generate_ttl(DATA_CSV_DIR)
            out = DATA_VALIDATED_DIR / 'knowledge.ttl'
            out.write_text(ttl, encoding='utf-8')

            self._send_json(200, {
                'ok': True,
                'saved_to': 'data/validated/knowledge.ttl',
                'download': '/download/knowledge.ttl',
                'warnings': warnings,
            })
            return

        self._send(404, 'text/plain; charset=utf-8', b'Not Found')


def main() -> int:
    parser = argparse.ArgumentParser(description='News Journalism KG Workbench (local web app).')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8125)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        print(f'Listening on http://{args.host}:{args.port} (Ctrl+C to stop)')
    except Exception:
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
