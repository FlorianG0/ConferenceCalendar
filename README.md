# ConferenceCalendar MVP

Minimal prototype for an IEEE conference deadline calendar with:

- manual conference list (`conferences.yaml`)
- crawler (`scripts/crawl_deadlines.py`) that checks pages and updates `data/conferences.json`
- scheduled GitHub Action (twice daily UTC)
- static GitHub Pages website (`site/index.html`) with calendar + stretch window

## Local run

```bash
pip install -r requirements.txt
python scripts/crawl_deadlines.py
```

## GitHub setup

1. Push repo to GitHub.
2. In repo settings, enable **Pages** and set source to **GitHub Actions**.
3. Workflow `.github/workflows/crawler-and-pages.yml` will run:
   - scheduled at `06:00` and `18:00` UTC
   - manually via `workflow_dispatch`

## Add conferences

1. Add an entry in `conferences.yaml`:

```yaml
- id: myconf-2027
  family: myconf
  year: 2027
  name: MyConf 2027
  short_name: MYCONF
  url: https://example.org/call-for-papers
  fallback_deadline: 2027-03-15
  seed_submission_dates:
    - 2027-02-20
    - 2027-03-01
    - 2027-03-15
  crawl_urls:
    - https://example.org/call-for-papers
    - https://example.org/important-dates
```

2. Add keyword rules in `crawler_rules.yaml`:

```yaml
families:
  myconf:
    include_keywords:
      - technical paper submission deadline
      - symposium paper deadline
      - paper submission due
```

3. Re-run crawler:

```bash
python scripts/crawl_deadlines.py
```

Notes:
- `fallback_deadline` is the initial date if parsing fails.
- `seed_submission_dates` lets you provide known historical extension dates (strongly recommended).
- `crawl_urls` lets you target exact subpages (better than homepage-only crawling).

## Push to GitHub + Auto Run

1. Initialize and push:

```bash
git init
git add .
git commit -m "Initial conference calendar MVP"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

2. In GitHub repo settings:
- `Settings -> Pages -> Source`: select `GitHub Actions`

3. The workflow `.github/workflows/crawler-and-pages.yml` is already configured to run:
- every day at `06:00 UTC`
- every day at `18:00 UTC`
- manually via `Run workflow`

4. Optional: if your default branch is not `main`, keep workflow on that branch and push there.

## Next MVP+ step

Use `data/deadline_history.json` to train a first extension predictor (baseline: median extension days per conference family).
