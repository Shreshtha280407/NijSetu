"""Record-specific pseudonym verification against the SERIALIZED output.

SPEC.md section 6: "the `patient` value must actually appear inside that
exported record". The previous implementation checked whether the token
appeared anywhere in the exported FILE's text. That is not the same claim and
is not sound: a token present in patient B's row proves nothing about patient
A's row, so every record in a file inherited the strongest claim any record in
it could make. A file with one correctly-tokenized row would vouch for all the
others, including rows whose pseudonym was wrong, missing, or belonged to a
different person.

So verification is done by ADDRESSING the record. Each claim carries an output
locator (`out`, per SPEC.md) alongside the input locator (`loc`); this module
resolves that locator in the re-parsed exported document and looks for the
token only inside the resolved node.

Design rules, in order of priority:

* **Re-parse, never trust.** The exported bytes are read and parsed again here.
  The transform's own bookkeeping is not consulted, so a serialization bug is
  caught rather than described.
* **Address exactly one record.** A CSV claim resolves to one row; a JSON
  claim resolves to one node via an RFC 6901 pointer; a document claim
  resolves to the whole document, which is the only case where
  whole-file scope is the correct scope.
* **Ambiguity abstains, it does not guess.** An unresolvable locator, a
  duplicated locator, or a row index past the end of the document yields an
  explicit failure reason, and the caller downgrades the claim to
  `patient: null`. Abstention is free under the organizer's scoring; a wrong
  claim is a false merge.
* **Abstention is reported, never silent.** Every rejection returns a reason
  code so the adapter can count and surface them.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

_CSV_ROW_PREFIX = "row:"


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    reason: str  # "OK" or a specific rejection code


_OK = VerificationResult(True, "OK")


def _reject(reason: str) -> VerificationResult:
    return VerificationResult(False, reason)


def json_pointer_unescape(token: str) -> str:
    """RFC 6901: `~1` is `/` and `~0` is `~`, and the order matters -- doing
    `~0` first would turn the escaped sequence `~01` into `/` instead of `~1`."""
    return token.replace("~1", "/").replace("~0", "~")


def resolve_json_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    """Returns (found, node). An RFC 6901 pointer; "" addresses the whole
    document. Array indices must be canonical decimal (no leading `+`, no
    leading zeros beyond "0" itself, no whitespace), which is what stops a
    pointer like `/01` from silently resolving to element 1."""
    if pointer in ("", "doc"):
        return True, document
    if not pointer.startswith("/"):
        return False, None
    node = document
    for raw_token in pointer[1:].split("/"):
        token = json_pointer_unescape(raw_token)
        if isinstance(node, dict):
            if token not in node:
                return False, None
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or (len(token) > 1 and token[0] == "0"):
                return False, None
            index = int(token)
            if index >= len(node):
                return False, None
            node = node[index]
        else:
            return False, None
    return True, node


def _node_contains_token(node: Any, token: str) -> bool:
    """True when `token` appears as a scalar value (or inside one) anywhere in
    this node's subtree. Keys are deliberately NOT searched: a pseudonym is a
    value, and a key matching it would be a structural coincidence, not a
    patient claim."""
    if isinstance(node, dict):
        return any(_node_contains_token(value, token) for value in node.values())
    if isinstance(node, list):
        return any(_node_contains_token(item, token) for item in node)
    if isinstance(node, str):
        return token in node
    return token in str(node) if node is not None else False


def parse_csv_rows(text: str, delimiter: str) -> list[list[str]]:
    """Rows as the exported document actually serializes them, including the
    header at index 0. Parsed with the csv module rather than split on
    newlines, so a quoted multi-line cell cannot desynchronize row indices."""
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))


def _row_index(locator: str) -> int | None:
    if not locator.startswith(_CSV_ROW_PREFIX):
        return None
    raw = locator[len(_CSV_ROW_PREFIX):]
    if not raw.isdigit() or (len(raw) > 1 and raw[0] == "0"):
        return None
    return int(raw)


def verify_csv_claim(text: str, delimiter: str, out_locator: str, token: str) -> VerificationResult:
    """The token must appear in the exact exported row `out_locator` names."""
    index = _row_index(out_locator)
    if index is None:
        return _reject("CSV_LOCATOR_NOT_A_ROW")
    rows = parse_csv_rows(text, delimiter)
    if not rows:
        return _reject("CSV_EXPORT_EMPTY")
    data_row_index = index + 1  # rows[0] is the header
    if data_row_index >= len(rows):
        return _reject("CSV_ROW_INDEX_PAST_END_OF_EXPORT")
    if not any(token in cell for cell in rows[data_row_index]):
        return _reject("CSV_TOKEN_ABSENT_FROM_CLAIMED_ROW")
    return _OK


def verify_json_claim(text: str, out_locator: str, token: str) -> VerificationResult:
    """The token must appear inside the exact node `out_locator` resolves to."""
    try:
        document = json.loads(text)
    except ValueError:
        return _reject("JSON_EXPORT_UNPARSEABLE")
    found, node = resolve_json_pointer(document, out_locator)
    if not found:
        return _reject("JSON_POINTER_DOES_NOT_RESOLVE_IN_EXPORT")
    if not _node_contains_token(node, token):
        return _reject("JSON_TOKEN_ABSENT_FROM_CLAIMED_NODE")
    return _OK


def verify_document_claim(text: str, token: str) -> VerificationResult:
    """A whole-document claim (a text note, or a single-patient FHIR Bundle).

    Here the record IS the document, so document scope is the correct scope --
    this is the one case where searching the file is searching the record.
    """
    if token not in text:
        return _reject("DOC_TOKEN_ABSENT_FROM_EXPORT")
    return _OK


def find_duplicate_locators(claims: list[tuple[int, str]]) -> set[tuple[int, str]]:
    """(file_no, out_locator) pairs claimed more than once.

    Two records resolving to one exported location means at least one claim is
    wrong and there is no evidence which. Both are rejected rather than
    letting whichever happened to be processed last win.
    """
    seen: set[tuple[int, str]] = set()
    duplicates: set[tuple[int, str]] = set()
    for claim in claims:
        if claim in seen:
            duplicates.add(claim)
        seen.add(claim)
    return duplicates
