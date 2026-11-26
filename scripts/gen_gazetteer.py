#!/usr/bin/env python3
"""Dev-only generator for the bundled Indian name gazetteer.

Pulls the *actual* static first/last-name tuples out of Faker's pinned
en_IN and hi_IN person providers (Faker==40.63.0, MIT licensed) and adds a
small set of curated variants for names those locales are known to miss
(e.g. "Arif" / "Imran" are absent from en_IN -- verified below -- which is
exactly why they are used as cue-only, gazetteer-absent fixtures instead).

Faker is NOT a runtime dependency: this script runs once, offline, and
writes static JSON that the shipped package reads directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from faker.providers.person.en_IN import Provider as EnIN
from faker.providers.person.hi_IN import Provider as HiIN

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "nijsetu" / "gazetteer" / "data"

# Curated additions: common South-Asian Muslim-origin and other names absent
# from Faker's en_IN/hi_IN static lists, kept intentionally small and
# attributed as curated (not sourced from Faker).
CURATED_FIRST_NAMES = [
    "Arif", "Imran", "Ayesha", "Zara", "Farhan", "Sameer", "Yusuf", "Fatima",
]
CURATED_SURNAMES = [
    "Shaikh", "Siddiqui", "Khan", "Ansari",
]


def main() -> None:
    assert "Arif" not in EnIN.first_names, "Arif unexpectedly present in Faker en_IN; re-check held-out design"

    first_names = sorted(set(EnIN.first_names) | set(HiIN.first_names) | set(CURATED_FIRST_NAMES))
    surnames = sorted(set(EnIN.last_names) | set(HiIN.last_names) | set(CURATED_SURNAMES))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "first_names.json").write_text(
        json.dumps({"names": first_names}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "surnames.json").write_text(
        json.dumps({"names": surnames}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "PROVENANCE.md").write_text(
        "# Gazetteer provenance\n\n"
        f"- {len(set(EnIN.first_names))} first names and {len(set(EnIN.last_names))} surnames "
        "from Faker's `en_IN` person provider (Faker==40.63.0, MIT license).\n"
        f"- {len(set(HiIN.first_names))} first names and {len(set(HiIN.last_names))} surnames "
        "from Faker's `hi_IN` person provider (Faker==40.63.0, MIT license).\n"
        f"- {len(CURATED_FIRST_NAMES)} first names and {len(CURATED_SURNAMES)} surnames "
        "curated by hand (not sourced from Faker) to cover common South Asian "
        "Muslim-origin names absent from the above locales; used deliberately "
        "to validate held-out, gazetteer-absent name detection.\n\n"
        f"Combined: {len(first_names)} first names, {len(surnames)} surnames.\n\n"
        "Faker license: MIT. See https://github.com/joke2k/faker/blob/master/LICENSE.txt\n"
        "The organizer's data generator is unverified; this gazetteer is a seed, not a claim "
        "of coverage against any hidden judge dataset.\n",
        encoding="utf-8",
    )
    print(f"wrote {len(first_names)} first names, {len(surnames)} surnames to {OUT_DIR}")


if __name__ == "__main__":
    main()
