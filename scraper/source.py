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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

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

# These report families are publicly accessible HTML pages.
# Subscription-protected PDF reports are intentionally excluded.
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
    re.compile(r"\b20[2-9]\d\b", re.IGNORECASE),
    re.compile(r"20[2-9]\d[-/]20[2-9]\d", re.IGNORECASE),
    re.compile(
        r"(?:Fall|Winter|Spring|Summer)\s+20[2-9]\d",
        re.IGNORECASE,
    ),
)

WEEK_PATTERNS = (
    re.compile(r"\bweek\s*#?\d+\b", re.IGNORECASE),
    re.compile(
        r"\b(?:round|match|game)\s*#?\d+\b",
        re.IGNORECASE,
    ),
)


class LeagueSecretaryScraper:
    """Collect public and JavaScript-rendered Rainbowlers League reports."""

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
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def run(self, backfill: bool = False) -> dict[str, Any]:
        """Discover and fetch public HTML reports.

        The dashboard and report-family pages are discovered with requests.
        Individual report pages are opened with Playwright so JavaScript-
        generated tables and interactive content are included.
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

            # Save the original dashboard response for debugging.
            self._save_text_file(
                self.raw_dir / "dashboard.html",
                dashboard_html,
            )

            reports = self.discover_reports(
                dashboard_html,
                base_url=self.dashboard_url,
            )

            logger.info(
                "Discovered %d links on dashboard",
                len(reports),
            )

            # Report-family pages contain links to specific season/week
            # reports. Inspect each family page one level deeper.
            family_reports = [
                report
                for report in reports
                if self._is_report_family(report.url)
            ]

            for family_report in family_reports:
                try:
                    family_html = self._fetch_text(family_report.url)

                    family_links = self.discover_reports(
                        family_html,
                        base_url=family_report.url,
                    )

                    reports.extend(family_links)

                    logger.info(
                        "Discovered %d links from %s",
                        len(family_links),
                        family_report.url,
                    )

                except Exception as exc:
                    self._add_error(
                        result,
                        family_report.url,
                        exc,
                    )
                    logger.warning(
                        "Could not inspect report family %s: %s",
                        family_report.url,
                        exc,
                    )

            reports = self._deduplicate_reports(reports)
            result["discovered"] = len(reports)

            logger.info(
                "Total unique report links discovered: %d",
                len(reports),
            )

            # Keep family pages for diagnostics. Season-specific links are
            # filtered to 2026 and later.
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
                selected_reports = (
                    self._select_latest_relevant_reports(reports)
                )

            logger.info(
                "Selected %d reports from %d filtered reports",
                len(selected_reports),
                len(reports),
            )

            for report in selected_reports:
                try:
                    self._fetch_and_store_report(
                        report=report,
                        state=state,
                        result=result,
                    )
                except Exception as exc:
                    self._add_error(
                        result,
                        report.url,
                        exc,
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
            self._add_error(
                result,
                self.dashboard_url,
                exc,
            )
            logger.exception("Dashboard fetch failed")
            self._save_index(state)
            return result

    def _fetch_and_store_report(
        self,
        report: ReportRecord,
        state: dict[str, dict[str, Any]],
        result: dict[str, Any],
    ) -> None:
        """Fetch one report and save its rendered HTML."""

        if self._is_subscription_pdf(report.url):
            logger.info(
                "Skipping subscription-protected PDF: %s",
                report.url,
            )
            return

        if self._is_rendered_report(report.url):
            logger.info(
                "Loading JavaScript-rendered report: %s",
                report.url,
            )
            report_html = self._fetch_rendered_html(report.url)
        else:
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
            return

        url_hash = hashlib.sha256(
            report.url.encode("utf-8")
        ).hexdigest()[:20]

        destination = self.raw_dir / f"{url_hash}.html"
        self._save_text_file(destination, report_html)

        saved_record = ReportRecord(
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

        state[key] = saved_record.to_dict()
        result["fetched"] += 1

        logger.info(
            "Saved report: %s",
            destination,
        )

    def discover_reports(
        self,
        html: str,
        base_url: str | None = None,
    ) -> list[ReportRecord]:
        """Find same-host report links in an HTML document."""

        source_url = base_url or self.dashboard_url
        soup = BeautifulSoup(html, "html.parser")
        seen: dict[str, ReportRecord] = OrderedDict()

        for tag in soup.select("a[href]"):
            href = tag.get("href", "").strip()

            if not href:
                continue

            if href.startswith(
                (
                    "javascript:",
                    "mailto:",
                    "tel:",
                )
            ):
                continue

            target = urljoin(
                source_url,
                href,
            ).split("#", 1)[0]

            parsed = urlparse(target)
            path = parsed.path.lower()

            if (
                parsed.netloc
                and parsed.netloc.lower() != ALLOWED_HOST
            ):
                continue

            if self._is_subscription_pdf(target):
                logger.debug(
                    "Ignoring subscription-protected PDF: %s",
                    target,
                )
                continue

            if (
                LEAGUE_ID not in parsed.path
                and target.rstrip("/")
                != self.dashboard_url.rstrip("/")
            ):
                continue

            text = " ".join(
                tag.get_text(
                    " ",
                    strip=True,
                ).split()
            )

            combined = f"{text} {target}".lower()

            # This is important because some dashboard links have visible
            # text such as "Interactive", while the URL contains the report
            # type, such as /league/standings/.
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
                season=self._extract_season(
                    text,
                    target,
                ),
                week=self._extract_week(
                    text,
                    target,
                ),
                kind=self._infer_kind(
                    text,
                    target,
                ),
            )

            seen[target.lower()] = report

        return list(seen.values())

    def _fetch_text(self, url: str) -> str:
        """Fetch an HTML page without executing JavaScript."""

        response = self.session.get(
            url,
            timeout=45,
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        path = urlparse(url).path.lower()

        if (
            "html" not in content_type
            and not path.endswith(
                (
                    "/",
                    ".html",
                )
            )
        ):
            raise ValueError(
                "Expected HTML but received "
                f"{content_type or 'unknown content type'}"
            )

        return response.text

    def _fetch_rendered_html(self, url: str) -> str:
        """Fetch a report after JavaScript has rendered its content."""

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
            )

            try:
                page = browser.new_page(
                    user_agent=self.user_agent,
                    viewport={
                        "width": 1440,
                        "height": 1200,
                    },
                )

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )

                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=30_000,
                    )
                except PlaywrightTimeoutError:
                    logger.warning(
                        "Network did not become idle for %s",
                        url,
                    )

                # Interactive report pages normally contain a table after
                # JavaScript finishes. If no table appears, allow additional
                # time for the page's client-side code to finish.
                try:
                    page.wait_for_selector(
                        "table",
                        timeout=30_000,
                    )
                except PlaywrightTimeoutError:
                    logger.warning(
                        "No table found on %s; waiting before capture",
                        url,
                    )
                    page.wait_for_timeout(5_000)

                rendered_html = page.content()

                logger.info(
                    "Rendered report: url=%s html_bytes=%d",
                    url,
                    len(rendered_html.encode("utf-8")),
                )

                return rendered_html

            finally:
                browser.close()

    def _is_rendered_report(self, url: str) -> bool:
        """Return True for individual interactive report pages."""

        path = urlparse(url).path.lower()

        # These report families contain JavaScript-rendered content.
        return any(
            marker in path
            for marker in (
                "/league/standings/",
                "/league/recaps/",
                "/league/results/",
                "/league/schedule/",
                "/league/statistics/",
                "/league/stats/",
                "/bowler/history/",
                "/team/history/",
            )
        ) and not self._is_report_family(url)
