"""Runtime settings. Everything is overridable by environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# A fixed simulated "today" keeps eval runs reproducible: delivery windows are
# computed against this date, never against the wall clock.
DEFAULT_TODAY = date(2026, 10, 17)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    policy_path: Path
    today: date


def load_settings() -> Settings:
    today_raw = os.getenv("RESOLVELAB_TODAY")
    return Settings(
        db_path=Path(os.getenv("RESOLVELAB_DB", ROOT / "data" / "resolvelab.db")),
        policy_path=Path(os.getenv("RESOLVELAB_POLICY", ROOT / "data" / "policy.yaml")),
        today=date.fromisoformat(today_raw) if today_raw else DEFAULT_TODAY,
    )
