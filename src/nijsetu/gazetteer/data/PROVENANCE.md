# Gazetteer provenance

- 531 first names and 538 surnames from Faker's `en_IN` person provider (Faker==40.63.0, MIT license).
- 335 first names and 102 surnames from Faker's `hi_IN` person provider (Faker==40.63.0, MIT license).
- 8 first-name entries and 4 surname entries curated by hand (not sourced from Faker) to supplement the above locales with common South Asian Muslim-origin names and variants. These entries are included in the bundled gazetteer; they are not gazetteer-absent test names. Duplicates across sources are removed in the combined lists.

Combined: 873 first names, 644 surnames.

The gazetteer-absent fixtures use separate names, such as `Zufishan`, in
`tests/fixtures/heldout/gazetteer_absent/`. Regenerate the data and this notice
with `python scripts/gen_gazetteer.py` after installing `.[dev]` (Faker is a
development dependency, not a runtime dependency).

Faker license: MIT. See https://github.com/joke2k/faker/blob/master/LICENSE.txt
The organizer's data generator is unverified; this gazetteer is a seed, not a claim of coverage against any hidden judge dataset.
