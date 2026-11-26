from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import re

# Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) are not
# usable as file names regardless of extension
_RESERVED_NAME_RE = re.compile(
    r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$', re.IGNORECASE
)

# Characters Windows refuses (or that break path handling): control chars and
# the reserved set. Rejecting them here turns a 500 from open() into a 400.
_UNSAFE_NAME_CHARS_RE = re.compile(r'[\x00-\x1f<>:"|?*]')


def is_valid_csv_name(name: str) -> bool:
    if not name or '/' in name or '\\' in name or not name.endswith('.csv'):
        return False
    if _UNSAFE_NAME_CHARS_RE.search(name):
        return False
    stem = name[:-len('.csv')]
    if _RESERVED_NAME_RE.match(stem):
        return False
    return True


def _local_hostname(value: str) -> str | None:
    """Extract hostname from an Origin URL or Host header; None if unparseable."""
    value = (value or '').strip()
    if not value:
        return None
    if '://' in value:
        try:
            host = urlparse(value).hostname
        except ValueError:  # e.g. "http://[::1" -> Invalid IPv6 URL
            return None
        return host.lower() if host else None
    # Host header: hostname[:port] or [ipv6]:port
    if value.startswith('['):
        end = value.find(']')
        if end <= 1:
            return None
        return value[1:end].lower()
    return value.split(':', 1)[0].lower()


_LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def is_local_origin_or_host(origin: str, host: str) -> bool:
    """True when Host is loopback and Origin, if sent, is loopback too.

    Host must always be loopback: a page on another site still sends
    `Host: 127.0.0.1`, so deciding on Host alone lets it through, while a
    DNS-rebinding domain sends its own Host name and is rejected here.
    """
    if _local_hostname(host) not in _LOOPBACK_HOSTS:
        return False
    if not (origin or '').strip():
        return True  # non-browser clients (curl, scripts) omit Origin
    return _local_hostname(origin) in _LOOPBACK_HOSTS


def content_type_is_json(content_type: str) -> bool:
    media = (content_type or '').split(';', 1)[0].strip().lower()
    return media == 'application/json'


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

# 学者外部补全旁挂表（scripts/enrich_scholars.py 产物；缺失时为空，API 优雅降级）
def load_scholar_ext() -> dict:
    path = DATA_CSV_DIR / 'scholars_external.csv'
    if not path.exists():
        return {}
    import csv as _csv
    with open(path, encoding='utf-8-sig', newline='') as f:
        return {r.get('scholar_id'): r for r in _csv.DictReader(f) if r.get('scholar_id')}


def merge_scholar_ext(scholar: dict, ext_map: dict | None = None) -> dict:
    ext_map = ext_map if ext_map is not None else load_scholar_ext()
    e = ext_map.get(scholar.get('scholar_id'))
    if e:
        for k in ('birth_year', 'death_year', 'wikipedia_url'):
            if e.get(k):
                scholar[k] = e[k]
    return scholar


def load_book_ext() -> dict:
    path = DATA_CSV_DIR / 'books_external.csv'
    if not path.exists():
        return {}
    import csv as _csv
    with open(path, encoding='utf-8-sig', newline='') as f:
        return {r.get('book_id'): r for r in _csv.DictReader(f) if r.get('book_id')}


def merge_book_ext(book: dict, ext_map: dict | None = None) -> dict:
    ext_map = ext_map if ext_map is not None else load_book_ext()
    e = ext_map.get(book.get('book_id'))
    if e:
        for k in ('wikipedia_url', 'found_year', 'year_status'):
            if e.get(k):
                book[k] = e[k]
    return book


def json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')


