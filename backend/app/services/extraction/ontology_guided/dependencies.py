"""Proof dependency graph with AND inputs and alternative OR proofs."""

from __future__ import annotations

from collections import defaultdict, deque


class DependencyIndex:
    def __init__(self):
        self.requirements: dict[str, list[frozenset[str]]] = defaultdict(list)
        self.dependents: dict[str, set[str]] = defaultdict(set)
        self.invalidated: set[str] = set()
        self.subscriptions: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    def add_proof(self, claim_id: str, dependency_ids: list[str]) -> None:
        proof = frozenset(dependency_ids)
        if proof not in self.requirements[claim_id]:
            self.requirements[claim_id].append(proof)
        for dependency in proof:
            self.dependents[dependency].add(claim_id)

    def subscribe(
        self,
        claim_id: str,
        *,
        record_or_group: str,
        owner_role: str,
        applicability: str,
    ) -> None:
        self.subscriptions[(record_or_group, owner_role, applicability)].add(claim_id)

    def notify_competitor(
        self, *, record_or_group: str, owner_role: str, applicability: str
    ) -> set[str]:
        affected = set(self.subscriptions.get((record_or_group, owner_role, applicability), set()))
        changed: set[str] = set()
        for claim in sorted(affected):
            changed.update(self.invalidate(claim))
        return changed

    def invalidate(self, dependency_id: str) -> set[str]:
        changed: set[str] = {dependency_id}
        self.invalidated.add(dependency_id)
        queue = deque([dependency_id])
        while queue:
            dependency = queue.popleft()
            for claim in self.dependents.get(dependency, set()):
                # A claim remains valid if at least one complete alternative proof
                # has no invalidated dependency.
                alternatives = self.requirements.get(claim, [])
                if any(not (proof & self.invalidated) for proof in alternatives):
                    continue
                if claim not in self.invalidated:
                    self.invalidated.add(claim)
                    changed.add(claim)
                    queue.append(claim)
        return changed

    def is_valid(self, claim_id: str) -> bool:
        if claim_id in self.invalidated:
            return False
        alternatives = self.requirements.get(claim_id)
        return not alternatives or any(not (proof & self.invalidated) for proof in alternatives)

    def restore(self, claim_id: str) -> bool:
        """Restore only this binding after a new complete alternative proof.

        Descendants keep their old invalidation until their own bounded tasks
        actually recheck the new semantic dependency. This is not undo.
        """
        if claim_id not in self.invalidated:
            return False
        alternatives = self.requirements.get(claim_id, [])
        if not any(not (proof & self.invalidated) for proof in alternatives):
            return False
        self.invalidated.remove(claim_id)
        return True

    def snapshot(self) -> dict:
        return {
            "requirements": {
                claim: [sorted(proof) for proof in proofs]
                for claim, proofs in sorted(self.requirements.items())
            },
            "invalidated": sorted(self.invalidated),
            "subscriptions": {
                "|".join(key): sorted(value) for key, value in sorted(self.subscriptions.items())
            },
        }

    @classmethod
    def from_snapshot(cls, raw: dict | None) -> "DependencyIndex":
        """Rebuild the derived lookup tables from a durable, versioned snapshot."""
        index = cls()
        if not raw:
            return index
        requirements = raw.get("requirements") or {}
        if not isinstance(requirements, dict):
            raise ValueError("dependency requirements must be an object")
        for claim_id, alternatives in requirements.items():
            if not isinstance(claim_id, str) or not isinstance(alternatives, list):
                raise ValueError("invalid dependency requirement")
            for dependency_ids in alternatives:
                if not isinstance(dependency_ids, list) or not all(
                    isinstance(item, str) for item in dependency_ids
                ):
                    raise ValueError("invalid dependency proof")
                index.add_proof(claim_id, dependency_ids)
        invalidated = raw.get("invalidated") or []
        if not isinstance(invalidated, list) or not all(
            isinstance(item, str) for item in invalidated
        ):
            raise ValueError("invalid dependency invalidation list")
        index.invalidated.update(invalidated)
        subscriptions = raw.get("subscriptions") or {}
        if not isinstance(subscriptions, dict):
            raise ValueError("dependency subscriptions must be an object")
        for encoded_key, claim_ids in subscriptions.items():
            if not isinstance(encoded_key, str) or not isinstance(claim_ids, list):
                raise ValueError("invalid dependency subscription")
            key = tuple(encoded_key.split("|", 2))
            if len(key) != 3 or not all(isinstance(item, str) for item in claim_ids):
                raise ValueError("invalid dependency subscription")
            index.subscriptions[key].update(claim_ids)
        return index
