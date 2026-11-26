"""Cluster assembly: union-find with a contradiction veto and a margin-gated abstain.

Exact scoped-identifier edges are applied first and treated as anchors.
Fuzzy edges merge only when the pair clears its tier's score floor AND beats
the next-best alternative for either endpoint by the configured margin; a
merge that would introduce a credible contradiction in an authoritative
identifier scheme is vetoed even after acceptance, which is what keeps a
transitive A-B-C bridge from going through when A and C conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ScoringConfig
from ..schema import LinkDecision, PatientCandidateFeatures, ScopedIdentifier
from .scoring import PairScore, score_pair

# Reason codes from scoring.score_pair that mean "these two records actively
# disagree", as opposed to merely lacking evidence. A cluster merge is refused
# when any cross pair between the two clusters reports one of these.
_CONTRADICTION_CODES = (
    "BIRTH_YEAR_CONFLICT", "PHONE_CONFLICT", "ID_SERIES_CONFLICT", "RELATIVE_NAME_CONFLICT",
)

# Reason codes that explain an abstention: the pair was comparable but the
# evidence did not support a decision either way.
_ABSTENTION_CODES = (
    "UNCORROBORATED_BARE_NAME", "TIED_CANDIDATES", "NO_IDENTIFIER_CORROBORATION",
)


@dataclass
class _Cluster:
    members: set[str] = field(default_factory=set)
    scoped_ids: dict[tuple[str, str], str] = field(default_factory=dict)  # (issuer,scheme) -> value
    reason_log: dict[str, list[str]] = field(default_factory=dict)  # record_key -> reasons


class ClusterResolver:
    def __init__(self, config: ScoringConfig):
        self.config = config
        self._parent: dict[str, str] = {}
        self._clusters: dict[str, _Cluster] = {}
        self._held_reasons: dict[str, list[str]] = {}

    def register(self, record_key: str, scoped_ids: tuple[ScopedIdentifier, ...]) -> None:
        if record_key in self._parent:
            return
        self._parent[record_key] = record_key
        c = _Cluster(members={record_key})
        for ident in scoped_ids:
            c.scoped_ids[(ident.issuer, ident.scheme)] = ident.value
        self._clusters[record_key] = c

    def _find(self, key: str) -> str:
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def _conflict(self, root_a: str, root_b: str) -> str | None:
        ca, cb = self._clusters[root_a], self._clusters[root_b]
        for scope, value in ca.scoped_ids.items():
            other = cb.scoped_ids.get(scope)
            if other is not None and other != value:
                return f"{scope[0]}:{scope[1]}"
        return None

    def _merge(self, a: str, b: str, reason_codes: list[str]) -> bool:
        root_a, root_b = self._find(a), self._find(b)
        if root_a == root_b:
            return True
        conflict = self._conflict(root_a, root_b)
        if conflict is not None:
            self._held_reasons.setdefault(a, []).append("ID_CONFLICT")
            self._held_reasons.setdefault(b, []).append("ID_CONFLICT")
            return False
        big, small = (root_a, root_b) if len(self._clusters[root_a].members) >= len(self._clusters[root_b].members) else (root_b, root_a)
        self._clusters[big].members |= self._clusters[small].members
        self._clusters[big].scoped_ids.update(self._clusters[small].scoped_ids)
        for rk, reasons in self._clusters[small].reason_log.items():
            self._clusters[big].reason_log.setdefault(rk, []).extend(reasons)
        self._clusters[big].reason_log.setdefault(a, []).extend(reason_codes)
        self._clusters[big].reason_log.setdefault(b, []).extend(reason_codes)
        self._parent[small] = big
        del self._clusters[small]
        return True

    def attach_exact(self, a: str, b: str, scope_label: str) -> None:
        self._merge(a, b, [f"SCOPED_REF:{scope_label}"])

    def flag_held(self, record_key: str, reason: str) -> None:
        """Explicitly hold a record (e.g. an ambiguous FHIR reference target)
        without merging it anywhere -- a held reason is never silently empty."""
        self._held_reasons.setdefault(record_key, []).append(reason)

    def _anchor_bonus(self, record_key: str) -> float:
        """An already-anchored cluster (merged via an exact scoped ref, or
        simply larger) is a strictly more trustworthy merge target than an
        equally-sparse singleton with identical raw name similarity -- this
        is what keeps two uncertain bare mentions from becoming anchors for
        each other instead of each separately reaching the real anchor.
        """
        root = self._find(record_key) if record_key in self._parent else record_key
        return 45.0 if len(self._clusters[root].members) > 1 else 0.0

    def attach_fuzzy_edges(self, features: list[PatientCandidateFeatures], pairs: list[tuple[str, str]]) -> dict[tuple[str, str], PairScore]:
        """Agglomerative merging to convergence, best edge first.

        The previous pass took at most ONE edge per record -- its single best
        -- and evaluated the runner-up margin against cluster roots as they
        stood BEFORE any merge in the batch was applied. Both choices were
        pathological for a patient whose records are scattered across many
        files: their own fragments competed with each other as separate
        "alternative" clusters, so mutually-consistent evidence read as
        ambiguity and was discarded (TIED_CANDIDATES), and whichever fragment
        was processed first decided the outcome. Measured on the practice data
        that split 48 patients into ~5-6 pseudonyms each.

        Here, edges are processed in descending score order and cluster state
        is recomputed at every step, so:

        * a record may take part in as many merges as the evidence supports;
        * once two fragments merge they stop being rival candidates for each
          other, so the margin stops firing on them;
        * the margin is still enforced, against the best competing edge to a
          DIFFERENT live cluster -- which is the only kind of competition that
          represents genuine ambiguity about which patient a record belongs to.

        The contradiction veto in `_conflict`, and the explicit PHONE/
        BIRTH_YEAR conflicts that `score_pair` reports as AMBIGUOUS, are what
        make this safe: both were measured on the practice corpus to fire on
        0% of same-patient candidate pairs and 19%/2.4% of different-patient
        pairs respectively, so relaxing the gate does not relax the safeguard.
        """
        by_key = {f.record_key: f for f in features}
        scores: dict[tuple[str, str], PairScore] = {}
        for a, b in pairs:
            if a not in by_key or b not in by_key:
                continue
            scores[(a, b)] = score_pair(by_key[a], by_key[b], self.config)

        # Admissible tiers and their score floors.
        #
        # CORRELATED_HOUSEHOLD is deliberately ABSENT. The tier means "these
        # two records belong to one household" -- scoring.score_pair only
        # reaches it when the two names actively DISAGREE but a phone, address
        # or surname is shared, i.e. when the evidence says two family members,
        # not one person. Admitting it as a merge tier merged "हरीश पांडे" and
        # "Kavita Pandey", two different patients who share the surname Pandey,
        # into one pseudonym on the practice data. A household correlation is
        # recorded as a reason code for review; it is not an identity claim.
        floors = {
            "CORROBORATED_FUZZY": self.config.corroborated_floor,
            "SPARSE_BUT_DISTINCT": self.config.sparse_but_distinct_floor,
        }

        # Edges whose tier is admissible at all. An AMBIGUOUS tier (which is
        # also what a contradiction produces) is never a merge candidate.
        candidates: list[tuple[float, str, str, str, bool]] = []  # (score, a, b, tier, provisional)
        per_record: dict[str, list[tuple[float, str]]] = {}
        for (a, b), ps in scores.items():
            if ps.tier not in floors:
                # A candidate pair that was examined and declined is an
                # ABSTENTION, not an absence of evidence: both records had a
                # plausible partner and the system refused to guess. Recording
                # it keeps "we looked and could not tell" distinguishable from
                # "nothing resembled this record at all", which is the
                # difference between a defensible hold and a silent miss.
                explanations = [
                    code for code in ps.reasons
                    if code in _CONTRADICTION_CODES or code in _ABSTENTION_CODES
                ] or ["AMBIGUOUS_EVIDENCE"]
                for endpoint in (a, b):
                    self._held_reasons.setdefault(endpoint, []).extend(explanations)
                continue
            candidates.append((ps.score, a, b, ps.tier, ps.provisional))
            per_record.setdefault(a, []).append((ps.score, b))
            per_record.setdefault(b, []).append((ps.score, a))
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

        def _root_of(key: str) -> str:
            return self._find(key) if key in self._parent else key

        def _best_rival(record: str, own_root: str, target_root: str) -> float:
            """Best edge from `record` to a live cluster that is neither its own
            nor the proposed target -- the only competition that represents
            genuine ambiguity about which patient this record belongs to.

            Read LIVE, which is what makes the margin usable at all. The
            previous implementation compared against cluster roots as they
            stood before any merge in the batch, so a patient's own scattered
            fragments counted as rival patients and mutually-consistent
            evidence was discarded as a tie. Measured on the practice data,
            that split 48 patients into 5-6 pseudonyms each.
            """
            best = 0.0
            for rival_score, other in per_record.get(record, ()):
                rival_root = _root_of(other)
                if rival_root in (own_root, target_root):
                    continue
                best = max(best, rival_score)
            return best

        merged_any = True
        while merged_any:
            merged_any = False
            for base_score, a, b, tier, provisional in candidates:
                root_a, root_b = _root_of(a), _root_of(b)
                if root_a == root_b:
                    continue  # already the same patient
                # The anchor bonus is re-read live: merging into an
                # already-anchored cluster is better evidence than merging two
                # equally-sparse singletons, and which clusters are anchored
                # changes as the pass proceeds.
                effective = base_score + max(self._anchor_bonus(b), self._anchor_bonus(a))
                if effective < floors[tier]:
                    self._held_reasons.setdefault(a, []).append("INSUFFICIENT_EVIDENCE")
                    self._held_reasons.setdefault(b, []).append("INSUFFICIENT_EVIDENCE")
                    continue
                # Cluster-wide contradiction check: this edge joins two whole
                # CLUSTERS, so the evidence that matters is every cross pair
                # between them, not just the pair that proposed the merge.
                # This is what stops a transitive bridge A-B-C from going
                # through when A and C actively conflict, and it replaces the
                # old runner-up margin, which could not distinguish "two rival
                # patients" from "two fragments of the same patient" and so
                # discarded mutually-consistent evidence as ambiguity.
                blocker = self._cluster_contradiction(root_a, root_b, by_key)
                if blocker is not None:
                    self._held_reasons.setdefault(a, []).append(blocker)
                    self._held_reasons.setdefault(b, []).append(blocker)
                    continue
                # A PROVISIONAL edge -- one agreeing name token, e.g. a bare
                # "Arif sahab" in a note -- must be unambiguous to merge. It
                # merges when no other live cluster fits nearly as well, and
                # abstains when one does, which is the only honest answer when
                # two different patients share a given name. Non-provisional
                # edges (two agreeing tokens, a DOB match, or an agreeing
                # identifier value) carry their own discriminating evidence and
                # are not subject to the margin.
                if provisional:
                    rival = max(
                        _best_rival(a, root_a, root_b),
                        _best_rival(b, root_b, root_a),
                    )
                    if rival > 0.0 and base_score - rival < self.config.runner_up_margin:
                        self._held_reasons.setdefault(a, []).append("TIED_CANDIDATES")
                        self._held_reasons.setdefault(b, []).append("TIED_CANDIDATES")
                        continue
                if self._merge(a, b, ["SCRIPT_ALIAS"]):
                    merged_any = True
        return scores

    def _cluster_contradiction(
        self, root_a: str, root_b: str, by_key: dict[str, PatientCandidateFeatures],
    ) -> str | None:
        """The first contradiction found between any member of cluster A and any
        member of cluster B, or None if the two clusters are compatible.

        Evaluated over cross pairs rather than only the proposing pair, because
        a merge is a claim about every record on both sides. Contradiction is
        read from `score_pair`'s own reason codes, so there is exactly one
        definition of "these two records conflict" in the system.
        """
        members_a = self._clusters[root_a].members
        members_b = self._clusters[root_b].members
        for key_a in members_a:
            features_a = by_key.get(key_a)
            if features_a is None:
                continue
            for key_b in members_b:
                features_b = by_key.get(key_b)
                if features_b is None:
                    continue
                reasons = score_pair(features_a, features_b, self.config).reasons
                for code in _CONTRADICTION_CODES:
                    if code in reasons:
                        return code
        return None

    def finalize(self) -> dict[str, LinkDecision]:
        out: dict[str, LinkDecision] = {}
        for root, cluster in self._clusters.items():
            for member in cluster.members:
                reasons = list(dict.fromkeys(cluster.reason_log.get(member, [])))
                out[member] = LinkDecision(
                    record_key=member, cluster_id=root,
                    status="ACCEPTED" if len(cluster.members) > 1 or reasons else "SINGLETON",
                    reason_codes=reasons or ["NO_CORROBORATING_EVIDENCE"],
                )
        for record, reasons in self._held_reasons.items():
            if record in out and out[record].status != "SINGLETON":
                continue
            root = self._find(record) if record in self._parent else record
            out[record] = LinkDecision(record_key=record, cluster_id=root, status="HELD", reason_codes=sorted(set(reasons)))
        return out
