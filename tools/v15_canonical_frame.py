"""Disconnected exact-key candidate for immutable, versioned configuration frames."""
from bisect import bisect_left
import copy
import hashlib


class CanonicalFrame:
    def __init__(self, base, version):
        if version is None:
            raise ValueError("explicit cumulative-set version required")
        if any(not isinstance(key, str) for key in base):
            raise ValueError("configuration keys must be strings")
        self.version = version
        self._base = copy.deepcopy(base)
        self._pairs = tuple(sorted((key, str(value)) for key, value in self._base.items()))
        self._keys = tuple(key for key, _ in self._pairs)

    def _check(self, version):
        if version != self.version:
            raise ValueError("promotion/default change invalidated canonical frame")

    def canonical(self, patch, *, version):
        self._check(version)
        pairs = list(self._pairs)
        added = []
        for key, value in patch.items():
            if not isinstance(key, str):
                raise ValueError("configuration keys must be strings")
            index = bisect_left(self._keys, key)
            pair = (key, str(value))
            if index < len(self._keys) and self._keys[index] == key:
                pairs[index] = pair
            else:
                added.append(pair)
        if added:
            pairs.extend(added)
            pairs.sort()
        return tuple(pairs)

    def compact_key(self, patch, *, version, window_days, prepared_id):
        canonical = self.canonical(patch, version=version)
        return (hashlib.blake2b(repr(canonical).encode(), digest_size=16).digest(), window_days, prepared_id)

    def task_config(self, patch, *, version):
        self._check(version)
        config = copy.deepcopy(self._base)
        config.update(copy.deepcopy(patch))
        return config
