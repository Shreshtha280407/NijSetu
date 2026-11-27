"""Loopback HTTP adapter for the real engine. Run: python -m nijsetu.web.

Uploaded raw files are sealed as AES-256-GCM ciphertext (nijsetu.security)
the instant they arrive and are only ever decrypted into a verified
RAM-backed (tmpfs) directory for the duration of one run's processing --
never into ordinary persistent storage. The private analysis plan stays
in-process memory and is never written to disk at all. Only the validated
public release enters the downloadable archive.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import os
import secrets
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from .config import AppConfig
from .phase2.policy import POLICIES
from .phase2.sanitize import run_sanitize
from .phase2.validate import validate_release
from .plan.builder import run_pipeline
from .security import SecureStore, SecureStoreError, ram_backed_tempdir

MAX_BYTES = 32 * 1024 * 1024
MAX_BODY = 46 * 1024 * 1024
MAX_FILES = 500
RUN_TTL = 2 * 60 * 60
DEMO_DIR = Path(__file__).with_name('demo_data')


class RequestError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.status = status


def safe_path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise RequestError('INVALID_FILE_PATH')
    path = PurePosixPath(name)
    if path.is_absolute() or '\\' in name or ':' in name or '\x00' in name or any(p in ('', '.', '..') for p in name.split('/')):
        raise RequestError('INVALID_FILE_PATH')
    return path.as_posix()


def decode_files(entries: list) -> dict[str, bytes]:
    if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES:
        raise RequestError('SELECT_1_TO_500_FILES')
    files: dict[str, bytes] = {}
    total = 0

    def add(name: str, data: bytes):
        nonlocal total
        name = safe_path(name)
        if name in files:
            raise RequestError('DUPLICATE_FILE_PATH')
        total += len(data)
        if total > MAX_BYTES or len(files) >= MAX_FILES:
            raise RequestError('DATASET_LIMIT_32_MIB_OR_500_FILES', 413)
        files[name] = data

    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('content'), str):
            raise RequestError('INVALID_FILE_ENTRY')
        name = safe_path(entry.get('name'))
        try:
            data = base64.b64decode(entry['content'], validate=True)
        except (ValueError, binascii.Error):
            raise RequestError('INVALID_FILE_CONTENT') from None
        if len(data) > MAX_BYTES:
            raise RequestError('DATASET_LIMIT_32_MIB_OR_500_FILES', 413)
        if not name.lower().endswith('.zip'):
            add(name, data)
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > MAX_FILES * 2:
                    raise RequestError('DATASET_LIMIT_32_MIB_OR_500_FILES', 413)
                for member in members:
                    safe_path(member.filename.rstrip('/'))
                    if stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1:
                        raise RequestError('ZIP_LINKS_OR_ENCRYPTION_UNSUPPORTED')
                    if member.is_dir() or member.filename.startswith('__MACOSX/'):
                        continue
                    if total + member.file_size > MAX_BYTES:
                        raise RequestError('DATASET_LIMIT_32_MIB_OR_500_FILES', 413)
                    # Do not extract to disk: validate each path before writing ourselves.
                    add(member.filename, archive.read(member))
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError):
            raise RequestError('INVALID_OR_UNSUPPORTED_ZIP') from None
    if not files:
        raise RequestError('DATASET_EMPTY')
    return files


def _check_path_conflicts(names) -> None:
    """A later filesystem reconstruction (decrypting into a real directory
    for the engine to read) would fail if one uploaded name is itself a
    file ("a.txt") while another treats it as a directory ("a.txt/b.txt").
    Checked here, purely against the logical names in memory, so the
    request is rejected up front -- before anything is written anywhere,
    encrypted or not."""
    names_set = set(names)
    for name in names_set:
        parts = PurePosixPath(name).parts
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in names_set:
                raise RequestError('CONFLICTING_FILE_PATHS')


def policy_options() -> dict:
    return {name: {
        'label': name.title(),
        'description': 'Protect identity while preserving research utility.' if name == 'balanced' else 'Broader generalization for more sensitive releases.',
        'age': f'{p.age_band.band_width}-year bands',
        'dates': f'±{p.date_shift_max_days} days',
        'geography': 'Suppressed' if p.geography_level == 'none' else 'District retained',
        'cohort': str(p.k_threshold),
        'providers': 'Retained' if p.provider_name_action == 'RETAIN' else 'Suppressed',
        'facilities': 'Retained' if p.facility_name_action == 'RETAIN' else 'Suppressed',
    } for name, p in POLICIES.items()}


class WorkspaceService:
    def __init__(self):
        self.temp = TemporaryDirectory(prefix='nijsetu-web-')
        os.chmod(self.temp.name, 0o700)
        self.root = Path(self.temp.name)
        self.lock = threading.RLock()
        self.runs: dict[str, dict] = {}
        self.executor = ThreadPoolExecutor(max_workers=1)
        # HMAC release key (phase2.keys / run_sanitize's pseudonymization
        # domain) and the AES storage-encryption key below are deliberately
        # two independently-random keys from two separate cryptographic
        # domains -- see nijsetu.security's module docstring. Neither is
        # ever derived from the other.
        self.key = secrets.token_bytes(32)
        self.storage_key = secrets.token_bytes(32)
        self.vault = SecureStore(self.root / '_vault', self.storage_key)

    def close(self):
        self.executor.shutdown(wait=True)
        self.vault.close()
        self.temp.cleanup()

    def create(self, payload: dict) -> dict:
        policy = payload.get('policy', 'balanced')
        if not isinstance(policy, str) or policy not in POLICIES:
            raise RequestError('UNKNOWN_POLICY')
        demo = payload.get('demo') is True
        source = payload.get('source_system', '')
        if not isinstance(source, str) or len(source) > 120:
            raise RequestError('INVALID_SOURCE_SYSTEM')
        source = source.strip()
        files = {p.name: p.read_bytes() for p in DEMO_DIR.iterdir() if p.is_file()} if demo else decode_files(payload.get('files'))
        if demo and 'demo_files' in payload:
            names = payload['demo_files']
            if not isinstance(names, list) or not names or any(not isinstance(n, str) or n not in files for n in names):
                raise RequestError('INVALID_DEMO_SELECTION')
            files = {name: files[name] for name in names}
        with self.lock:
            now = time.monotonic()
            for run_id, run in list(self.runs.items()):
                if run['state'] in ('complete', 'failed') and now - run['_created'] > RUN_TTL:
                    shutil.rmtree(self.root / run_id)
                    del self.runs[run_id]
            if len(self.runs) >= 20:
                raise RequestError('WORKSPACE_FULL_RESTART_SERVER_OR_WAIT', 429)
            if sum(r['state'] not in ('complete', 'failed') for r in self.runs.values()) >= 2:
                raise RequestError('ENGINE_BUSY_TRY_AGAIN', 429)
            run_id = secrets.token_hex(16)
            (self.root / run_id).mkdir(mode=0o700)  # guarantees a cleanup target even if _process never reaches Staging()
            _check_path_conflicts(files.keys())
            # Raw uploaded bytes are never written to disk in plaintext: each
            # file is sealed into the vault (AES-256-GCM, logical name hashed
            # into a generic physical filename) immediately on arrival, so a
            # disk snapshot or a crash before processing starts never
            # discloses them. See _process for the matching decrypt step.
            try:
                for name, data in files.items():
                    self.vault.write_encrypted(f'{run_id}:{name}', data, purpose=b'upload')
            except (OSError, SecureStoreError):
                for name in files:
                    self.vault.delete(f'{run_id}:{name}')
                raise RequestError('UPLOAD_STORAGE_FAILURE') from None
            self.runs[run_id] = {
                'id': run_id, 'state': 'queued', 'progress': 0, 'stage': 'Queued for engine',
                'policy': policy, 'demo': demo, 'reviewed': False, 'file_count': len(files),
                '_created': now, '_file_names': list(files.keys()),
                '_upload_bytes': sum(len(d) for d in files.values()),
            }
            config = AppConfig(source_system_map=tuple((name, 'DEMO_HOSPITAL' if demo else source) for name in files) if demo or source else ())
            self.executor.submit(self._process, run_id, config)
            return self.get(run_id)

    def get(self, run_id: str) -> dict:
        with self.lock:
            if run_id not in self.runs:
                raise RequestError('RUN_NOT_FOUND_OR_EXPIRED', 404)
            return {k: v for k, v in self.runs[run_id].items() if not k.startswith('_')}

    def _update(self, run_id, **values):
        with self.lock:
            self.runs[run_id].update(values)

    def _process(self, run_id: str, config: AppConfig):
        root = self.root / run_id
        file_names = self.runs[run_id]['_file_names']
        decrypt_dir = None
        try:
            self._update(run_id, state='running', progress=15, stage='Parsing files, detecting identifiers and linking patients')
            # The ingest/detect/sanitize pipeline needs real filesystem
            # paths, but the decrypted bytes must never land on ordinary
            # persistent storage: staged into a directory verified to be
            # tmpfs (RAM-backed, so a disk snapshot/backup never captures
            # it), not merely assumed to be. No fallback to plain disk --
            # if no RAM-backed storage is available, the run fails closed
            # instead of silently writing plaintext to persistent storage.
            # Required space scales with THIS run's actual upload size
            # (headroom for the plaintext copy plus tmpdir/inode overhead),
            # not the dataset-wide upload ceiling -- a 1 KB upload must not
            # need the same tmpfs space as a full 32 MiB one.
            min_free = max(self.runs[run_id]['_upload_bytes'] * 2, 1 * 1024 * 1024)
            ram_root = ram_backed_tempdir(min_free_bytes=min_free)
            if ram_root is None:
                raise RequestError('RAM_BACKED_STORAGE_UNAVAILABLE')
            decrypt_dir = Path(tempfile.mkdtemp(prefix=f'nijsetu-run-{run_id}-', dir=str(ram_root)))
            os.chmod(decrypt_dir, 0o700)
            input_dir = decrypt_dir / 'input'
            input_dir.mkdir(mode=0o700)
            for name in file_names:
                data = self.vault.read_decrypted(f'{run_id}:{name}', purpose=b'upload')
                path = input_dir / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                os.chmod(path, 0o600)

            plan = run_pipeline(input_dir, config)
            records = []
            for r in plan.private['records']:
                names = [f['original_text'] for f in r['features'] if f['role'] == 'PATIENT_NAME']
                records.append({
                    'id': r['record_key'], 'name': names[0] if names else f"Record {r['record_key']}",
                    'file': r['file_path'], 'type': r['format'].upper(),
                    'status': r['link_status'], 'cluster': r['cluster_id'],
                    'reasons': r['reason_codes'],
                    'features': [{'role': f['role'], 'tier': f['confidence_tier'], 'rules': [e['rule_id'] for e in f['evidence']]} for f in r['features']],
                })
            self._update(run_id, progress=45, stage='Sanitizing with the selected privacy policy')
            outcome = run_sanitize(input_dir, root / 'release', POLICIES[self.runs[run_id]['policy']], run_id, self.key, config)
            self._update(run_id, progress=85, stage='Validating the published release')
            validation = validate_release(root / 'release') if outcome.output_dir else None
            holds = json.loads((root / 'release' / 'hold_index.json').read_text()) if outcome.output_dir else []
            checks = [
                {'name': 'source_identifier_leak_scan', 'passed': outcome.report.get('leak_scan', {}).get('clean') is True, 'detail': 'Source-aware scan performed during sanitization.'},
                {'name': 'source_clinical_invariants', 'passed': outcome.output_dir is not None, 'detail': 'Publication requires the engine’s source-to-output clinical checks.'},
            ] + (validation.checks if validation else [])
            ready = bool(outcome.output_dir and validation and validation.passed)
            result = {
                'analysis': plan.safe_report, 'records': records,
                'files': plan.private['file_records'], 'ingest_holds': plan.private['held_records'],
                'report': outcome.report, 'holds': holds, 'checks': checks,
                'validation_scope': validation.scope_note if validation else '',
                'release_ready': ready,
            }
            self._update(run_id, state='complete', progress=100, stage='Engine run complete', result=result)
        except Exception:
            # Patient content and keys must never appear in errors or access logs.
            self._update(run_id, state='failed', progress=100, stage='Processing failed', error='ENGINE_PROCESSING_FAILED')
        finally:
            if decrypt_dir is not None:
                shutil.rmtree(decrypt_dir, ignore_errors=True)
            for name in file_names:
                self.vault.delete(f'{run_id}:{name}')

    def review(self, run_id: str) -> dict:
        with self.lock:
            run = self.get(run_id)
            if run['state'] != 'complete':
                raise RequestError('RUN_NOT_COMPLETE', 409)
            self.runs[run_id]['reviewed'] = True
            return self.get(run_id)

    def download(self, run_id: str) -> bytes:
        with self.lock:
            run = self.get(run_id)
            if not run.get('reviewed') or not run.get('result', {}).get('release_ready'):
                raise RequestError('RELEASE_REQUIRES_VALIDATION_AND_REVIEW', 409)
            release = self.root / run_id / 'release'
            if not validate_release(release).passed:
                raise RequestError('RELEASE_VALIDATION_FAILED', 409)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(release.rglob('*')):
                    if path.is_file():
                        archive.write(path, path.relative_to(release).as_posix())
            return buffer.getvalue()


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service: WorkspaceService, static_dir: Path):
        self.service = service
        self.static_dir = static_dir.resolve()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: WebServer

    def log_message(self, *_args):
        pass

    def _send(self, status, data, content_type='application/json', filename=None):
        if content_type == 'application/json':
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, post=False):
        try:
            # Prevent DNS rebinding and cross-origin mutation of the loopback service.
            host = self.headers.get('Host', '')
            if urlsplit('http://' + host).hostname not in ('localhost', '127.0.0.1'):
                raise RequestError('LOCAL_HOST_REQUIRED', 403)
            origin = self.headers.get('Origin')
            if origin and (urlsplit(origin).hostname not in ('localhost', '127.0.0.1') or urlsplit(origin).scheme != 'http'):
                raise RequestError('LOCAL_ORIGIN_REQUIRED', 403)
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise RequestError('LOCAL_ORIGIN_REQUIRED', 403)
            path = urlsplit(self.path).path
            payload = {}
            if post:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise RequestError('JSON_REQUIRED', 415)
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise RequestError('INVALID_CONTENT_LENGTH') from None
                if size <= 0 or size > MAX_BODY:
                    raise RequestError('REQUEST_LIMIT_46_MIB', 413)
                self.connection.settimeout(30)
                try:
                    payload = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeError):
                    raise RequestError('INVALID_JSON') from None
                if not isinstance(payload, dict):
                    raise RequestError('JSON_OBJECT_REQUIRED')
            service = self.server.service
            if path == '/api/health' and not post:
                return self._send(200, {'status': 'ok', 'engine': 'nijsetu'})
            if path == '/api/options' and not post:
                return self._send(200, {
                    'policies': policy_options(), 'max_bytes': MAX_BYTES, 'max_files': MAX_FILES,
                    'demo_files': [{'id': p.name, 'name': p.name, 'size': p.stat().st_size, 'format': p.suffix[1:].upper(), 'supported': p.suffix != '.pdf'} for p in sorted(DEMO_DIR.iterdir()) if p.is_file()],
                })
            if path == '/api/runs' and post:
                return self._send(202, service.create(payload))
            parts = path.strip('/').split('/')
            if len(parts) in (3, 4) and parts[:2] == ['api', 'runs']:
                run_id = parts[2]
                if len(parts) == 3 and not post:
                    return self._send(200, service.get(run_id))
                if len(parts) == 4 and parts[3] == 'review' and post:
                    if payload.get('acknowledged') is not True:
                        raise RequestError('REVIEW_ACKNOWLEDGEMENT_REQUIRED')
                    return self._send(200, service.review(run_id))
                if len(parts) == 4 and parts[3] == 'download' and not post:
                    return self._send(200, service.download(run_id), 'application/zip', f'nijsetu-{run_id}.zip')
            if path.startswith('/api/') or post:
                raise RequestError('NOT_FOUND', 404)
            import mimetypes
            from urllib.parse import unquote
            target = (self.server.static_dir / unquote(path).lstrip('/')).resolve()
            if not target.is_relative_to(self.server.static_dir):
                raise RequestError('NOT_FOUND', 404)
            if not target.is_file():
                target = self.server.static_dir / 'index.html'
            if not target.is_file():
                raise RequestError('BUILD_FRONTEND_OR_START_VITE', 404)
            return self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        except RequestError as exc:
            self._send(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._send(500, {'error': 'REQUEST_FAILED'})

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch(post=True)


def main():
    parser = argparse.ArgumentParser(description='NijSetu local engine API and dashboard')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--static-dir', type=Path, default=Path('frontend/dist'))
    args = parser.parse_args()
    service = WorkspaceService()
    server = WebServer(('127.0.0.1', args.port), service, args.static_dir)
    print(f'NijSetu dashboard and API: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == '__main__':
    main()
