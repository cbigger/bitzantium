"""
dice.py

All randomness in the engine lives here. Pure functions, no side effects.
"""

import random
import re


def roll(expr: str) -> int:
    """
    Parse and evaluate a dice expression.
    Supports: '2d6', 'd20', '1d8+3', '2d6-1', '5' (flat), 'd20' (implicit 1)
    """
    expr = expr.strip().lower().replace(" ", "")
    m = re.fullmatch(r'(\d*)d(\d+)([+-]\d+)?|(\d+)', expr)
    if not m:
        raise ValueError(f"Cannot parse dice expression: {expr!r}")

    if m.group(4) is not None:
        return int(m.group(4))

    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    bonus = int(m.group(3)) if m.group(3) else 0

    return sum(random.randint(1, sides) for _ in range(count)) + bonus


def roll_d20(advantage: str = "normal") -> dict:
    """
    Roll d20 respecting advantage/disadvantage.

    advantage: 'normal' | 'advantage' | 'disadvantage'

    Returns:
        {
          'roll':      int,        # final value used
          'rolls':     [r1, r2],   # both dice (r2 == r1 for normal)
          'advantage': str,
        }
    """
    r1 = random.randint(1, 20)
    r2 = random.randint(1, 20)

    if advantage == "advantage":
        final = max(r1, r2)
    elif advantage == "disadvantage":
        final = min(r1, r2)
    else:
        final = r1
        r2 = r1  # suppress unused second roll

    return {"roll": final, "rolls": [r1, r2], "advantage": advantage}


def roll_initiative(dex_mod: int) -> int:
    return random.randint(1, 20) + dex_mod
