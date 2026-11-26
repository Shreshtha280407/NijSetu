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
        by_key = {f.record_key: f for f in features}
        scores: dict[tuple[str, str], PairScore] = {}
        for a, b in pairs:
            if a not in by_key or b not in by_key:
                continue
            scores[(a, b)] = score_pair(by_key[a], by_key[b], self.config)

        per_record: dict[str, list[tuple[float, str, str]]] = {}  # record -> [(score, other, tier)]
        for (a, b), ps in scores.items():
            per_record.setdefault(a, []).append((ps.score + self._anchor_bonus(b), b, ps.tier))
            per_record.setdefault(b, []).append((ps.score + self._anchor_bonus(a), a, ps.tier))

        floors = {
            "CORROBORATED_FUZZY": self.config.corroborated_floor,
            "SPARSE_BUT_DISTINCT": self.config.sparse_but_distinct_floor,
            "CORRELATED_HOUSEHOLD": self.config.corroborated_floor,
        }

        wanted: list[tuple[float, str, str, str]] = []  # (score, record, other, tier)
        for record, edges in per_record.items():
            edges.sort(key=lambda e: e[0], reverse=True)
            best_score, best_other, best_tier = edges[0]
            floor = floors.get(best_tier, 1000.0)
            if best_score < floor:
                self._held_reasons.setdefault(record, []).append("INSUFFICIENT_EVIDENCE")
                continue
            # Compare by current cluster root, not raw record key: several
            # candidates that already merged (e.g. via an exact scoped ref)
            # are one real competitor, not several near-tied ones.
            best_root = self._find(best_other) if best_other in self._parent else best_other
            runner_up = next(
                (e for e in edges[1:] if (self._find(e[1]) if e[1] in self._parent else e[1]) != best_root),
                None,
            )
            if runner_up is not None and (best_score - runner_up[0]) < self.config.runner_up_margin:
                self._held_reasons.setdefault(record, []).append("TIED_CANDIDATES")
                continue
            wanted.append((best_score, record, best_other, best_tier))

        for score, record, other, tier in sorted(wanted, key=lambda w: w[0], reverse=True):
            reason = "HOUSEHOLD_SHARED" if tier == "CORRELATED_HOUSEHOLD" else "SCRIPT_ALIAS"
            self._merge(record, other, [reason])
        return scores

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
