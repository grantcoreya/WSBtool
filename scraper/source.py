from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
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

# These patterns allow the scraper to recognize modern league naming conventions,
# including season labels like "Fall 2026", "2026-2027", and similar variants.
SEASON_PATTERNS = [
    re.compile(r"20[2-9]\d(?:[-/ ]20[2-9]\d)?", re.IGNORECASE),
    re.compile(r"(?:Fall|Winter|Spring|Summer)\s+20[2-9]\d", re.IGNORECASE),
    re.compile(r"20[2-9]\d[-/ ]?Season", re.IGNORECASE),
]

WEEK_PATTERNS = [
    re.compile(r"\bweek\s*#?\d+\b", re.IGNORECASE),
    re.compile(r"\bweek\s*\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:round|match|game)\s*#?\d+\b", re.IGNORECASE),
]

# Report-like words to keep only strong, real report candidates and exclude
# generic navigation, help, or marketing links.
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
    "league",
    "dashboard",
)


class LeagueSecretaryScraper:
    """
    Conservative public scraper for Rainbowlers League pages.

    The goal is to collect public League Secretary reports in a way that is
    stable, easily debuggable, and friendly to a weekly automation run.

    Design goals:
    - fetch public HTML without login
    - save raw HTML files for inspection/debugging
    - store an index of known reports to avoid re-downloading unchanged content
    - keep a very conservative allowlist for report-like pages
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
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def run(self, backfill: bool = False) -> dict[str, Any]:
        """
        Fetch the public dashboard and any report links that look legitimate.

        Args:
            backfill:
                If True, fetch all supported report URLs. If False, fetch only
                the newest records from the current season/week selection.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_index()
        result: dict[str, Any] = {
            "fetched": 0,
            "skipped": 0,
            "errors": [],
            "records": 0,
        }

        try:
            dashboard_html = self._fetch_text(self.dashboard_url)
            reports = self.discover_reports(dashboard_html)

            # Keep only seasons that look current enough for your workflow.
            reports = [report for report in reports if self._is_supported_season(report.season, report.url)]
            reports = sorted(reports, key=lambda item: (self._season_sort_key(item.season), self._week_sort_key(item.week), item.url))

            if backfill:
                selected_reports = reports
            else:
                selected_reports = self._select_latest_relevant_reports(reports)

            for report in selected_reports:
                try:
                    report_html = self._fetch_text(report.url)
                    content_hash = hashlib.sha256(report_html.encode("utf-8", errors="replace")).hexdigest()
                    record_key = self._record_key(report)

                    if record_key in state and state[record_key].get("sha256") == content_hash:
                        result["skipped"] += 1
                        continue

                    filename = f"{hashlib.sha256(report.url.encode('utf-8')).hexdigest()[:20]}.html"
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
                        source="League Secretary",
                    )

                    state[record_key] = record.to_dict()
                    result["fetched"] += 1

                except Exception as exc:  # Continue even if one URL fails.
                    result["errors"].append({"url": report.url, "error": str(exc)})
                    logger.warning("Failed to fetch report %s: %s", report.url, exc)

            self._save_index(state)
            result["records"] = len(state)
            return result

        except Exception as exc:
            result["errors"].append({"url": self.dashboard_url, "error": str(exc)})
            logger.exception("Dashboard fetch failed.")
            self._save_index(state)
            return result

    def discover_reports(self, html: str) -> list[ReportRecord]:
        """
        Walk the dashboard HTML and collect candidate report links.

        We intentionally keep the filter narrow so the scraper does not pull in
        unrelated links from the site navigation, newsletters, or marketing pages.
        """
        soup = BeautifulSoup(html, "html.parser")
        seen: dict[str, ReportRecord] = OrderedDict()

        for tag in soup.select("a[href]"):
            href = tag.get("href", "").strip()
            if not href or href.startswith("javascript:"):
                continue

            target = urljoin(self.dashboard_url, href).split("#", 1)[0]
            parsed = urlparse(target)

            if parsed.netloc and parsed.netloc.lower() != ALLOWED_HOST:
                continue

            text = " ".join(tag.get_text(" ", strip=True).split())
            combined = f"{text} {target}".lower()

            if not self._looks_like_report(text, target, combined):
                continue

            record = ReportRecord(
                url=target,
                title=(text or "League Secretary report")[:200],
                season=self._extract_season(text, target),
                week=self._extract_week(text, target),
                kind=self._infer_kind(text, target),
            )

            if not self._is_supported_season(record.season, record.url):
                continue

            seen[target.lower()] = record

        dashboard_record = ReportRecord(
            url=self.dashboard_url,
            title="Rainbowlers League Dashboard",
            season="Unknown season",
            week="Unknown week",
            kind="dashboard",
        )
        seen.setdefault(self.dashboard_url.lower(), dashboard_record)

        return list(seen.values())

    def _looks_like_report(self, text: str, url: str, combined: str) -> bool:
        """Return True when a URL is likely to be a report page instead of a menu item."""
        if not text and "/reports/" not in url.lower():
            return False

        lower_text = text.lower()
        if any(keyword in lower_text for keyword in REPORT_KEYWORDS):
            return True

        if any(keyword in url.lower() for keyword in ("reports", "standings", "stats", "results", "recap", "schedule")):
            return True

        if any(keyword in combined for keyword in ("statistics", "standings", "results", "recap", "weekly")):
            return True

        return False

    def _fetch_text(self, url: str) -> str:
        response = self.session.get(url, timeout=45)
        response.raise_for_status()
        return response.text

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}

        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            return payload.get("records", {})
        except json.JSONDecodeError:
            logger.warning("index.json exists but could not be parsed; starting fresh")
            return {}

    def _save_index(self, state: dict[str, dict[str, Any]]) -> None:
        payload = {
            "league": "Rainbowlers League",
            "records": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.index_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _record_key(self, record: ReportRecord) -> str:
        return f"{record.season}|{record.week}|{record.url}".lower()

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
        for keyword in (
            "standings",
            "stats",
            "statistics",
            "result",
            "recap",
            "schedule",
            "performance",
            "bowler",
            "team",
            "graph",
            "average",
        ):
            if keyword in text:
                return keyword
        return "report"

    def _is_supported_season(self, season: str, url: str) -> bool:
        """
        Accept only seasons aligned with your app’s current requirement.

        For example, this keeps the workflow focused on 2026-2027 and later,
        while still tolerating older-season links that might be referenced in the
        dashboard metadata.
        """
        text = f"{season} {url}"
        year_matches = re.findall(r"20([2-9]\d)", text)
        if not year_matches:
            return False

        for year in year_matches:
            try:
                if int(year) >= 26:
                    return True
            except ValueError:
                continue

        return False

    def _select_latest_relevant_reports(self, reports: list[ReportRecord]) -> list[ReportRecord]:
        """Keep a small, deterministic set for a normal weekly run."""
        if not reports:
            return []

        sorted_reports = sorted(
            reports,
            key=lambda item: (
                self._season_sort_key(item.season),
                self._week_sort_key(item.week),
                item.url,
            ),
            reverse=True,
        )

        return sorted_reports[:20]

    def _season_sort_key(self, season: str) -> tuple[int, str]:
        match = re.search(r"20([2-9]\d)", season)
        year = int(match.group(1)) if match else 0
        return (year, season)

    def _week_sort_key(self, week: str) -> int:
        match = re.search(r"(?:week\s*)?(\d+)", week, flags=re.IGNORECASE)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    scraper = LeagueSecretaryScraper(output_dir="data")
    result = scraper.run(backfill=False)
    print(json.dumps(result, indent=2, sort_keys=True))
