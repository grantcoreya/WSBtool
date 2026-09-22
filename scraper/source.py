from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scraper.models import ReportRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.leaguesecretary.com/bowling-centers/c/bowling-leagues/l/dashboard/122895"
ALLOWED_HOST = "www.leaguesecretary.com"

# Keep the scraper intentionally conservative. We only want 2026-2027 and later.
SEASON_PATTERNS = [
    re.compile(r"20[2-9]\d[-/ ]20[2-9]\d", re.IGNORECASE),  # 2026-2027, 2026/2027, etc.
    re.compile(r"(Fall|Winter|Spring|Summer)\s+20[2-9]\d", re.IGNORECASE),
    re.compile(r"20[2-9]\d[-/ ]?Season", re.IGNORECASE),
]

WEEK_PATTERNS = [
    re.compile(r"\bweek\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bweek\s*(?:#)?\d+\b", re.IGNORECASE),
]

# Common report keywords that indicate views worth scraping
REPORT_KEYWORDS = (
    "standings",
    "recap",
    "stats",
    "statistics",
    "results",
    "documents",
    "schedule",
    "performance",
    "bowler",
    "team",
    "lane",
    "graph",
    "average",
    "top",
)


class LeagueSecretaryScraper:
    """
    Conservative public scraper for Rainbowlers League pages.

    Design goals:
    - fetch public HTML without login
    - save raw HTML for debugging
    - deduplicate by (season, week, URL) + content hash
    - keep data stable across reruns
    """

    def __init__(
        self,
        output_dir: Path | str = "data",
        dashboard_url: str = BASE_URL,
        user_agent: str = "WSBtool/RainbowlersLeagueScraper/1.0",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.raw_dir = self.output_dir / "raw"
        self.index_path = self.output_dir / "index.json"
        self.dashboard_url = dashboard_url
        self.user_agent = user_agent

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def run(self, backfill: bool = False) -> dict[str, Any]:
        """
        Fetch the public dashboard and any useful report links.

        - backfill=True: fetch all discovered report URLs
        - backfill=False: fetch only the latest records or missing records
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_index()
        results = {
            "fetched": 0,
            "skipped": 0,
            "errors": [],
            "records": 0,
        }

        try:
            dashboard_html = self._fetch_text(self.dashboard_url)
            reports = self.discover_reports(dashboard_html)

            # Keep only season records associated with 2026-2027 and later
            reports = [r for r in reports if self._is_supported_season(r.season, r.url)]

            # Latest report ordering is not guaranteed. Keep a stable order.
            reports = sorted(reports, key=lambda r: (r.season, r.week, r.url))

            if not backfill:
                # In normal runs, fetch only the newest unknown or changed records.
                # This keeps the weekly process small but still catches updates.
                selected_reports = self._select_latest_relevant_reports(reports)
            else:
                selected_reports = reports

            for report in selected_reports:
                try:
                    report_html = self._fetch_text(report.url)
                    content_hash = hashlib.sha256(report_html.encode("utf-8", errors="replace")).hexdigest()
                    key = self._record_key(report)

                    if key in state and state[key].get("sha256") == content_hash:
                        results["skipped"] += 1
                        continue

                    filename = f\"{hashlib.sha256(report.url.encode('utf-8')).hexdigest()[:20]}.html\"
                    destination = self.raw_dir / filename
                    destination.write_text(report_html, encoding="utf-8")

                    record = ReportRecord(
                        url=report.url,
                        title=report.title,
                        season=report.season,
                        week=report.week,
                        kind=report.kind,
                        sha256=content_hash,
                        raw_file=str(destination.relative_to(self.output_dir)),
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        source=\"League Secretary\",
                    )

                    state[key] = record.to_dict()
                    results["fetched"] += 1

                except Exception as exc:  # Keep going even if one report fails.
                    results["errors"].append({"url": report.url, "error": str(exc)})
                    logger.warning("Failed to fetch report %s: %s", report.url, exc)

            self._save_index(state)
            results["records"] = len(state)
            return results

        except Exception as exc:
            results["errors"].append({"url": self.dashboard_url, "error": str(exc)})
            logger.exception("Dashboard fetch failed.")
            self._save_index(state)
            return results

    def discover_reports(self, html: str) -> list[ReportRecord]:
        """
        Walk the dashboard HTML and collect candidate report URLs.

        This intentionally finds only strong report-like links. It is intentionally
        conservative because some generic links may not represent actual data pages.
        """
        soup = BeautifulSoup(html, "html.parser")
        seen: dict[str, ReportRecord] = OrderedDict()

        for tag in soup.select("a[href]"):
            href = tag.get("href", "").strip()
            if not href:
                continue

            target = urljoin(self.dashboard_url, href).split("#", 1)[0]
            parsed = urlparse(target)

            if parsed.netloc and parsed.netloc.lower() != ALLOWED_HOST:
                continue

            text = " ".join(tag.get_text(" ", strip=True).split())
            combined = f\"{text} {target}\".lower()

            if not any(keyword in combined for keyword in REPORT_KEYWORDS):
                continue

            title = text or "League Secretary report"
            season = self._extract_season(title, target)
            week = self._extract_week(title, target)
            kind = self._infer_kind(title, target)

            if not self._is_supported_season(season, target):
                continue

            seen[target] = ReportRecord(
                url=target,
                title=title[:200],
                season=season or "Unknown season",
                week=week or "Unknown week",
                kind=kind,
            )

        # Include the dashboard itself, as a useful baseline reference.
        dashboard_record = ReportRecord(
            url=self.dashboard_url,
            title="Rainbowlers League Dashboard",
            season="Unknown season",
            week="Unknown week",
            kind="dashboard",
        )
        seen.setdefault(self.dashboard_url, dashboard_record)

        return list(seen.values())

    def _fetch_text(self, url: str) -> str:
        response = self.session.get(url, timeout=45)
        response.raise_for_status()
        return response.text

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}

        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data.get("records", {})
        except json.JSONDecodeError:
            return {}

    def _save_index(self, state: dict[str, dict[str, Any]]) -> None:
        payload = {
            "league": "Rainbowlers League",
            "records": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.index_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _record_key(self, record: ReportRecord) -> str:
        # The key is stable and intentionally deduplicates a given season/week report.
        return f\"{record.season}|{record.week}|{record.url}\".lower()

    def _extract_season(self, title: str, url: str) -> str:
        text = f"{title} {url}"
        for pattern in SEASON_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return "Unknown season"

    def _extract_week(self, title: str, url: str) -> str:
        text = f"{title} {url}"
        for pattern in WEEK_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return "Unknown week"

    def _infer_kind(self, title: str, url: str) -> str:
        text = f"{title} {url}".lower()
        for keyword in ("standings", "stats", "statistics", "result", "recap", "schedule", "performance", "bowler", "team", "graph"):
            if keyword in text:
                return keyword
        return "report"

    def _is_supported_season(self, season: str, url: str) -> bool:
        """
        Only keep seasons newer than or equal to 2026-2027.

        This keeps the scraper aligned with your requirement to fetch only
        seasons 2026-2027 and later.
        """
        text = f"{season} {url}"
        year_matches = re.findall(r"20([2-9]\\d)", text)
        if not year_matches:
            return False

        # Accept any season starting in 2026 or later, including 2026-2027.
        for year in year_matches:
            try:
                if int(year) >= 26:
                    return True
            except ValueError:
                continue

        return False

    def _select_latest_relevant_reports(self, reports: list[ReportRecord]) -> list[ReportRecord]:
        """
        Choose a small set for a weekly run. Prefer unseen or updated records.
        The exact order is not critical, only that the selection remains deterministic.
        """
        if not reports:
            return []

        # Keep newest season and outstanding week candidates.
        sorted_reports = sorted(
            reports,
            key=lambda r: (
                self._season_sort_key(r.season),
                self._week_sort_key(r.week),
                r.url,
            ),
            reverse=True,
        )

        # Keep the newest 20 report URLs to avoid over-fetching.
        return sorted_reports[:20]

    def _season_sort_key(self, season: str) -> tuple[int, str]:
        # Convert common season labels to a comparable value.
        # Examples: '2026-2027', 'Fall 2026', '2027-2028'
        match = re.search(r"20([2-9]\\d)", season)
        year = int(match.group(1)) if match else 0
        return (year, season)

    def _week_sort_key(self, week: str) -> int:
        match = re.search(r"(?:week\\s*)?(\\d+)", week, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0
