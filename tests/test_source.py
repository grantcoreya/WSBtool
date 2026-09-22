from scraper.source import LeagueSecretaryScraper


def test_discover_reports_filters_external_links(tmp_path):
    html = '''<a href="/reports/standings">Standings Week 2</a><a href="https://example.com/statistics">Statistics</a><a href="/about">About</a>'''
    reports = LeagueSecretaryScraper(tmp_path).discover_reports(html)
    urls = {r.url for r in reports}
    assert "https://www.leaguesecretary.com/reports/standings" in urls
    assert not any("example.com" in url for url in urls)
    assert not any(url.endswith("/about") for url in urls)


def test_existing_content_is_idempotent(tmp_path):
    scraper = LeagueSecretaryScraper(tmp_path)
    scraper.index_path.parent.mkdir(parents=True, exist_ok=True)
    scraper.index_path.write_text('{"records": {"key": {"sha256": "abc"}}}', encoding="utf-8")
    assert scraper._load_state()["key"]["sha256"] == "abc"