def safe_inside_root(candidate: Path) -> bool:
    """True only when the resolved path stays inside the CSV directory.

    Resolving first matters: a symlink placed in data/csv would otherwise pass a
    plain join check and let /api/csv read or overwrite files elsewhere.
    """
    try:
        candidate.resolve().relative_to(DATA_CSV_DIR.resolve())
        return True
    except (OSError, ValueError):
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
        """Return (ok, payload). ok is False when the body is not valid JSON."""
        try:
            length = int(self.headers.get('Content-Length', '0') or '0')
        except ValueError:
            return False, None
        if length <= 0:
            return True, {}
        if length > 20 * 1024 * 1024:
            return False, None
        raw = self.rfile.read(length)
        if not raw:
            return True, {}
        try:
            return True, json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return False, None

    def do_GET(self) -> None:
        try:
            self._handle_get()
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._send(500, 'text/plain; charset=utf-8', b'Internal Server Error')
            except Exception:
                pass

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == '/':
            try:
                body = (STATIC_DIR / 'index.html').read_bytes()
            except FileNotFoundError:
                self._send(500, 'text/plain; charset=utf-8', b'index.html not found')
                return
            self._send(200, 'text/html; charset=utf-8', body)
            return

        if parsed.path.startswith('/static/'):
            rel = unquote(parsed.path.removeprefix('/static/'))
            f = (STATIC_DIR / rel)
            try:
                # Only serve files that live inside the static directory
                f.resolve().relative_to(STATIC_DIR.resolve())
            except Exception:
                self._send(403, 'text/plain; charset=utf-8', b'Forbidden')
                return
            if not f.is_file():
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
            name = unquote((qs.get('name', [''])[0] or '')).strip()
            if not is_valid_csv_name(name):
                self._send_json(400, {'error': 'invalid name'})
                return
            p = (DATA_CSV_DIR / name)
            if not safe_inside_root(p) or not p.is_file():
                self._send_json(404, {'error': 'not found'})
                return
            try:
                content = p.read_text(encoding='utf-8-sig')
            except UnicodeDecodeError:
                self._send_json(422, {
                    'error': f'{name} 不是 UTF-8 编码，工作台无法编辑（请先转为 UTF-8）',
                })
                return
            self._send_json(200, {'name': name, 'content': content})
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
            # Load each sidecar once: merge_* called without a map re-reads the
            # whole CSV on every row (~160 scholars / ~60 books per request).
            scholar_ext = load_scholar_ext()
            book_ext = load_book_ext()
            scholars = [merge_scholar_ext(s, scholar_ext) for s in VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'scholars.csv')]
            props = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'propositions.csv')
            passages = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'passages.csv')
            concepts = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'concepts.csv')
            books = [merge_book_ext(b, book_ext) for b in VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'books.csv')]
            relations = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'relations.csv')
            influences = VALIDATE_MOD.read_csv(DATA_CSV_DIR / 'influences.csv')
            quotes_path = DATA_CSV_DIR / 'quotes.csv'
            quotes = VALIDATE_MOD.read_csv(quotes_path) if quotes_path.exists() else []
            self._send_json(200, {
                'scholars': scholars,
                'propositions': props,
                'passages': passages,
                'concepts': concepts,
                'books': books,
                'relations': relations,
                'influences': influences,
                'quotes': quotes,
            })
            return

        self._send(404, 'text/plain; charset=utf-8', b'Not Found')

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._send(500, 'text/plain; charset=utf-8', b'Internal Server Error')
            except Exception:
                pass

    def _handle_post(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == '/api/csv':
            if not content_type_is_json(self.headers.get('Content-Type', '')):
                self._send_json(415, {'error': 'Content-Type must be application/json'})
                return
            if not is_local_origin_or_host(
                self.headers.get('Origin', ''),
                self.headers.get('Host', ''),
            ):
                self._send_json(403, {'error': 'forbidden: local Origin or Host required'})
                return
            ok, payload = self._read_json()
            if not ok or not isinstance(payload, dict):
                self._send_json(400, {'error': 'invalid JSON body'})
                return
            name = payload.get('name')
            content = payload.get('content')
            if not isinstance(name, str) or not isinstance(content, str):
                self._send_json(400, {'error': 'name and content must be strings'})
                return
            name = name.strip()
            if not is_valid_csv_name(name):
                self._send_json(400, {'error': 'invalid name'})
                return
            p = (DATA_CSV_DIR / name)
            if not safe_inside_root(p):
                self._send_json(400, {'error': 'invalid path'})
                return
            # Encode before touching the file: write_text() truncates first and
            # would leave an empty CSV behind if encoding failed (lone surrogate).
            try:
                data = content.replace('\r\n', '\n').encode('utf-8')
            except UnicodeEncodeError:
                self._send_json(400, {'error': 'content is not valid Unicode'})
                return
            tmp = p.with_name(f'.{p.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            try:
                tmp.write_bytes(data)
                os.replace(tmp, p)
            except OSError as e:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                self._send_json(500, {'error': f'write failed: {e.strerror or e}'})
                return
            self._send_json(200, {'ok': True, 'name': name, 'bytes': p.stat().st_size})
            return

        if parsed.path == '/api/validate':
            try:
                issues = VALIDATE_MOD.validate(DATA_CSV_DIR)
            except FileNotFoundError as e:
                self._send_json(500, {'error': f'缺少数据文件: {e.filename}'})
                return
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

            try:
                issues = VALIDATE_MOD.validate(DATA_CSV_DIR)
            except FileNotFoundError as e:
                self._send_json(400, {'error': f'缺少数据文件: {e.filename}'})
                return
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

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        print(f'无法启动服务器: {e}')
        print(f'端口 {args.port} 可能已被占用（或已有一个工作台实例在运行）。')
        print('尝试使用其他端口: python workbench/server.py --port 8126')
        return 1

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
