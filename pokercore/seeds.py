"""Seed derivation. Every random choice in a run comes from one root seed, so
the same seed replays the same run: run -> match -> hand, plus per-bot seeds."""

import hashlib
import random


def derive_seed(*parts) -> int:
    """Stable 63-bit seed from any parts (same inputs -> same seed)."""
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def new_seed() -> int:
    return random.SystemRandom().randrange(2 ** 31)
