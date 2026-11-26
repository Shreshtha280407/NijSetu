"""Loads the bundled Indian name gazetteer. No Faker import here -- data is static JSON."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def first_names() -> frozenset[str]:
    return _load("first_names.json")


@lru_cache(maxsize=1)
def surnames() -> frozenset[str]:
    return _load("surnames.json")


def _load(filename: str) -> frozenset[str]:
    raw = resources.files("nijsetu.gazetteer.data").joinpath(filename).read_text(encoding="utf-8")
    data = json.loads(raw)
    return frozenset(name.lower() for name in data["names"])


def is_known_first_name(token: str) -> bool:
    return token.lower() in first_names()


def is_known_surname(token: str) -> bool:
    return token.lower() in surnames()


@lru_cache(maxsize=1)
def _name_skeletons() -> frozenset[str]:
    # Local import: avoids a hard import-time dependency from gazetteer -> translit
    # for callers that only need the exact-match lookups above.
    from ..translit import consonant_skeleton
    return frozenset(consonant_skeleton(n) for n in (first_names() | surnames()))


def skeleton_matches_gazetteer(skeleton: str) -> bool:
    """Coarse cross-script recall check: skeleton equality only, never identity proof."""
    return bool(skeleton) and skeleton in _name_skeletons()
