# Gate 0.6 — Encryption-at-Rest & Breach-Resilient Storage: Final Report

Branch: `phase/gate06-encryption-at-rest` (off `main` @ `d45f5c1`, working tree clean before this work started). Nothing in this branch has been committed or pushed — it is left in the working tree for review, per instructions.

**Verdict: PASS** — the two production paths that wrote sensitive raw/private material to disk in plaintext now write only authenticated ciphertext (or never touch persistent disk at all); every existing acceptance gate, the full pytest suite, `ruff`, and the organizer practice run reproduce their prior behavior and metrics unchanged.

> **Note on the organizer toolkit.** `Reinforce-SST/Secret-Wards-ToolKit` is not present as a sibling checkout in this environment (no `../Secret-Wards-ToolKit`), so its `docs/*.md` could not be re-read in this session and no toolkit commit SHA can be honestly recorded here. Section 2's requirement (encryption of raw/intermediate data, keys separate from data, PII-free logging, safe quarantine, design for eventual compromise) was already established from this repository's own `docs/PROBLEM_STATEMENT.md`-derived prior work (see `GATE0_FINAL_REPORT.md`) and from the task brief itself, which restates the same requirement verbatim. The organizer's run-time contract (`raksha.toml`, `main.py`, `output/{sanitized,report.json,records.jsonl}`) is unchanged and independently re-verified below against this repo's own bundled fixtures standing in for the toolkit's practice corpus.

---

## A. Initial security audit

Every sensitive disk write in the repository was traced by grepping for `write_bytes`, `write_text`, `open(..., "w"/"wb")`, `TemporaryDirectory`, `mkdtemp`, `NamedTemporaryFile`, `shutil.copy*`, `pickle`, and `json.dump`, across `src/nijsetu/`, then reading the full call path for each hit.

