from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReportRecord:
    url: str
    title: str
    season: str = "Unknown season"
    week: str = "Unknown week"
    kind: str = "report"
    sha256: str = ""
    raw_file: str = ""
    fetched_at: str = ""
    source: str = "League Secretary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "season": self.season,
            "week": self.week,
            "kind": self.kind,
            "sha256": self.sha256,
            "raw_file": self.raw_file,
            "fetched_at": self.fetched_at,
            "source": self.source,
        }
