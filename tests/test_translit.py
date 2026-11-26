"""Cross-script normalization: transliteration, alias families, skeletons, similarity."""

from nijsetu.translit import (
    consonant_skeleton,
    normalize_alias_family,
    token_sequence_similarity,
    tokenize_name,
    vowel_collapsed,
)
from nijsetu.translit.devanagari import transliterate


def test_devanagari_transliteration_matches_expected_forms():
    assert transliterate("मोहम्मद") == "mohammad"
    assert transliterate("आरिफ़") == "aarif"
    assert transliterate("मीरा") == "miiraa"


def test_mohammad_alias_family_normalized():
    for variant in ("Mohammad", "Mohammed", "Muhammad", "Mohd", "Mohd.", "Md", "Md."):
        assert normalize_alias_family(variant) == "mohammad"
    assert normalize_alias_family("Ramesh") == "ramesh"


def test_md_as_medical_abbreviation_not_confused_in_isolation():
    # normalize_alias_family is a pure per-token mapper; context decisions
    # (name-position vs degree) live in detect.names. Here we only check the
    # mapping itself is total and doesn't explode on an unrelated token.
    assert normalize_alias_family("Cardiology") == "cardiology"


def test_arif_family_skeletons_match_across_scripts_and_abbreviations():
    variants = ["Mohammad Arif", "मोहम्मद आरिफ़", "Md. Arif", "Aarif"]
    skeletons = set()
    for v in variants:
        tokens = tokenize_name(v)
        skeletons.add(consonant_skeleton(tokens[-1]))
    assert skeletons == {"rf"}


def test_meera_family_skeletons_match():
    for v in ["Meera", "मीरा", "Mira"]:
        assert consonant_skeleton(tokenize_name(v)[0]) == "mr"


def test_unrelated_names_can_share_a_skeleton_but_tokens_differ():
    # "Arif" and "Oorf" would both skeletonize to "rf" -- a deliberate
    # collision used to prove skeleton equality alone never implies identity
    # (that check lives in identity.scoring, not here).
    assert consonant_skeleton("arif") == consonant_skeleton("oorf") == "rf"
    assert tokenize_name("arif") != tokenize_name("oorf")


def test_vowel_collapsed_preserves_syllable_structure():
    assert vowel_collapsed("meera") == vowel_collapsed("mira") == vowel_collapsed("miiraa")


def test_token_sequence_similarity_high_for_alias_variants():
    assert token_sequence_similarity("Mohammad Arif", "Md. Arif") > 80


def test_token_sequence_similarity_low_for_unrelated_names():
    assert token_sequence_similarity("Ramesh Kumar", "Suresh Yadav") < 60


def test_devi_retained_not_stripped_as_honorific():
    assert tokenize_name("Smt. Sita Devi") == ("sita", "devi")