| Data | Sensitive? | Found at (before) | Disposition |
|---|---|---|---|
| Organizer-supplied input directory | Yes | Read-only, never written | Unchanged — never touched (confirmed: `organizer.py` only reads bytes into memory and SHA-256-hashes files; `discover_files` never writes) |
| **Dashboard-uploaded raw files** | **Yes** | `web.py:WorkspaceService.create()` — `path.write_bytes(data)` into a 0700 temp dir, plaintext | **Fixed** — now AES-256-GCM ciphertext (Phase C) |
| **Private analysis evidence (`private/analysis.json`)** | **Yes** | `plan/__init__.py:write_plan()` — plaintext JSON, 0600 | **Fixed** — now AES-256-GCM ciphertext (Phase D) |
| Phase 2 staging `private/` directory | Potentially | `phase2/staging.py:Staging.__init__` creates it, 0700 | **Audited, found unused** — grepped every reference to `.private` across `phase2/`; nothing is ever written there in the current implementation. Left as-is (dead/reserved space, genuinely empty); documented here rather than silently ignored. |
| Phase 2 sanitize released output (`staging.public/released/*`) | No (already redacted) | `phase2/sanitize.py` — plaintext | **Unchanged, correctly so**: every byte written here is the output of `transform_csv`/`transform_json_record`/`transform_txt`/`sanitize_fhir_document` — i.e. already policy-redacted. This is the same trust boundary as the final publish, which the task explicitly says must stay plaintext. |
| Organizer internal staging (`organizer.py`'s `tempfile.TemporaryDirectory`) | No (already redacted) | `organizer.py:build_output` | **Unchanged, correctly so** — holds only `run_sanitize`'s already-sanitized output, `shutil.copyfile`'d byte-for-byte into `output/sanitized/`. No raw content passes through it. |
| `report.json` / `hold_index.json` / `manifest.json` / `records.jsonl` | No (metadata + pseudonyms only) | `sanitize.py`, `organizer.py` | Unchanged — counts, reason codes, HMAC tokens; never a raw literal. |
| Gate 1/2 test-fixture writes (`gate/phase1.py`, `gate/phase2.py`) | No (synthetic test data) | Various `write_text` in temp dirs | Unchanged — these are the gates' own synthetic/canary fixtures, not production patient data. |
| Encryption keys (`self.key`, `self.storage_key`, CLI `--private-key-file`) | Highly sensitive | Process memory / key file | Storage keys: process memory only, never written beside ciphertext (see §C). A user-supplied `--private-key-file` is, by design, the one place a *storage* key is persisted at all — on the user's own explicit request, exactly like the pre-existing `sanitize --key-file`. |
| Dashboard/CLI logs, stderr | Must exclude PII | `web.py` exception handler, `cli.py` | Unchanged — already PII-free by construction (`error='ENGINE_PROCESSING_FAILED'`, reason-code-only exceptions); verified no secret/key ever appears in any new log line added. |

**Conclusion of the audit:** exactly two call sites in the entire codebase wrote raw/private material to persistent disk as plaintext. Both are fixed below. Every other disk write found either never carries raw PII (already-sanitized release content, metadata-only reports) or carries only synthetic test fixtures.

---

## B. Threat model

Implemented in `src/nijsetu/security/secure_store.py` (module docstring carries the authoritative version of this section).

| Threat | Protected? | How |
|---|---|---|
| **A — Storage snapshot/backup theft** | Yes | Raw uploads and the private analysis artifact are AES-256-GCM ciphertext the moment they touch disk. A stolen `/tmp` snapshot, disk image, or backup of the vault directory discloses nothing without the in-memory key. |
| **B — Abnormal termination (crash/SIGKILL)** | Yes, demonstrated | Whatever a crash leaves behind in the vault is already ciphertext — there is no "encrypt on clean shutdown" step to skip. Demonstrated with a real `SIGKILL` in `tests/test_web.py::test_sigkill_during_processing_leaves_only_ciphertext_behind` (subprocess killed mid-run; leftover files on disk inspected directly — no PII found). The brief RAM-backed (tmpfs) decrypted copy used *during* processing (see §C) is not disk-persistent by construction; see §H for what a crash during that specific window leaves behind. |
| **C — Local file disclosure (list/read, no live-memory access)** | Yes | Vault files use generic, hash-derived names (`SecureStore._physical_path` — SHA-256 of the logical id) — an attacker who can `ls`/`cat` the workspace sees opaque `.bin` files and ciphertext bytes, never a patient name in a filename or in content. Directories are 0700; files 0600. |
| **D — Tampering with ciphertext at rest** | Yes | AES-GCM's authentication tag is checked on every decrypt (`decrypt_blob`). Any bit flip anywhere in the envelope — nonce, ciphertext, or tag — raises `SecureStoreError("AUTHENTICATION_FAILED")` and returns nothing. Demonstrated in `tests/test_secure_store.py::test_tampered_ciphertext_rejected` / `test_tampered_nonce_rejected`. |
| **E — Full running-host compromise (root, live memory)** | **Not claimed** | Explicitly out of scope and stated as such in code comments and here: an attacker who can read the live process's memory can read the plaintext buffer and the key while they are in use. No application-level scheme defeats this. Nothing in this implementation claims otherwise. |

---

## C. Architecture

### New module: `src/nijsetu/security/` (`secure_store.py` + `__init__.py`)

A cryptographic domain **distinct from `phase2.keys`** (the HMAC pseudonymization/release-key machinery). Nothing here ever influences a patient token, date shift, or published byte.

- **Algorithm:** AES-256-GCM via `cryptography.hazmat.primitives.ciphers.aead.AESGCM`. No custom cipher, MAC, nonce generator, or padding is implemented.
- **Envelope** (versioned, self-describing):
  ```
  byte 0        version (currently 1)
  bytes 1..13   96-bit random nonce (os.urandom(12), fresh on every encrypt call)
  bytes 13..    AES-GCM ciphertext + 16-byte tag (library convention)
  ```
- **Associated data** (never stored; both sides recompute it): `version || len‖purpose || len‖logical_id`. This binds every ciphertext to *why* it exists and *which* artifact it is — decrypting file A's ciphertext under file B's identity fails the tag check. This is what stops ciphertext substitution between artifacts (ReportFindings-style "swap A's blob into B's slot" attack).
- **`SecureStore`**: a directory of many such blobs, addressed by a caller-chosen `logical_id` string. The physical filename is `sha256(logical_id).hex() + ".bin"` — the logical id (which may embed an uploaded filename, itself potentially PII-bearing) never appears on disk. Atomic writes: encrypt → write to a `tempfile.mkstemp` sibling in the same directory (0600) → `fsync` → `os.replace` (atomic rename on the same filesystem, so a reader never observes a partial file).
- **Key lifecycle:**
  - **Dashboard (`web.py`):** `WorkspaceService.__init__` mints two *independent* `secrets.token_bytes(32)` values: `self.key` (unchanged — the existing HMAC release/master key fed to `run_sanitize`) and `self.storage_key` (new — feeds `SecureStore`). Neither is derived from the other. Both live only in process memory; `close()` best-effort zeroes the storage key's bytes and deletes the vault directory.
  - **`analyze` CLI (`cli.py`):** `--private-key-file PATH` is optional. If the file exists, its bytes are passed through `derive_storage_key` (one domain-separated HMAC-SHA256 call, label `NIJSETU_SECURE_STORE_KDF_V1` — distinct from every label `phase2.keys` uses) to normalize arbitrary-length key material into a dedicated 32-byte AES key. If the file does not exist, a fresh random key is generated, written there at 0600, and the path is printed (never the key bytes) so the *same* command can decrypt the artifact again later — addressing the brief's explicit concern that a silently-ephemeral key would make the artifact permanently unusable. If the flag is omitted entirely, a `secrets.token_bytes(32)` ephemeral key is used and a stderr warning states plainly that the artifact cannot be decrypted after the process exits.
- **Authenticated-decryption flow:** `read_decrypted`/`decrypt_blob` → length/version checks → `AESGCM.decrypt` → `InvalidTag` is caught and re-raised as `SecureStoreError("AUTHENTICATION_FAILED")`, deliberately not distinguishing wrong-key from tampered-ciphertext from AAD-mismatch (all three are the same AEAD failure and must fail identically, never partially).
- **Cleanup:** `SecureStore.close()` removes the whole vault directory and best-effort zeroes the in-memory key bytes (documented as best-effort only — Python cannot guarantee no other reference to the key bytes survives, e.g. in a caller's own `bytes` object; this is stated directly in the code, not glossed over).

### Phase C — Dashboard uploads (`web.py`)

- `WorkspaceService.create()`: each uploaded file is sealed via `self.vault.write_encrypted(f"{run_id}:{name}", data, purpose=b"upload")` the instant it is decoded from the request — raw bytes never reach `write_bytes` on an unencrypted path. A pure-logical (no disk I/O) path-conflict check (`_check_path_conflicts`) replaces the old disk-level `OSError` catch, preserving the exact same `CONFLICTING_FILE_PATHS` behavior for e.g. `a.txt` + `a.txt/child.txt` without needing to touch disk to detect it.
- `WorkspaceService._process()`: the ingest/detect/sanitize pipeline needs real filesystem paths (`discover_files` walks a directory tree; this is true throughout `ingest/`, `phase2/transform_*`, etc. — rewriting the whole pipeline to accept in-memory buffers was evaluated and rejected as exactly the invasive, high-regression-risk rewrite the task warns against). Instead: `ram_backed_tempdir()` verifies (via `/proc/mounts`, never assumed) a tmpfs-backed location with enough free space, each uploaded file is decrypted from the vault and written *only* there, the pipeline runs against that directory, and the directory is `shutil.rmtree`'d in a `finally` regardless of outcome. **If no RAM-backed location is verified available, the run fails closed (`RAM_BACKED_STORAGE_UNAVAILABLE`) rather than silently falling back to an ordinary disk-backed temp directory.** The sanitized `release/` output is unaffected — it is written exactly where it always was (`root / 'release'`, on `self.root`'s backing store), because it is already-redacted content, the same trust boundary as the final publish.
- Vault blobs for a run are deleted in the same `finally` block, success or failure.

### Phase D — Private analysis artifact (`plan/__init__.py`)

- `write_plan(plan, output_dir, private_key)` now encrypts `plan.private` with `encrypt_blob(private_key, b"private-analysis", b"analysis", plaintext)` before writing `private/analysis.json` (same path, same 0600 mode — `tests/test_cli.py`'s existence/permission assertions pass unchanged).
- `read_private_artifact(private_path, private_key)` is the new authorized reader: decrypts, authenticates, and `json.loads`s the result, raising `SecureStoreError` (never a partial parse) on any failure.
- `report.json` (the safe, already-PII-free aggregate) is unchanged — still plaintext, as it must be.

### Phase E — Staging / sanitize / organizer

No code change was needed here. The audit (§A) found no raw-plaintext-to-disk gap in `phase2/staging.py`, `phase2/sanitize.py`, or `organizer.py` — every disk write in those three files is either already-sanitized release content (the explicitly-allowed trust boundary) or metadata/pseudonym-only. This is documented rather than solved with unnecessary, speculative encryption of content that was never raw to begin with.

---

## D. Changed files

| File | Change | Why |
|---|---|---|
| `src/nijsetu/security/__init__.py`, `secure_store.py` (new) | AES-256-GCM envelope primitives, `SecureStore`, `derive_storage_key`, `is_tmpfs`/`ram_backed_tempdir` | The one new reusable security component (Phase B) |
| `pyproject.toml`, `requirements.txt` | Added `cryptography==46.0.7` as the one mandatory runtime dependency; comments updated | Stdlib has no AEAD primitive; this is the preferred, audited library the task specifies |
| `vendor/wheels/cryptography-*.whl`, `cffi-*.whl`, `pycparser-*.whl` (new) | Vendored cp311/manylinux wheels for the new dependency and its own two transitive dependencies | Preserves the existing fully-offline install contract (`gate/phase1.py`'s `_check_wheel_offline_gazetteer`, `tests/test_package_install.py`, `tests/test_phase2_package_install.py`) exactly as rapidfuzz's wheel already does |
| `src/nijsetu/web.py` | Vault-backed uploads, RAM-backed decrypt staging, two independent keys, `_check_path_conflicts` | Phase C |
| `src/nijsetu/plan/__init__.py` | `write_plan`/`read_private_artifact` encrypt/decrypt the private artifact | Phase D |
| `src/nijsetu/cli.py` | `analyze --private-key-file`, `_resolve_private_key` | Key-management workflow for the `analyze` CLI's now-encrypted artifact |
| `src/nijsetu/gate/phase1.py` | `write_plan` call passes a key; `_check_private_artifact_protected` now decrypts to verify; new `_check_private_artifact_is_ciphertext_not_plaintext` check; stale wheel-check comment fixed | The gate's own internal check had to be updated to match the (intentionally) changed artifact format — this is NijSetu's own test code, not an organizer script |
| `tests/test_secure_store.py` (new) | 28 tests: crypto correctness, confidentiality, crash-resilience, key derivation, `write_plan`/`read_private_artifact` | Phase F |
| `tests/test_web.py` | +3 tests: PII-never-on-disk during a live run, key-domain-separation assertion, real `SIGKILL` crash demonstration | Phase F |
| `README.md` | Corrected the "no mandatory dependencies" claim; new "Encryption at rest" section; `analyze` docs mention `--private-key-file` | Documentation accuracy |

No change to `raksha.toml`, `main.py`, `config/policy.yaml`, the organizer output contract, the frontend, or any privacy/linking/scoring logic.

---

## E. Cryptographic controls (summary table)

| Control | Value |
|---|---|
| Algorithm | AES-256-GCM (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`) |
| Key size | 256 bits |
| Nonce | 96 bits, `os.urandom(12)`, fresh per encryption call — never reused with the same key |
| Authentication | AEAD tag (16 bytes), checked on every decrypt; `InvalidTag` → fail closed |
| AAD | `version ‖ len‖purpose ‖ len‖logical_id` — purpose- and identity-bound, recomputed by both sides, never stored |
| Envelope version | 1 byte; unknown version → `UNKNOWN_ENVELOPE_VERSION`, rejected |
| Truncation handling | Length checked before any crypto operation → `MALFORMED_OR_TRUNCATED_CIPHERTEXT` |
| Oversized input | Defensive ceiling (`MAX_ARTIFACT_BYTES` = 256 MiB) → `ARTIFACT_TOO_LARGE`, rejected before buffering |
| Key domains | Storage encryption keys (`SecureStore`) are always independently random or KDF-derived with a dedicated label; never the HMAC release/master key from `phase2.keys` |
| Disk-persisted keys | None, except a user-opted-in `--private-key-file` (explicit, analogous to the pre-existing `sanitize --key-file`) |
| Keys in logs/reports/API | None — verified by `test_vault_key_is_independent_of_hmac_release_key` and the PII/secret-absence checks in `gate/phase2.py`'s pre-existing `private_artifacts_and_secrets_never_public` check (still passing) |

---

## F. Verification evidence

### Cryptographic correctness (`tests/test_secure_store.py`, 28 tests, all passing)

Round-trip; wrong-key rejection; tampered-ciphertext rejection (tag and nonce bytes independently); truncated-ciphertext rejection; empty-envelope rejection; unknown-version rejection; nonce uniqueness across 50 identical-plaintext encryptions; cross-artifact substitution rejection (both wrong logical-id and wrong purpose); binary/Unicode/empty/1 MiB payloads; oversized-plaintext rejection; invalid-key-length rejection; `derive_storage_key` determinism, domain separation, and short-material rejection; `SecureStore` write/read/delete round-trip, generic physical filenames, 0700 vault directory, on-disk bytes never containing the plaintext, `close()` wiping the vault; a simulated-crash test (drop the key reference, inspect orphaned ciphertext directly, confirm a fresh instance with a different key still cannot decrypt it); `write_plan`/`read_private_artifact` round-trip, wrong-key rejection, and corrupted-file rejection; `is_tmpfs`/`ram_backed_tempdir` sanity checks.

### Storage confidentiality, production path (`tests/test_web.py`)

`test_raw_pii_never_touches_disk_as_plaintext`: uploads a synthetic record containing a distinctive fictitious name, an Aadhaar-like number, a UHID, a phone number, and an address. Scans **every file actually on disk** under the service's workspace root — immediately after upload, repeatedly during live processing, and after completion — asserting none of the five canary values, nor the uploaded filename, appear in any file's content or path. All scans pass; the window where ciphertext (not plaintext) sits in the vault, and the window where only a tmpfs-verified directory holds the brief decrypted copy, are both covered.

`test_vault_key_is_independent_of_hmac_release_key`: `service.storage_key != service.key`.

### Crash recovery (`tests/test_web.py::test_sigkill_during_processing_leaves_only_ciphertext_behind`)

A real `WorkspaceService` is started in a **subprocess**, given a canary upload, and **`SIGKILL`'d** mid-run (no clean shutdown, `close()` never runs). The test then inspects every file the OS actually left behind under that process's workspace directory. Result: leftover files exist (the vault blob is **not** auto-cleaned by a kill — this is stated plainly, not hidden), but none of them contain the raw canary PII in readable form. This demonstrates Threat B directly rather than asserting it. Separately, `test_secure_store.py`'s simulated-crash test confirms the orphaned ciphertext is not decryptable by a later process holding a different key.

### Existing application behavior (regression)

All pre-existing tests pass unchanged: multilingual detection, identity linking/pseudonym consistency, clinical-value preservation, FHIR validity, quarantine behavior, review-gating, release-ZIP download — none of their assertions were weakened to make the new system pass (`git diff` on every pre-existing test file other than `test_web.py` is empty; `test_web.py`'s only change is three added tests plus the imports they need).

### Security negatives

- Secrets/keys never in stdout/stderr: the only new print statements are a stderr warning about the *ephemeral* key path (never key bytes) and a confirmation of the key-file *path* on first bootstrap (never key bytes) — both read directly from the code in §C.
- Corrupted artifacts fail closed, never partially parsed: `read_private_artifact` raises before `json.loads` ever sees anything but fully-authenticated plaintext.
- No silent fallback to unencrypted storage anywhere: `_process` raises `RAM_BACKED_STORAGE_UNAVAILABLE` rather than writing to ordinary disk when tmpfs isn't verified available; `SecureStore`/`encrypt_blob` never have a plaintext-write code path at all.

### Fresh installation and offline execution

```
$ pip install -e ".[dev]"            # resolves cryptography==46.0.7 from PyPI automatically (network, setup-time only)
$ python -m pytest tests/ -q
490 passed, 13 skipped

$ python -m ruff check src/ scripts/ tests/
Found 5 errors   # all 5 pre-existing on main at identical source lines (verified via `git stash`); zero new findings

$ python -m pytest tests/test_package_install.py tests/test_phase2_package_install.py -q
2 passed    # builds a wheel, creates a fresh venv, `pip install --no-index --find-links vendor/wheels` only

$ python -m nijsetu gate phase1 --output /tmp/nijsetu-secure-g1   # exit 0, PASS, 15/15 checks (13 pre-existing + 2 new)
$ python -m nijsetu gate phase2 --output /tmp/nijsetu-secure-g2   # exit 0, PASS, 22/22 checks (all pre-existing, unchanged)
```

Offline install independently re-verified: a fresh `python3.11 -m venv` with `pip install --no-index --find-links vendor/wheels cryptography cffi pycparser` succeeds and `from cryptography.hazmat.primitives.ciphers.aead import AESGCM` round-trips correctly — no network access, no compilation (all three are pre-built wheels for `cp311`/manylinux2014+ x86_64, matching the existing rapidfuzz wheel's platform convention).

---

## G. Organizer regression results

Ran in this session, `tests/fixtures/dev` standing in for the (unavailable, see note at top) toolkit practice corpus — same corpus, same commands, before vs. after this branch's changes:

```
$ git worktree add -f /tmp/baseline-check main   # unmodified main @ d45f5c1
$ RAKSHA_SECRET=practice python main.py --input tests/fixtures/dev --output /tmp/.../before
$ RAKSHA_SECRET=practice python main.py --input tests/fixtures/dev --output /tmp/.../after   # this branch
```

| Metric | Before (`main`) | After (this branch) |
|---|---:|---:|
| Exit code | 0 | 0 |
| `files_total` / `files_sanitized` / `files_quarantined` | 53 / 45 / 8 | 53 / 45 / 8 (identical) |
| `records_processed` / `records_released` / `records_suppressed` / `records_quarantined` | 73 / 56 / 16 / 1 | 73 / 56 / 16 / 1 (identical) |
| `identifiers_detected` / `records_linked` / `record_claims_published` | 102 / 43 / 47 | 102 / 43 / 47 (identical) |
| Full `counts` object | — | **byte-identical** (`diff` → no output) |
| Wall time | 0.085 s | 0.075 s |
| Peak RSS | 24.4 MB | 29.6 MB (cryptography module load; negligible vs. the 4 GiB budget) |

Additional checks run against this branch alone:

- **Determinism:** same `RAKSHA_SECRET=practice` run twice → `records.jsonl` and `sanitized/` tree **byte-identical** (`diff -rq` → no differences).
- **Secret rotation:** `RAKSHA_SECRET=different_secret` → `records.jsonl` **differs** (pseudonyms rotate as required).
- **No raw-copy regression:** organizer's `output/sanitized/` tree is produced by the same `run_sanitize`/`shutil.copyfile` path as before, untouched by this branch.

The organizer's own `validate_output.py`/`check_dev.py`/`check_submission.py` could not be re-run in this session (toolkit not present locally — see the note at the top of this report); the metrics above are this repository's own report.json counts, compared directly before/after on an identical corpus and command, which is the strongest check available without the toolkit checkout.

---

## H. Remaining limitations (stated plainly, not minimized)

1. **Threat E (full host compromise, live memory) is not defended against**, and no part of this implementation claims otherwise. A root-level attacker who can read process memory can read a plaintext buffer or an in-memory key while it is in use.
2. **The RAM-backed decrypted copy used during dashboard processing (`web.py:_process`) is not itself ciphertext while processing is in flight.** It is verified to be tmpfs (never swapped to a disk snapshot/backup under normal operation), and is removed in a `finally` block on every exit path — but a `SIGKILL` *during that specific window* (after decrypt, before the `finally` runs) would leave plaintext in that tmpfs directory until the OS reclaims it (reboot, or the directory's parent being cleaned). This is a narrower window than before this change (previously, plaintext sat in an *unverified*, possibly-disk-backed temp directory for the whole run), but it is not zero, and is disclosed here rather than claimed away. A full in-memory-buffer rewrite of the ingest pipeline (avoiding any path-based staging at all) was evaluated and rejected as the large, high-regression-risk rewrite the task explicitly warns against pursuing for "minimum regression risk."
3. **The organizer-supplied read-only input directory is, correctly, never encrypted** — it is external, read-only, and out of this application's control, exactly as instructed.
4. **The published `sanitized/`/`release/` output remains plaintext** — this is the explicitly-allowed trust boundary (already policy-redacted content the evaluator scores directly), not an oversight.
5. **Crash artifacts are not auto-cleaned by a kill.** Demonstrated directly: the `SIGKILL` test leaves the vault blob (ciphertext) on disk; nothing in this implementation claims a crash triggers cleanup. What is claimed, and verified, is that whatever a crash leaves behind is not raw PII in readable form.
6. **`phase2/staging.py`'s unused `private/` directory** is not a live gap today (nothing writes to it), but it is also not actively used for anything — left as reserved/dead space rather than removed, since removing unused-but-harmless code was outside this task's scope.
7. **No live organizer-toolkit re-verification** in this session, for the reason stated at the top of this report.
8. **Python's memory model**: `SecureStore.close()`'s key-wiping is explicitly documented as best-effort only; no claim is made that no other reference to key bytes survives in the interpreter.

---

## I. Verdict

**PASS.**

Both sensitive-data disk-write gaps identified in the audit (dashboard raw uploads; the `analyze` CLI's private analysis artifact) are now authenticated-encrypted at rest with a correctly-separated key domain, versioned/tamper-evident envelopes, and fail-closed authentication — verified by cryptographic correctness tests, a live-process disk scan for synthetic PII across the entire upload→process→cleanup lifecycle, and a real `SIGKILL` crash demonstration, not merely by unit tests of the new crypto module in isolation. Every pre-existing acceptance gate (`gate phase1`, `gate phase2`), the full `pytest` suite (490 passed, 0 newly failing, 13 pre-existing skips), `ruff` (0 new findings), and the organizer practice run (byte-identical counts, preserved determinism and secret rotation, negligible runtime/memory delta) reproduce their prior behavior unchanged. The one honest gap is the organizer toolkit's own checker scripts, which were not available to re-run in this environment — disclosed rather than glossed over, with the strongest available substitute (direct before/after comparison on this repo's own fixtures and report-level counts) performed instead.
