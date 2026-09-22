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

BASE_URL = (
    "https://www.leaguesecretary.com/"
    "bowling-centers/west-seattle-bowl/"
    "bowling-leagues/rainbowlers-league-c21/"
    "dashboard/122895"
)

ALLOWED_HOST = "www.leaguesecretary.com"
LEAGUE_ID = "122895"

# These are public HTML report families. PDF/shared-report URLs are intentionally
# excluded because the league's PDF reports require a subscription.
REPORT_PATH_MARKERS = (
    "/league/standings/",
    "/league/recaps/",
    "/league/results/",
    "/league/schedule/",
    "/league/statistics/",
    "/league/stats/",
    "/bowler/history/",
    "/team/history/",
)

REPORT_KEYWORDS = (
    "standings",
    "recap",
    "stats",
    "statistics",
    "results",
    "schedule",
    "performance",
    "bowler",
    "team",
    "history",
    "average",
)

SEASON_PATTERNS = (
    # League Secretary uses URLs such as /122895/2026/f/2.
    re.compile(r"\b20[2-9]\d\b", re.IGNORECASE),
    re.compile(r"20[2-9]\d[-/]20[2-9]\d", re.IGNORECASE),
    re.compile(r"(?:Fall|Winter|Spring|Summer)\s+20[2-9]\d", re.IGNORECASE),
)

WEEK_PATTERNS = (
    re.compile(r"\bweek\s*#?\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:round|match|game)\s*#?\d+\b", re.IGNORECASE),
)


