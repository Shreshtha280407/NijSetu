"""Integration checks for browser uploads, engine execution and release gates."""
import base64
import io
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from nijsetu.phase2.validate import validate_release
from nijsetu.web import RequestError, WebServer, WorkspaceService, decode_files


def upload(name, data):
    return {'name': name, 'content': base64.b64encode(data).decode()}


def completed(service, payload):
    run = service.create(payload)
    deadline = time.monotonic() + 15
    while run['state'] not in ('complete', 'failed') and time.monotonic() < deadline:
        time.sleep(.01)
        run = service.get(run['id'])
    assert run['state'] == 'complete', run
    return run


@pytest.fixture
def service():
    s = WorkspaceService()
    yield s
    s.close()


@pytest.mark.parametrize('policy', ['balanced', 'strict'])
def test_demo_uses_engine_and_download_is_validated_public_release(service, tmp_path, policy):
    run = completed(service, {'demo': True, 'policy': policy})
    result = run['result']
    assert result['analysis']['counts']['documents_processed'] == 4
    assert result['analysis']['counts']['patients_accepted_clusters'] == 1
    assert any(r['status'] == 'HELD' for r in result['records'])
    assert result['report']['policy']['name'] == policy
    assert result['report']['hold_summary']['UNSUPPORTED_EXTENSION'] == 1
    assert result['release_ready']
    with pytest.raises(RequestError, match='REQUIRES_VALIDATION_AND_REVIEW'):
        service.download(run['id'])
    service.review(run['id'])
    archive = zipfile.ZipFile(io.BytesIO(service.download(run['id'])))
    assert {'report.json', 'hold_index.json', 'manifest.json'} <= set(archive.namelist())
    assert not any('private' in n or 'input' in n for n in archive.namelist())
    released = b''.join(archive.read(n) for n in archive.namelist() if n.startswith('released/'))
    for original in [b'Mohammad', b'Arif', b'AR001', b'9876543210']:
        assert original not in released
    assert b'140/90' in released and b'8.2%' in released
    archive.extractall(tmp_path)
    assert validate_release(tmp_path).passed
    # Revalidate on each download, rather than trusting stale frontend state.
    (service.root / run['id'] / 'release' / 'report.json').write_text('{}')
    with pytest.raises(RequestError, match='RELEASE_VALIDATION_FAILED'):
        service.download(run['id'])


def test_upload_data_drives_records_and_results(service):
    files = [upload('hospital/registration.csv', b'Patient Name,UHID,BP\nMeera Sharma,JUDGE001,120/80'),
             upload('hospital/visit.json', b'{"patientName":"Meera Sharma","uhid":"JUDGE001","hba1c":"6.4%"}')]
    run = completed(service, {'files': files, 'source_system': 'JUDGE_HOSPITAL'})
    result = run['result']
    assert len(result['records']) == 2
    assert all(r['name'] == 'Meera Sharma' and r['status'] == 'ACCEPTED' for r in result['records'])
    assert result['report']['counts']['records_released'] == 2
    assert result['release_ready']
    assert not (service.root / run['id'] / 'input').exists()
    service.review(run['id'])
    archive = zipfile.ZipFile(io.BytesIO(service.download(run['id'])))
    released = b''.join(archive.read(n) for n in archive.namelist() if n.startswith('released/'))
    assert b'120/80' in released and b'6.4%' in released
    assert b'Meera' not in released and b'JUDGE001' not in released


@pytest.mark.parametrize('name,data', [('broken.json', b'{bad'), ('scan.pdf', b'unsupported')])
def test_blocked_or_unsupported_batch_cannot_download_even_after_review(service, name, data):
    run = completed(service, {'files': [upload(name, data)]})
    assert run['result']['report']['status'] == 'BLOCKED'
    assert not run['result']['release_ready']
    service.review(run['id'])
    with pytest.raises(RequestError):
        service.download(run['id'])


def test_demo_file_removal_is_honored(service):
    run = completed(service, {'demo': True, 'demo_files': ['registration.csv']})
    assert run['file_count'] == 1
    assert len(run['result']['records']) == 1


