from __future__ import annotations

import html
import json
from pathlib import Path

DATA_PATH = Path("data")
SITE_PATH = Path("site")


def load_records() -> list[dict]:
    index_path = DATA_PATH / "index.json"
    if not index_path.exists():
        return []

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        records = payload.get("records", {})
        return list(records.values())
    except json.JSONDecodeError:
        return []


def build_site() -> None:
    SITE_PATH.mkdir(parents=True, exist_ok=True)
    records = load_records()

    season_options = sorted({r.get("season", "Unknown season") for r in records}, reverse=True)
    rows_html = []

    for record in sorted(records, key=lambda r: (r.get("season", ""), r.get("week", ""), r.get("title", ""))):
        season = html.escape(str(record.get("season", "Unknown season")))
        week = html.escape(str(record.get("week", "Unknown week")))
        kind = html.escape(str(record.get("kind", "report")))
        title = html.escape(str(record.get("title", "League Secretary report")))
        raw_file = record.get("raw_file", "")
        fetched_at = html.escape(str(record.get("fetched_at", "")))

        if raw_file:
            source_link = f'<a href="{html.escape(raw_file)}" target="_blank" rel="noreferrer">raw snapshot</a>'
        else:
            source_link = "N/A"

        rows_html.append(
            f"""
            <tr>
              <td>{season}</td>
              <td>{week}</td>
              <td>{kind}</td>
              <td>{title}</td>
              <td>{source_link}</td>
              <td>{fetched_at}</td>
            </tr>
            """
        )

    if not rows_html:
        rows_html.append(
            """
            <tr>
              <td colspan="6">No report records have been collected yet. Run the scraper or perform a backfill.</td>
            </tr>
            """
        )

    season_select = "\n".join(
        f'<option value="{html.escape(s)}">{html.escape(s)}</option>' for s in season_options
    )

    template = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Rainbowlers League Reports</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --panel: #ffffff;
      --border: #dfe3ea;
      --text: #1f2937;
      --muted: #6b7280;
      --accent: #2563eb;
      --header: #111827;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 16px 64px;
    }}
    header {{
      background: var(--header);
      color: white;
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 2rem;
    }}
    p {{
      margin: 0;
      color: #d5d9e2;
    }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      margin-top: 18px;
    }}
    .controls label {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-weight: 600;
    }}
    select, button {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 1rem;
    }}
    button {{
      background: var(--accent);
      border-color: var(--accent);
      color: white;
      cursor: pointer;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }}
    th, td {{
      border-bottom: 1px solid var(--border);
      padding: 12px 10px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #f8fafc;
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }}
    .muted {{
      color: var(--muted);
    }}
    @media (max-width: 700px) {{
      th, td {{
        font-size: 0.92rem;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Rainbowlers League</h1>
      <p>Public reports collected from League Secretary</p>

      <div class="controls">
        <label>
          Season
          <select id="seasonFilter">
            <option value="all">All seasons</option>
            {season_select}
          </select>
        </label>
        <button id="printButton" type="button">Print / Save PDF</button>
      </div>
    </header>

    <div class="panel">
      <div class="muted" id="resultCount">{len(records)} record(s)</div>
      <table>
        <thead>
          <tr>
            <th>Season</th>
            <th>Week</th>
            <th>Kind</th>
            <th>Title</th>
            <th>Source</th>
            <th>Fetched</th>
          </tr>
        </thead>
        <tbody id="reportTable">
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
  </div>

  <script>
    const seasonFilter = document.getElementById('seasonFilter');
    const rows = [...document.querySelectorAll('#reportTable tr')];
    const resultCount = document.getElementById('resultCount');

    function refreshTable() {{
      const value = seasonFilter.value;
      let visible = 0;

      rows.forEach((row) => {{
        const cells = row.cells;
        if (!cells || cells.length < 6) {{
          row.style.display = 'table-row';
          return;
        }}

        const seasonCell = cells[0]?.textContent?.trim() || '';
        const show = value === 'all' || seasonCell === value;
        row.style.display = show ? 'table-row' : 'none';
        if (show) visible += 1;
      }});

      resultCount.textContent = visible + ' record(s)';
    }}

    seasonFilter.addEventListener('change', refreshTable);
    document.getElementById('printButton').addEventListener('click', () => window.print());

    refreshTable();
  </script>
</body>
</html>
"""

    (SITE_PATH / "index.html").write_text(template, encoding="utf-8")


if __name__ == "__main__":
    build_site()
