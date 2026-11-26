"""Unit tests: policy defaults/loading (phase2.policy) and patient-level
risk analysis + the small-cohort widening ladder (phase2.risk)."""

from __future__ import annotations

import json

import pytest

from nijsetu.phase2 import risk
from nijsetu.phase2.patient_context import PatientContext
from nijsetu.phase2.policy import BALANCED, STRICT, MASK_SPAN, PolicyError, SUPPRESS, TOKENIZE, load_policy
from nijsetu.schema import FieldRole


def test_balanced_and_strict_presets_differ():
    assert BALANCED.k_threshold != STRICT.k_threshold
    assert BALANCED.geography_level != STRICT.geography_level


def test_role_action_direct_identifiers_always_masked_regardless_of_policy():
    for policy in (BALANCED, STRICT):
        assert policy.role_action(FieldRole.PATIENT_NAME) == MASK_SPAN
        assert policy.role_action(FieldRole.RELATIVE_NAME) == MASK_SPAN
        assert policy.role_action(FieldRole.CONTACT) == SUPPRESS
        assert policy.role_action(FieldRole.FINE_ADDRESS) == SUPPRESS


def test_role_action_national_identifier_masked_not_tokenized():
    assert BALANCED.role_action(FieldRole.PATIENT_IDENTIFIER, scheme="aadhaar") == MASK_SPAN
    assert BALANCED.role_action(FieldRole.PATIENT_IDENTIFIER, scheme="uhid") == TOKENIZE


def test_age_band_default_and_top_band():
    assert BALANCED.age_band.band_for(5) == "0-9"
    assert BALANCED.age_band.band_for(45) == "40-49"
    assert BALANCED.age_band.band_for(91) == "90+"
    assert BALANCED.age_band.band_for(150) == "90+"


def test_load_policy_unknown_name_raises():
    with pytest.raises(PolicyError) as exc:
        load_policy("nonexistent")
    assert exc.value.reason_code == "UNKNOWN_POLICY"


def test_load_policy_config_override(tmp_path):
    cfg = tmp_path / "override.json"
    cfg.write_text(json.dumps({"k_threshold": 7, "age_band": {"band_width": 5, "top_band_start": 95}}))
    policy = load_policy("balanced", cfg)
    assert policy.k_threshold == 7
    assert policy.age_band.band_width == 5
    assert policy.age_band.top_band_start == 95
    assert policy.name == "balanced"  # unmodified fields retain the preset


def test_load_policy_rejects_invalid_json(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not valid json")
    with pytest.raises(PolicyError) as exc:
        load_policy("balanced", cfg)
    assert exc.value.reason_code == "CONFIG_FILE_INVALID_JSON"


def _ctx(cluster_id: str, age_band=None, ref_date=None) -> PatientContext:
    return PatientContext(
        cluster_id=cluster_id, canonical_key=cluster_id, patient_token=f"TOK-{cluster_id}",
        date_shift_days=0, reference_event_date=ref_date, age_band=age_band, age_band_reason="DERIVED",
    )


def test_risk_counts_singletons_and_small_groups_over_distinct_patients_not_records():
    import datetime
    clusters = {"p1": ["0:0", "0:1"], "p2": ["1:0"], "p3": ["2:0"]}  # p1 has 2 records, same patient
    record_features: dict[str, list] = {k: [] for rk in clusters.values() for k in rk}
    contexts = {
        "p1": _ctx("p1", age_band="30-39", ref_date=datetime.date(2024, 1, 1)),
        "p2": _ctx("p2", age_band="30-39", ref_date=datetime.date(2024, 1, 1)),
        "p3": _ctx("p3", age_band="70-79", ref_date=datetime.date(2024, 6, 1)),
    }
    sigs = risk.build_signatures(clusters, record_features, contexts)
    assert len(sigs) == 3  # one signature per PATIENT, not per record
    report = risk.analyze_risk(sigs, k_threshold=3)
    assert report.total_patients == 3
    # p1 and p2 share (age_band, geography=None, visit_period) -> group of 2; p3 alone -> singleton
    assert report.singleton_count == 1
    assert report.small_group_count == 3  # both groups (<3) are small


def test_escalation_ladder_never_holds_by_default_even_for_tiny_cohort():
    clusters = {f"p{i}": [f"{i}:0"] for i in range(3)}
    record_features = {f"{i}:0": [] for i in range(3)}
    import datetime
    contexts = {f"p{i}": _ctx(f"p{i}", age_band=f"{i}0-{i}9", ref_date=datetime.date(2024, 1, i + 1)) for i in range(3)}
    sigs = risk.build_signatures(clusters, record_features, contexts)
    widened, held = risk.escalate_for_small_groups(sigs, BALANCED)
    assert held == set()  # hold_distinctive_singletons is off by default
    assert len(widened) == 3


def test_escalation_ladder_widens_then_optionally_holds_distinctive_singleton():
    from dataclasses import replace

    clusters = {"p0": ["0:0"], "p1": ["1:0"], "p2": ["2:0"]}
    record_features = {
        "0:0": [_mk_feature(FieldRole.CLINICAL_HISTORY, "rare_disease_x")],
        "1:0": [_mk_feature(FieldRole.CLINICAL_HISTORY, "common_cold")],
        "2:0": [_mk_feature(FieldRole.CLINICAL_HISTORY, "common_cold")],
    }
    import datetime
    contexts = {
        "p0": _ctx("p0", age_band="90+", ref_date=datetime.date(2024, 1, 1)),
        "p1": _ctx("p1", age_band="20-29", ref_date=datetime.date(2024, 1, 1)),
        "p2": _ctx("p2", age_band="20-29", ref_date=datetime.date(2024, 1, 2)),
    }
    sigs = risk.build_signatures(clusters, record_features, contexts)
    strict_opt_in = replace(BALANCED, hold_distinctive_singletons=True, k_threshold=3)
    widened, held = risk.escalate_for_small_groups(sigs, strict_opt_in)
    assert "p0" in held  # unique age band + release-wide-rare diagnosis
    assert "p1" not in held  # common diagnosis term, not held


def _mk_feature(role, text):
    from nijsetu.schema import Feature, SourceCoordinate

    src = SourceCoordinate(file_no=0, file_path="x", record_no=0, path="body")
    return Feature(source=src, role=role, char_start=0, char_end=len(text), original_text=text)
