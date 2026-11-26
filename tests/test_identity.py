"""Patient identity resolution: scoped exact matches, candidate blocking, scoring, clustering."""

from pathlib import Path

from nijsetu.config import AppConfig
from nijsetu.gate.phase1 import DEV_SOURCE_SYSTEM_MAP
from nijsetu.plan import run_pipeline

DEV_CONFIG = AppConfig(source_system_map=DEV_SOURCE_SYSTEM_MAP)


def _records_by_path(private: dict, substring: str) -> list[dict]:
    return [r for r in private["records"] if substring in r["file_path"]]


def _clusters(private: dict, substring: str) -> set[str | None]:
    return {r["cluster_id"] for r in _records_by_path(private, substring) if r["link_status"] == "ACCEPTED"}


def test_corroborated_arif_four_fragments_link(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    clusters = _clusters(plan.private, "arif_corroborated/")
    assert len(clusters) == 1 and None not in clusters
    recs = _records_by_path(plan.private, "arif_corroborated/")
    assert len(recs) == 4
    assert all(r["link_status"] == "ACCEPTED" for r in recs)


def test_arif_ambiguous_stays_held(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    recs = _records_by_path(plan.private, "arif_ambiguous/")
    assert recs[0]["link_status"] == "HELD"
    assert recs[0]["reason_codes"]


def test_different_arif_not_merged_with_corroborated_group(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    corroborated = _clusters(plan.private, "arif_corroborated/")
    different = _clusters(plan.private, "arif_different/")
    assert not (corroborated & different)


def test_meera_three_sources_link_via_shared_uhid(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    clusters = _clusters(plan.private, "meera_corroborated/")
    assert len(clusters) == 1 and None not in clusters


def test_same_uhid_different_issuer_does_not_merge(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    a = _clusters(plan.private, "uhid_cross_issuer_hospitalA/")
    b = _clusters(plan.private, "uhid_cross_issuer_hospitalB/")
    assert not (a & b)


def test_shared_household_phone_does_not_force_a_merge(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    recs = _records_by_path(plan.private, "household_shared/")
    assert len(recs) == 2
    assert {r["cluster_id"] for r in recs if r["link_status"] == "ACCEPTED"} == set()


def test_conflicting_authoritative_ids_veto_merge_with_explicit_reason(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    recs = _records_by_path(plan.private, "conflicting_ids/")
    assert all(r["link_status"] == "HELD" for r in recs)
    assert all("ID_CONFLICT" in r["reason_codes"] for r in recs)


def test_transitive_bridge_conflict_does_not_fully_merge(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    recs = _records_by_path(plan.private, "bridge_conflict/")
    cluster_ids = {r["cluster_id"] for r in recs}
    assert len(cluster_ids) > 1  # never collapses into one cluster


def test_provider_and_patient_same_name_not_linked_as_one_person(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    csv_ramesh = _records_by_path(plan.private, "csv_basic/registration.csv")
    txt_ramesh = _records_by_path(plan.private, "txt_notes/provider_patient_collision.txt")
    csv_cluster = {r["cluster_id"] for r in csv_ramesh if r["link_status"] == "ACCEPTED"}
    txt_cluster = {r["cluster_id"] for r in txt_ramesh if r["link_status"] == "ACCEPTED"}
    assert not (csv_cluster & txt_cluster)


def test_deterministic_across_repeated_runs(fixtures_dev: Path):
    def without_timing(plan):
        return {k: v for k, v in plan.safe_report.items() if k != "timing"}

    plan_a = run_pipeline(fixtures_dev, DEV_CONFIG)
    plan_b = run_pipeline(fixtures_dev, DEV_CONFIG)
    assert without_timing(plan_a) == without_timing(plan_b)


def _record(private: dict, file_path: str) -> dict:
    return next(r for r in private["records"] if r["file_path"] == file_path)


def _records_in_order(private: dict, file_path: str) -> list[dict]:
    recs = [r for r in private["records"] if r["file_path"] == file_path]
    return sorted(recs, key=lambda r: int(r["record_key"].split(":")[1]))


# --- Finding 1: source-system issuer scoping ---------------------------------

def test_unconfigured_files_with_same_local_id_do_not_auto_merge(fixtures_dev: Path):
    """Direct reproduction of the reported bug: two independently sourced,
    unconfigured files sharing a repeated local identifier must never be
    treated as an authoritative exact match."""
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    a = _record(plan.private, "source_scope_unknown/hospitalA/records.csv")
    b = _record(plan.private, "source_scope_unknown/hospitalB/records.csv")
    assert a["cluster_id"] != b["cluster_id"]
    assert not any(code.startswith("SCOPED_REF:") for code in a["reason_codes"] + b["reason_codes"])


def test_deliberately_shared_issuer_legitimately_links(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    a = _record(plan.private, "source_scope_configured_shared/hospitalA/records.csv")
    b = _record(plan.private, "source_scope_configured_shared/hospitalB/records.csv")
    assert a["link_status"] == "ACCEPTED" and b["link_status"] == "ACCEPTED"
    assert a["cluster_id"] == b["cluster_id"]
    # The configured issuer name ("NETWORK_X") must never appear in a reason
    # code -- only the fixed, bounded public label (see identity.scoped).
    assert any("SCOPED_REF:CONFIGURED_SOURCE:uhid" in code for code in a["reason_codes"])
    assert not any("NETWORK_X" in code for code in a["reason_codes"])


def test_two_configured_distinct_issuers_do_not_merge(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    a = _record(plan.private, "source_scope_configured_distinct/hospitalA/records.csv")
    b = _record(plan.private, "source_scope_configured_distinct/hospitalB/records.csv")
    assert a["cluster_id"] != b["cluster_id"]


def test_national_identifier_links_structured_csv_to_freetext_note(fixtures_dev: Path):
    """Aadhaar must resolve to the same scope whether detected in a
    structured CSV field or in free text -- proving the national-scheme
    issuer fix survives into an actual exact link."""
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    csv_rec = _record(plan.private, "aadhaar_consistency/registration.csv")
    note_rec = _record(plan.private, "aadhaar_consistency/note.txt")
    assert csv_rec["link_status"] == "ACCEPTED" and note_rec["link_status"] == "ACCEPTED"
    assert csv_rec["cluster_id"] == note_rec["cluster_id"]
    assert any("SCOPED_REF:NATIONAL_ID:aadhaar" in code for code in csv_rec["reason_codes"])


# --- Finding 2: FHIR subject reference establishes patient association -----

def test_fhir_encounter_and_observation_join_their_referenced_patients(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    pA, eA, pB, oB = _records_in_order(plan.private, "fhir_patient_link/two_patients_bundle.json")
    assert eA["link_status"] == "ACCEPTED" and eA["cluster_id"] == pA["cluster_id"]
    assert oB["link_status"] == "ACCEPTED" and oB["cluster_id"] == pB["cluster_id"]
    assert pA["cluster_id"] != pB["cluster_id"]  # two distinct patients, never cross-linked


def test_fhir_performer_reference_never_merges_into_the_practitioner(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    practitioner, observation = _records_in_order(plan.private, "fhir_patient_link/performer_not_patient.json")
    assert observation["cluster_id"] != practitioner["cluster_id"]


def test_fhir_ambiguous_subject_reference_is_held_explicitly(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    pX, pY, obs = _records_in_order(plan.private, "fhir_patient_link/ambiguous_subject_bundle.json")
    assert obs["link_status"] == "HELD"
    assert "FHIR_SUBJECT_REFERENCE_AMBIGUOUS" in obs["reason_codes"]
    assert obs["cluster_id"] != pX["cluster_id"]
    assert obs["cluster_id"] != pY["cluster_id"]


def test_fhir_duplicate_logical_id_target_does_not_resolve_arbitrarily(fixtures_dev: Path):
    plan = run_pipeline(fixtures_dev, DEV_CONFIG)
    alpha, beta, enc = _records_in_order(plan.private, "fhir_patient_link/duplicate_logical_id_bundle.json")
    assert enc["cluster_id"] != alpha["cluster_id"]
    assert enc["cluster_id"] != beta["cluster_id"]


# --- Safety: a raw filename must never leak into the safe report -----------

def test_raw_filename_does_not_leak_into_safe_report_via_a_reason_code(tmp_path: Path):
    """Direct reproduction: two matching UHID rows in an unconfigured file
    named with an identifying canary string previously produced the public
    reason code "SCOPED_REF:UNKNOWN_SOURCE:<the whole filename>:uhid",
    which flows straight into the SAFE report's reason_code_tally."""
    import json

    canary = "SYNTHETIC_CANARY_PATIENT_9988776655"
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / f"{canary}.csv").write_text(
        "Patient Name,UHID\nAsha Rao,UH900\nNeha Rao,UH900\n", encoding="utf-8",
    )
    plan = run_pipeline(in_dir, AppConfig())
    assert canary not in json.dumps(plan.safe_report)
    # the match must still have actually happened (not silently dropped)
    assert plan.safe_report["linking"]["status_counts"].get("ACCEPTED", 0) == 2
    for code in plan.safe_report["linking"]["reason_code_tally"]:
        assert canary not in code
        assert ".csv" not in code