def zip_upload(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return upload('dataset.zip', buf.getvalue())


def test_zip_dataset_runs_real_engine(service):
    run = completed(service, {'files': [zip_upload([('folder/patients.tsv', 'Patient Name\tUHID\tBP\nMeera Sharma\tZIP001\t120/80')])]})
    assert run['file_count'] == 1
    assert run['result']['records'][0]['file'] == 'folder/patients.tsv'
    assert run['result']['release_ready']


@pytest.mark.parametrize('name', ['../escape.csv', '/absolute.csv', 'folder/../../escape.txt', 'C:/file.txt', 'a\\b.txt'])
def test_unsafe_upload_and_zip_paths_are_rejected(name):
    for entry in [upload(name, b'data'), zip_upload([(name, 'data')])]:
        with pytest.raises(RequestError, match='INVALID_FILE_PATH'):
            decode_files([entry])


def test_duplicate_paths_invalid_zip_and_base64_are_rejected():
    with pytest.raises(RequestError, match='DUPLICATE_FILE_PATH'):
        decode_files([upload('same.txt', b'a'), upload('same.txt', b'b')])
    with pytest.raises(RequestError, match='INVALID_OR_UNSUPPORTED_ZIP'):
        decode_files([upload('broken.zip', b'not zip')])
    with pytest.raises(RequestError, match='INVALID_FILE_CONTENT'):
        decode_files([{'name': 'file.txt', 'content': '%%%'}])
    with pytest.raises(RequestError, match='CONFLICTING_FILE_PATHS'):
        s = WorkspaceService()
        try:
            s.create({'files': [upload('a.txt', b'a'), upload('a.txt/child.txt', b'b')]})
        finally:
            s.close()


def test_zip_limits_and_symlinks(monkeypatch):
    monkeypatch.setattr('nijsetu.web.MAX_BYTES', 50)
    with pytest.raises(RequestError, match='DATASET_LIMIT'):
        decode_files([upload('too-big.txt', b'x' * 51)])
    monkeypatch.setattr('nijsetu.web.MAX_BYTES', 1024 * 1024)
    buf = io.BytesIO()
    info = zipfile.ZipInfo('link.txt')
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr(info, '/etc/passwd')
    with pytest.raises(RequestError, match='ZIP_LINKS'):
        decode_files([upload('links.zip', buf.getvalue())])


def test_http_contract_and_local_origin_protection(service, tmp_path):
    (tmp_path / 'index.html').write_text('<html>dashboard</html>')
    server = WebServer(('127.0.0.1', 0), service, tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def request(path, data=None, **headers):
        body = None if data is None else json.dumps(data).encode()
        return urllib.request.urlopen(urllib.request.Request(base + path, data=body, headers={'Content-Type': 'application/json', **headers}))
    try:
        assert json.load(request('/api/health'))['engine'] == 'nijsetu'
        options = json.load(request('/api/options'))
        assert options['policies']['strict']['dates'] == '±45 days'
        assert len(options['demo_files']) == 5
        assert b'dashboard' in request('/dashboard/intake').read()
        with pytest.raises(urllib.error.HTTPError) as error:
            request('/api/runs', {'demo': True}, Origin='https://untrusted.example')
        assert error.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as error:
            request('/api/health', Host='untrusted.example')
        assert error.value.code == 403
        run = json.load(request('/api/runs', {'demo': True}))
        deadline = time.monotonic() + 15
        while run['state'] not in ('complete', 'failed') and time.monotonic() < deadline:
            time.sleep(.01)
            run = json.load(request('/api/runs/' + run['id']))
        with pytest.raises(urllib.error.HTTPError) as error:
            request(f"/api/runs/{run['id']}/download")
        assert error.value.code == 409
        with pytest.raises(urllib.error.HTTPError) as error:
            request(f"/api/runs/{run['id']}/review", {})
        assert error.value.code == 400
        json.load(request(f"/api/runs/{run['id']}/review", {'acknowledged': True}))
        assert request(f"/api/runs/{run['id']}/download").read().startswith(b'PK')
        with pytest.raises(urllib.error.HTTPError) as error:
            request('/api/runs/unknown')
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# --- Gate 0.6: encryption-at-rest for dashboard uploads ---------------------

_CANARY_MARKERS = {
    'name': b'Fictitious Canary Patientname',
    'aadhaar': b'234567890123',
    'uhid': b'UHCANARY999PII',
    'phone': b'9123456789',
    'address': b'221B Canary Baker Street Lucknow',
}
_CANARY_FILENAME = 'canary_patientname_record.csv'


def _canary_csv() -> bytes:
    return (
        b'Patient Name,UHID,Phone,Address,Aadhaar\n'
        + b','.join([_CANARY_MARKERS['name'], _CANARY_MARKERS['uhid'], _CANARY_MARKERS['phone'],
                     _CANARY_MARKERS['address'], _CANARY_MARKERS['aadhaar']])
        + b'\n'
    )


def test_raw_pii_never_touches_disk_as_plaintext(service):
    """While an upload is sealed in the vault and while it is being
    processed, every file actually on disk under the service's workspace
    must be inspected and found free of the raw PII bytes and of the
    uploaded filename -- content AND path/metadata, per the Gate 0.6
    confidentiality requirement."""
    run = service.create({'files': [upload(_CANARY_FILENAME, _canary_csv())]})

    def scan_disk():
        for p in service.root.rglob('*'):
            if not p.is_file():
                continue
            assert _CANARY_FILENAME.split('.')[0] not in p.name, f"filename leaked in path {p}"
            raw = p.read_bytes()
            for label, needle in _CANARY_MARKERS.items():
                assert needle not in raw, f"{label} leaked into {p}"
            assert b'canary_patientname_record' not in raw

    scan_disk()  # immediately after create(): upload must already be ciphertext
    deadline = time.monotonic() + 15
    state = run['state']
    while state not in ('complete', 'failed') and time.monotonic() < deadline:
        scan_disk()  # during processing: decrypted copy must live only in tmpfs, never under service.root
        time.sleep(.01)
        state = service.get(run['id'])['state']
    assert state == 'complete'
    scan_disk()  # after completion: vault blob for this run must be gone, nothing plaintext remains


def test_vault_key_is_independent_of_hmac_release_key(service):
    assert service.storage_key != service.key


@pytest.mark.skipif(sys.platform != 'linux', reason="relies on /proc/mounts and SIGKILL semantics")
def test_sigkill_during_processing_leaves_only_ciphertext_behind(tmp_path: Path):
    """Gate 0.6 Threat B, demonstrated rather than claimed: start a real
    WorkspaceService in a subprocess, hand it a canary upload, SIGKILL the
    process mid-run (no clean shutdown, no close()), then inspect whatever
    the OS left on disk. The orphaned vault ciphertext is expected to
    survive (nothing here claims SIGKILL triggers cleanup) -- what matters
    is that it is never the raw PII in readable form."""
    script = tmp_path / 'run_service.py'
    marker_path = tmp_path / 'root_path.txt'
    script.write_text(
        "import sys, time, base64\n"
        "sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent.parent / 'src')) + ")\n"
        "from nijsetu.web import WorkspaceService\n"
        "s = WorkspaceService()\n"
        "open(" + repr(str(marker_path)) + ", 'w').write(str(s.root))\n"
        "data = b'Patient Name,UHID\\nSigkillCanaryName,UHSIGKILL777\\n'\n"
        "s.create({'files': [{'name': 'f.csv', 'content': base64.b64encode(data).decode()}]})\n"
        "time.sleep(30)\n",
        encoding='utf-8',
    )
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not marker_path.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker_path.exists(), "service never started"
        time.sleep(.3)  # let create() finish sealing the upload into the vault
        workspace_root = Path(marker_path.read_text())
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)

        assert workspace_root.exists(), "nothing left behind to inspect -- test is not exercising the crash path"
        found_any_file = False
        for p in workspace_root.rglob('*'):
            if not p.is_file():
                continue
            found_any_file = True
            raw = p.read_bytes()
            assert b'SigkillCanaryName' not in raw
            assert b'UHSIGKILL777' not in raw
        assert found_any_file, "expected leftover ciphertext from the killed run; found none"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        import shutil as _shutil
        if marker_path.exists():
            _shutil.rmtree(Path(marker_path.read_text()), ignore_errors=True)
