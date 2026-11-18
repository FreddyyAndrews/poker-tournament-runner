"""Blind structures. Levels advance by hand count (never wall-clock time), so a
run's blinds depend only on its config, not on how fast the bots are."""

from dataclasses import asdict, dataclass

# Base schedule for a 10,000-chip starting stack; presets scale it.
_BASE_LEVELS = [
    (25, 50), (50, 100), (75, 150), (100, 200), (150, 300), (200, 400),
    (300, 600), (400, 800), (500, 1000), (700, 1400), (1000, 2000),
    (1500, 3000), (2000, 4000), (3000, 6000), (4000, 8000), (6000, 12000),
    (8000, 16000), (10000, 20000), (15000, 30000), (20000, 40000),
]
_ANTES_FROM_LEVEL = 5          # 0-based level index where antes start
_BASE_STACK = 10_000

PRESETS = {
    "turbo":    10,   # hands per level
    "standard": 20,
    "slow":     40,
}


@dataclass(frozen=True)
class Level:
    sb: int
    bb: int
    ante: int = 0
    hands: int = 0   # hands at this level; 0 = forever (last level)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Structure:
    name: str
    levels: tuple

    def level_index(self, hand_num: int) -> int:
        """Level in force for hand_num (0-based). After the last listed level,
        blinds keep doubling every len(last level) hands."""
        played = 0
        for i, lvl in enumerate(self.levels):
            if lvl.hands == 0 or hand_num < played + lvl.hands:
                return i
            played += lvl.hands
        last = self.levels[-1]
        return len(self.levels) - 1 + (hand_num - played) // last.hands + 1

    def level(self, hand_num: int) -> Level:
        idx = self.level_index(hand_num)
        if idx < len(self.levels):
            return self.levels[idx]
        last = self.levels[-1]
        k = 2 ** (idx - len(self.levels) + 1)
        return Level(last.sb * k, last.bb * k, last.ante * k, last.hands)

    def to_dict(self):
        return {"name": self.name, "levels": [l.to_dict() for l in self.levels]}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], tuple(Level(**l) for l in d["levels"]))


def _round_chip(x: float) -> int:
    """Round to a tidy chip amount (1, 5, 25, ...-ish granularity)."""
    if x < 1:
        return 1
    for step in (1, 5, 10, 25, 50, 100, 250, 500, 1000):
        if x < step * 20:
            return max(step, int(round(x / step)) * step)
    return int(round(x / 1000)) * 1000


def preset(name: str, starting_stack: int = _BASE_STACK) -> Structure:
    if name not in PRESETS:
        raise ValueError(f"Unknown structure {name!r}; choose from {sorted(PRESETS)} "
                         "or give flat blinds like 50/100")
    hands = PRESETS[name]
    scale = starting_stack / _BASE_STACK
    levels = []
    for i, (sb, bb) in enumerate(_BASE_LEVELS):
        sb_, bb_ = _round_chip(sb * scale), _round_chip(bb * scale)
        sb_ = min(sb_, bb_)
        ante = _round_chip(bb_ / 10) if i >= _ANTES_FROM_LEVEL else 0
        levels.append(Level(sb_, bb_, ante, hands))
    return Structure(name, tuple(levels))


def flat(sb: int, bb: int, ante: int = 0) -> Structure:
    return Structure(f"flat {sb}/{bb}" + (f"/{ante}" if ante else ""),
                     (Level(sb, bb, ante, 0),))


def parse(spec: str, starting_stack: int = _BASE_STACK) -> Structure:
    """'turbo' / 'standard' / 'slow', or flat blinds 'SB/BB' or 'SB/BB/ANTE'."""
    if "/" in spec:
        try:
            parts = [int(p) for p in spec.split("/")]
        except ValueError:
            raise ValueError(f"Bad blinds {spec!r}; expected SB/BB or SB/BB/ANTE")
        if len(parts) not in (2, 3) or not (0 < parts[0] <= parts[1]):
            raise ValueError(f"Bad blinds {spec!r}; need 0 < SB <= BB")
        return flat(*parts)
    return preset(spec, starting_stack)
