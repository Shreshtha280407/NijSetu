"""Per-feature action decisions: the field/action plan stage.

Turns one detected Feature into a schema.Action (private evidence + safe
aggregate counts) plus the actual replacement text, given the active policy,
the owning patient's context (token/date-shift/age-band), and the release key
for tokenization. Shared by every format's transformer (CSV/JSON/TXT; FHIR
reuses it only for free-text-shaped sub-fields like reference display
strings -- structured FHIR elements are rewritten directly, see fhir_rewrite.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schema import Action, Feature, FieldRole
from . import keys
from .dates import DateParseError, parse_flexible_date, shift_date_text
from .patient_context import PatientContext
from .policy import GENERALIZE, MASK_SPAN, PolicyConfig, RETAIN, SHIFT_DATE, SUPPRESS, TOKENIZE

# Coarse-geography schemes that survive the small-cohort widening step: the
# district (and state) level is the declared retention floor under
# `geography_level: district`.
_RETAINED_COARSE_SCHEMES = frozenset({"district"})

_MASK_MARKERS: dict[FieldRole, str] = {
    FieldRole.PATIENT_NAME: "[REDACTED_NAME]",
    FieldRole.RELATIVE_NAME: "[REDACTED_NAME]",
    FieldRole.PROVIDER_NAME: "[REDACTED_PROVIDER]",
    FieldRole.FACILITY_NAME: "[REDACTED_FACILITY]",
    FieldRole.PATIENT_IDENTIFIER: "[REDACTED_ID]",
    FieldRole.CONTACT: "[REDACTED_CONTACT]",
    FieldRole.FINE_ADDRESS: "[REDACTED_ADDRESS]",
}


@dataclass
class ActionResult:
    action: Action
    replacement_text: str
    omit_field: bool = False  # whole element should be dropped (SUPPRESS of an optional field), not merely replaced


def decide_feature_action(
    feature: Feature,
    policy: PolicyConfig,
    patient_ctx: PatientContext,
    release_key: bytes,
    target_label: str,
    *,
    allow_age_band_text: bool = True,
) -> ActionResult:
    role = feature.role

    if role == FieldRole.DATE:
        # A DOB and a stated age are the same decision: publish the patient's
        # age BAND, or nothing. Neither is ever shifted like an event date.
        if feature.scheme in ("dob", "age"):
            if allow_age_band_text and patient_ctx.age_band is not None:
                action = Action(target=target_label, kind=GENERALIZE, rule_id="DOB_TO_AGE_BAND", confidence_tier=feature.confidence_tier)
                return ActionResult(action, patient_ctx.age_band)
            reason = "FHIR_BIRTHDATE_TYPE" if not allow_age_band_text else patient_ctx.age_band_reason
            action = Action(target=target_label, kind=SUPPRESS, rule_id=f"DOB_SUPPRESSED_{reason}")
            return ActionResult(action, "", omit_field=True)
        try:
            parsed = parse_flexible_date(feature.original_text, feature.date_convention)
        except DateParseError as exc:
            action = Action(target=target_label, kind=SUPPRESS, rule_id=f"DATE_HELD_{exc.reason_code}")
            return ActionResult(action, "", omit_field=True)
        shifted = shift_date_text(parsed, patient_ctx.date_shift_days)
        action = Action(target=target_label, kind=SHIFT_DATE, rule_id="DATE_SHIFTED", confidence_tier=feature.confidence_tier)
        return ActionResult(action, shifted)

    kind = policy.role_action(role, scheme=feature.scheme)
    # The small-cohort ladder widens a patient's geography by ONE step: the
    # finer coarse levels (tehsil/block/subdivision) go, the district stays.
    # Suppressing every coarse field instead would discard the district --
    # the level the active policy declares it retains, and one of the values
    # the clinical-utility score expects to find -- while buying almost no
    # privacy, since a district holds millions of people and every FINE
    # component is already suppressed unconditionally above.
    if role == FieldRole.COARSE_GEOGRAPHY and patient_ctx.suppress_geography and feature.scheme not in _RETAINED_COARSE_SCHEMES:
        kind = SUPPRESS

    if kind == MASK_SPAN:
        marker = _MASK_MARKERS.get(role, "[REDACTED]")
        action = Action(target=target_label, kind=MASK_SPAN, rule_id=f"MASK_{role.value}", confidence_tier=feature.confidence_tier)
        return ActionResult(action, marker)

    if kind == TOKENIZE:
        # issuer scopes the token's HMAC namespace (sanitize.py resolves and
        # fills in feature.issuer for every local-scheme identifier before
        # transform runs) -- two different hospitals' coincidentally-
        # identical local IDs must never collapse onto the same public token.
        token = keys.identifier_token(release_key, feature.scheme or "identifier", feature.original_text.strip(), issuer=feature.issuer)
        action = Action(
            target=target_label, kind=TOKENIZE, rule_id="IDENTIFIER_TOKENIZED",
            replacement_ref=token, confidence_tier=feature.confidence_tier,
        )
        return ActionResult(action, token)

    if kind == SUPPRESS:
        action = Action(target=target_label, kind=SUPPRESS, rule_id=f"SUPPRESS_{role.value}", confidence_tier=feature.confidence_tier)
        return ActionResult(action, "", omit_field=True)

    # GENERALIZE with no implemented coarsening (provider/facility override)
    # and the plain RETAIN default both keep the original text.
    kind = kind if kind in (GENERALIZE, RETAIN) else RETAIN
    action = Action(
        target=target_label, kind=RETAIN if kind == RETAIN else GENERALIZE,
        rule_id="RETAIN", confidence_tier=feature.confidence_tier,
    )
    return ActionResult(action, feature.original_text)
