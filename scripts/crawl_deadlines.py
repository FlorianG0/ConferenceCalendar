import json
import re
import statistics
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests
import urllib3
import yaml
from bs4 import BeautifulSoup
from dateutil import parser

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "conferences.yaml"
RULES_PATH = ROOT / "crawler_rules.yaml"
OUT_PATH = DATA_DIR / "conferences.json"
HISTORY_PATH = DATA_DIR / "deadline_history.json"

DATE_PATTERNS = [
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?),?\s+20\d{2}\b",
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+20\d{2}\b",
    r"\b20\d{2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s*\d{1,2}\b",
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s*\d{1,2}\b",
    r"\b20\d{2}-\d{2}-\d{2}\b",
]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_iso_date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return parser.parse(value, fuzzy=True, dayfirst=False).date().isoformat()
    return str(value)


def fetch_page_lines(url: str) -> tuple[list[str] | None, str | None]:
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "ConferenceCalendarBot/0.2"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
        return lines, None
    except requests.exceptions.SSLError:
        try:
            resp = requests.get(
                url,
                timeout=30,
                headers={"User-Agent": "ConferenceCalendarBot/0.2"},
                verify=False,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
            return lines, "ssl-verify-failed; fetched without TLS verification"
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def merge_rule_for_conference(conf: dict, rules: dict) -> dict:
    merged = dict(rules.get("default", {}))

    family = conf.get("family")
    family_rule = (rules.get("families") or {}).get(family, {})
    merged.update(family_rule)

    conf_rule = (rules.get("conferences") or {}).get(conf["id"], {})
    merged.update(conf_rule)
    return merged


def contains_any(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords if k)


def extract_dates_from_line(line: str, target_year: int | None) -> list[str]:
    results: list[str] = []
    for pat in DATE_PATTERNS:
        results.extend(re.findall(pat, line, flags=re.IGNORECASE))
    dedup: list[str] = []
    seen = set()
    for item in results:
        token = item.strip()
        if re.fullmatch(
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s*\d{1,2}",
            token,
            flags=re.IGNORECASE,
        ):
            if target_year:
                token = f"{token} {target_year}"
            else:
                continue
        if token not in seen:
            dedup.append(token)
            seen.add(token)
    return dedup


def extract_candidates(lines: list[str], rule: dict, target_year: int | None) -> list[dict]:
    include_keywords = rule.get("include_keywords", [])
    exclude_keywords = rule.get("exclude_keywords", [])
    firm_keywords = rule.get("firm_keywords", [])
    extension_keywords = rule.get("extension_keywords", [])
    radius = int(rule.get("context_radius", 1))

    matched = set()
    for idx, line in enumerate(lines):
        if contains_any(line, exclude_keywords):
            continue
        if contains_any(line, include_keywords):
            for j in range(max(0, idx - radius), min(len(lines), idx + radius + 1)):
                if not contains_any(lines[j], exclude_keywords):
                    matched.add(j)

    if not matched:
        matched = set(range(len(lines)))

    candidates = []
    for idx in sorted(matched):
        line = lines[idx]
        if contains_any(line, exclude_keywords):
            continue
        date_texts = extract_dates_from_line(line, target_year=target_year)
        if not date_texts:
            continue

        for raw in date_texts:
            try:
                iso = parser.parse(raw, fuzzy=True, dayfirst=False).date().isoformat()
            except Exception:  # noqa: BLE001
                continue
            lower = line.lower()
            candidates.append(
                {
                    "iso": iso,
                    "raw": raw,
                    "context": line[:240],
                    "has_keyword": contains_any(line, include_keywords),
                    "is_firm": contains_any(lower, firm_keywords),
                    "is_extension": contains_any(lower, extension_keywords),
                }
            )

    dedup = {}
    for c in candidates:
        current = dedup.get(c["iso"])
        if not current:
            dedup[c["iso"]] = c
            continue
        current["has_keyword"] = current["has_keyword"] or c["has_keyword"]
        current["is_firm"] = current["is_firm"] or c["is_firm"]
        current["is_extension"] = current["is_extension"] or c["is_extension"]
    return sorted(dedup.values(), key=lambda x: x["iso"])


def pick_deadline(candidates: list[dict], fallback_iso: str, target_year: int | None = None) -> tuple[str, bool, float]:
    if not candidates:
        return fallback_iso, False, 0.0

    keyword_pool = [c for c in candidates if c["has_keyword"]]
    pool0 = keyword_pool if keyword_pool else candidates

    if target_year is not None:
        year_pool = [c for c in pool0 if date.fromisoformat(c["iso"]).year in {target_year, target_year - 1}]
        if year_pool:
            pool0 = year_pool

    firm = [c for c in pool0 if c["is_firm"]]
    pool = firm if firm else pool0
    chosen = max(pool, key=lambda x: x["iso"])

    keyword_hits = sum(1 for c in candidates if c["has_keyword"])
    confidence = 0.45
    if keyword_hits:
        confidence += 0.25
    if len(candidates) > 1:
        confidence += 0.1
    if chosen["is_firm"]:
        confidence += 0.15
    confidence = min(confidence, 0.95)
    return chosen["iso"], chosen["is_firm"], round(confidence, 2)


def extension_days_from_dates(dates_iso: list[str], final_iso: str, lookback_days: int = 120) -> int:
    if not dates_iso:
        return 0
    final_date = date.fromisoformat(final_iso)
    relevant = []
    for iso in sorted(set(dates_iso)):
        d = date.fromisoformat(iso)
        delta = (final_date - d).days
        if 0 <= delta <= lookback_days:
            relevant.append(d)
    if len(relevant) < 2:
        return 0
    return max(0, (max(relevant) - min(relevant)).days)


def infer_extension_by_family(
    results: list[dict],
    family: str,
    year: int | None,
    default_days: int,
    max_training_days: int,
) -> int:
    samples = []
    for r in results:
        if r.get("family") != family:
            continue
        if r.get("observed_extension_days", 0) <= 0:
            continue
        if int(r["observed_extension_days"]) > max_training_days:
            continue
        if year is not None and r.get("year") is not None and int(r["year"]) >= int(year):
            continue
        samples.append(int(r["observed_extension_days"]))
    if not samples:
        return default_days
    return int(round(statistics.median(samples)))


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    confs = load_yaml(CONFIG_PATH).get("conferences", [])
    rules = load_yaml(RULES_PATH)
    previous = {item["id"]: item for item in load_json(OUT_PATH, default=[])}
    history = load_json(HISTORY_PATH, default=[])

    now = datetime.now(timezone.utc).isoformat()
    results = []
    default_extension_days = int((rules.get("default") or {}).get("default_extension_days", 14))
    max_training_extension_days = int((rules.get("default") or {}).get("max_training_extension_days", 90))

    for conf in confs:
        rule = merge_rule_for_conference(conf, rules)
        crawl_urls = conf.get("crawl_urls") or [conf["url"]]
        all_lines: list[str] = []
        errors: list[str] = []
        success_count = 0
        for url in crawl_urls:
            lines, error = fetch_page_lines(url)
            if lines:
                all_lines.extend(lines)
                success_count += 1
            if error:
                errors.append(f"{url}: {error}")

        fallback_iso = normalize_iso_date(conf["fallback_deadline"])
        seed_dates = [normalize_iso_date(v) for v in conf.get("seed_submission_dates", [])]
        candidates = extract_candidates(all_lines, rule, target_year=conf.get("year")) if all_lines else []
        extracted_dates = [c["iso"] for c in candidates]

        all_dates = sorted(set(seed_dates + extracted_dates))
        deadline_iso, is_firm, confidence = pick_deadline(candidates, fallback_iso, conf.get("year"))
        if seed_dates:
            deadline_iso = max(seed_dates)
            is_firm = True
            confidence = max(confidence, 0.95)
        deadline_dt = date.fromisoformat(deadline_iso)
        all_dates = sorted({d for d in all_dates if date.fromisoformat(d) <= deadline_dt} | {deadline_iso})

        observed_extension_days = extension_days_from_dates(all_dates, final_iso=deadline_iso)

        result = {
            "id": conf["id"],
            "family": conf.get("family"),
            "year": conf.get("year"),
            "name": conf["name"],
            "short_name": conf.get("short_name"),
            "url": conf["url"],
            "deadline_iso": deadline_iso,
            "deadline_dates_found": all_dates,
            "is_firm_deadline": is_firm,
            "observed_extension_days": observed_extension_days,
            "confidence": confidence,
            "last_checked_utc": now,
            "status": "ok" if success_count > 0 else "fallback",
            "error": " | ".join(errors) if errors else None,
            "fallback_deadline": fallback_iso,
        }
        results.append(result)

        prev = previous.get(conf["id"])
        if not prev or prev.get("deadline_iso") != result["deadline_iso"]:
            history.append(
                {
                    "conference_id": conf["id"],
                    "seen_at_utc": now,
                    "deadline_iso": result["deadline_iso"],
                    "source": "crawler",
                    "is_firm_deadline": is_firm,
                }
            )

    for result in results:
        year = result.get("year")
        family = result.get("family")
        if result["is_firm_deadline"]:
            predicted_extension_days = 0
        else:
            predicted_extension_days = infer_extension_by_family(
                results=results,
                family=family,
                year=year,
                default_days=default_extension_days,
                max_training_days=max_training_extension_days,
            )
        deadline = date.fromisoformat(result["deadline_iso"])
        stretch_until = deadline + timedelta(days=predicted_extension_days)
        result["predicted_extension_days"] = int(predicted_extension_days)
        result["stretch_until_iso"] = stretch_until.isoformat()

    results.sort(key=lambda x: x["deadline_iso"])
    history = sorted(history, key=lambda x: x["seen_at_utc"])

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    with HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


if __name__ == "__main__":
    main()