class LeagueSecretaryScraper:
    """Collect public Rainbowlers League HTML reports.

    The dashboard contains links to report families such as standings and
    recaps. Those family pages contain more specific season/week links. This
    scraper follows that two-level structure without requiring a browser.
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

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def run(self, backfill: bool = False) -> dict[str, Any]:
        """Discover and fetch public HTML reports.

        ``backfill=True`` fetches all discovered report URLs. A normal run
        fetches the newest 20 report candidates, which keeps the weekly job
        small while still allowing changed reports to be refreshed.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_index()
        result: dict[str, Any] = {
            "fetched": 0,
            "skipped": 0,
            "errors": [],
            "discovered": 0,
            "records": 0,
        }

        try:
            dashboard_html = self._fetch_text(self.dashboard_url)

            logger.info(
                "Fetched dashboard: url=%s html_bytes=%d",
                self.dashboard_url,
                len(dashboard_html.encode("utf-8")),
            )

            # Save this snapshot even when no report links are found. It is the
            # most useful artifact for debugging future site-layout changes.
            (self.raw_dir / "dashboard.html").write_text(
                dashboard_html,
                encoding="utf-8",
            )

            reports = self.discover_reports(dashboard_html)

            logger.info(
                "Discovered %d report links on dashboard",
                len(reports),
            )

            # Report-family pages such as /league/standings/122895 often contain
            # season/week URLs. Follow each family page one level deeper.
            family_reports = [
                report
                for report in reports
                if self._is_report_family(report.url)
            ]

            for family_report in family_reports:
                try:
                    family_html = self._fetch_text(family_report.url)
                    detailed_reports = self.discover_reports(
                        family_html,
                        base_url=family_report.url,
                    )
                    reports.extend(detailed_reports)

                    logger.info(
                        "Discovered %d links from %s",
                        len(detailed_reports),
                        family_report.url,
                    )
                except Exception as exc:
                    result["errors"].append(
                        {
                            "url": family_report.url,
                            "error": str(exc),
                        }
                    )
                    logger.warning(
                        "Could not inspect report family %s: %s",
                        family_report.url,
                        exc,
                    )

            reports = self._deduplicate_reports(reports)
            result["discovered"] = len(reports)

            # Keep dashboard/family pages for diagnostics, plus supported
            # season-specific reports. Unknown-season family pages are retained.
            reports = [
                report
                for report in reports
                if (
                    report.season == "Unknown season"
                    or self._is_supported_season(
                        report.season,
                        report.url,
                    )
                )
            ]

            if backfill:
                selected_reports = reports
            else:
                selected_reports = self._select_latest_relevant_reports(
                    reports
                )

            logger.info(
                "Fetching %d selected reports from %d filtered reports",
                len(selected_reports),
                len(reports),
            )

            for report in selected_reports:
                try:
                    if self._is_subscription_pdf(report.url):
                        logger.info(
                            "Skipping subscription-protected PDF: %s",
                            report.url,
                        )
                        continue

                    report_html = self._fetch_text(report.url)

                    content_hash = hashlib.sha256(
                        report_html.encode(
                            "utf-8",
                            errors="replace",
                        )
                    ).hexdigest()

                    key = self._record_key(report)

                    if (
                        key in state
                        and state[key].get("sha256") == content_hash
                    ):
                        result["skipped"] += 1
                        continue

                    url_hash = hashlib.sha256(
                        report.url.encode("utf-8")
                    ).hexdigest()[:20]

                    filename = f"{url_hash}.html"
                    destination = self.raw_dir / filename
                    destination.write_text(
                        report_html,
                        encoding="utf-8",
                    )

                    saved = ReportRecord(
                        url=report.url,
                        title=report.title,
                        season=report.season,
                        week=report.week,
                        kind=report.kind,
                        sha256=content_hash,
                        raw_file=str(
                            destination.relative_to(self.output_dir)
                        ),
                        fetched_at=datetime.now(
                            timezone.utc
                        ).isoformat(),
                        source="League Secretary",
                    )

                    state[key] = saved.to_dict()
                    result["fetched"] += 1

                except Exception as exc:
                    result["errors"].append(
                        {
                            "url": report.url,
                            "error": str(exc),
                        }
                    )
                    logger.warning(
                        "Failed to fetch report %s: %s",
                        report.url,
                        exc,
                    )

            self._save_index(state)
            result["records"] = len(state)
            return result

        except Exception as exc:
            result["errors"].append(
                {
                    "url": self.dashboard_url,
                    "error": str(exc),
                }
            )
            logger.exception("Dashboard fetch failed")
            self._save_index(state)
            return result

    def discover_reports(
        self,
        html: str,
        base_url: str | None = None,
    ) -> list[ReportRecord]:
        """Find same-host public report links in an HTML document."""
        source_url = base_url or self.dashboard_url
        soup = BeautifulSoup(html, "html.parser")
        seen: dict[str, ReportRecord] = OrderedDict()

        for tag in soup.select("a[href]"):
            href = tag.get("href", "").strip()

            if not href:
                continue

            if href.startswith(("javascript:", "mailto:", "tel:")):
                continue

            target = urljoin(source_url, href).split("#", 1)[0]
            parsed = urlparse(target)
            host = parsed.netloc.lower()

            if host and host != ALLOWED_HOST:
                continue

            if self._is_subscription_pdf(target):
                logger.debug(
                    "Ignoring subscription-protected PDF: %s",
                    target,
                )
                continue

            if LEAGUE_ID not in parsed.path and target != self.dashboard_url:
                continue

            text = " ".join(
                tag.get_text(" ", strip=True).split()
            )

            combined = f"{text} {target}".lower()
            path = parsed.path.lower()

            # This is the important path-marker check. It catches links whose
            # visible text is only "Interactive", while the URL contains
            # "/league/standings/" or another report-family marker.
            is_report_path = any(
                marker in path
                for marker in REPORT_PATH_MARKERS
            )

            has_report_keyword = any(
                keyword in combined
                for keyword in REPORT_KEYWORDS
            )

            is_dashboard = (
                target.rstrip("/")
                == self.dashboard_url.rstrip("/")
            )

            if (
                not is_report_path
                and not has_report_keyword
                and not is_dashboard
            ):
                continue

            report = ReportRecord(
                url=target,
                title=(
                    text or self._title_from_path(path)
                )[:200],
                season=self._extract_season(text, target),
                week=self._extract_week(text, target),
                kind=self._infer_kind(text, target),
            )

            seen[target.lower()] = report

        return list(seen.values())

        def _fetch_text(self, url: str) -> str:
            response = self.session.get(url, timeout=45)
            response.raise_for_status()

            content_type = response.headers.get(
                "content-type",
                "",
            ).lower()

            path = urlparse(url).path.lower()

            if (
                "html" not in content_type
                and not path.endswith(("/", ".html"))
            ):
                raise ValueError(
                    "Expected HTML but received "
                    f"{content_type or 'unknown content type'}"
                )

            return response.text

        def _is_report_family(self, url: str) -> bool:
            """Return True for report landing pages such as /standings/122895."""
            path = urlparse(url).path.rstrip("/").lower()

            family_paths = (
                f"/league/standings/{LEAGUE_ID}",
                f"/league/recaps/{LEAGUE_ID}",
                f"/league/results/{LEAGUE_ID}",
                f"/league/schedule/{LEAGUE_ID}",
                f"/league/statistics/{LEAGUE_ID}",
                f"/league/stats/{LEAGUE_ID}",
            )

        return path in family_paths

    def _is_subscription_pdf(self, url: str) -> bool:
        lower = url.lower()

        return (
            lower.endswith(".pdf")
            or "/reports/shared" in lower
        )

    def _title_from_path(self, path: str) -> str:
        value = (
            path.rstrip("/")
            .split("/")[-1]
            .replace("-", " ")
        )

        return value.title() or "League Secretary report"

    def _deduplicate_reports(
        self,
        reports: list[ReportRecord],
    ) -> list[ReportRecord]:
        unique: dict[str, ReportRecord] = OrderedDict()

        for report in reports:
            unique[report.url.lower()] = report

        return list(unique.values())

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}

        try:
            payload = json.loads(
                self.index_path.read_text(
                    encoding="utf-8",
                )
            )

            records = payload.get("records", {})

            return records if isinstance(records, dict) else {}

        except (json.JSONDecodeError, OSError):
            logger.warning(
                "Could not read %s; starting with an empty index",
                self.index_path,
            )
            return {}

    def _save_index(
        self,
        state: dict[str, dict[str, Any]],
    ) -> None:
        payload = {
            "league": "Rainbowlers League",
            "records": state,
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        self.index_path.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _record_key(
        self,
        report: ReportRecord,
    ) -> str:
        return (
            f"{report.season}|"
            f"{report.week}|"
            f"{report.url}"
        ).lower()

        def _extract_season(self, title: str, url: str) -> str:
            """Extract a season from visible text or a League Secretary URL."""
            text = f"{title} {url}"

            for pattern in SEASON_PATTERNS:
                match = pattern.search(text)
                if match:
                    return match.group(0)

        # League Secretary URLs commonly contain:
        # /122895/2026/f/2
        path_parts = [
            part
            for part in urlparse(url).path.split("/")
            if part
        ]

        for part in path_parts:
            if re.fullmatch(r"20[2-9]\d", part):
                return part

        return "Unknown season"

        def _extract_week(self, title: str, url: str) -> str:
            """Extract a week from visible text or a League Secretary URL."""
            text = f"{title} {url}"

            for pattern in WEEK_PATTERNS:
                match = pattern.search(text)
                if match:
                    return match.group(0)

            path_parts = [
                part
                for part in urlparse(url).path.split("/")
                if part
            ]

        # Examples:
        # /122895/2026/f/2
        # /122895/2026/f/2/25
            for index, part in enumerate(path_parts):
                if re.fullmatch(r"20[2-9]\d", part):
                    remaining = path_parts[index + 1 :]

                # The first component after the season is the season code.
                    if len(remaining) >= 2 and remaining[1].isdigit():
                        return f"Week {remaining[1]}"

                    break

            if "bowler" not in url.lower() and path_parts:
                final_part = path_parts[-1]

                if final_part.isdigit():
                    return f"Report {final_part}"

        return "Unknown week"

    def _infer_kind(
        self,
        title: str,
        url: str,
    ) -> str:
        text = f"{title} {url}".lower()

        for keyword in (
            "standings",
            "recap",
            "statistics",
            "stats",
            "results",
            "schedule",
            "bowler",
            "team",
            "performance",
            "history",
        ):
            if keyword in text:
                return keyword

        return "report"

    def _is_supported_season(
        self,
        season: str,
        url: str,
    ) -> bool:
        text = f"{season} {url}"
        years = re.findall(r"20([2-9]\d)", text)

        return any(
            int(year) >= 26
            for year in years
        )

    def _select_latest_relevant_reports(
        self,
        reports: list[ReportRecord],
    ) -> list[ReportRecord]:
        return sorted(
            reports,
            key=lambda report: (
                self._season_sort_key(report.season),
                self._week_sort_key(report.week),
                report.url,
            ),
            reverse=True,
        )[:20]

    def _season_sort_key(
        self,
        season: str,
    ) -> tuple[int, str]:
        match = re.search(
            r"20([2-9]\d)",
            season,
        )

        return (
            int(match.group(1))
            if match
            else 0,
            season,
        )

    def _week_sort_key(
        self,
        week: str,
    ) -> int:
        match = re.search(
            r"(?:week|report)\s*(\d+)",
            week,
            re.IGNORECASE,
        )

        return int(match.group(1)) if match else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    scraper = LeagueSecretaryScraper(output_dir="data")

    print(
        json.dumps(
            scraper.run(backfill=False),
            indent=2,
            sort_keys=True,
        )
    )
