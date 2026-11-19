"""Job-board sources, fan-out, dedupe, and location ranking.

Port of CareerPilot's ``lib/agents/sources/*``. Each source adapter returns
``RawJob`` dicts in one shared shape and **never raises** — a missing API
key or a flaky endpoint just yields an empty list so the fan-out survives.

Sources:
  * remoteok  — open JSON feed (no key)
  * arbeitnow — open JSON API (no key)
  * themuse   — public API (optional THEMUSE_API_KEY raises the rate limit)
  * adzuna    — needs ADZUNA_APP_ID / ADZUNA_APP_KEY
  * tavily    — optional web search, needs TAVILY_API_KEY
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 12
USER_AGENT = 'Bunk/1.0 (careerpilot port)'
MAX_PER_SOURCE = 15
REMOTE_FEED_TTL_SECONDS = 300

REMOTE_LOC_HINTS = re.compile(
    r'\b(remote|anywhere|work from home|wfh|distributed|worldwide|global)\b', re.I
)
REMOTE_TITLE_HINTS = re.compile(
    r'\b(remote|anywhere|wfh|work[- ]from[- ]home|distributed)\b', re.I
)

_remoteok_cache = {'at': 0.0, 'data': []}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(*names, default=''):
    """Read a provider key from the environment, then Django settings.

    Settings are checked so deployment config lives in one place; the
    environment wins so a shell override still works during development.
    """
    for name in names:
        value = os.environ.get(name) or getattr(settings, f'CAREERPILOT_{name}', '')
        if value:
            return str(value).strip()
    return default


def _get_json(url, *, params=None, headers=None):
    try:
        response = requests.get(
            url,
            params=params,
            headers={'User-Agent': USER_AGENT, 'Accept': 'application/json', **(headers or {})},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning('[sources] GET %s failed: %s', url, exc)
        return None
    if response.status_code >= 400:
        logger.warning('[sources] GET %s -> %s', url, response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        logger.warning('[sources] %s returned non-JSON', url)
        return None


def strip_html(value: str, limit: int = 600) -> str:
    text = re.sub(r'<br\s*/?>', '\n', value or '', flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()[:limit]


def _flatten(value: str, limit: int) -> str:
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', value or '')
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def _iso_from_unix(seconds):
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(int(seconds), tz=dt_timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _job_type_from_terms(terms) -> str:
    joined = ' '.join(str(t).lower() for t in (terms or []))
    if 'intern' in joined:
        return 'internship'
    if 'contract' in joined or 'freelance' in joined:
        return 'contract'
    if 'part' in joined:
        return 'part-time'
    if 'research' in joined:
        return 'research'
    return 'full-time'


def _raw(source, external_id, title, company, url, **kwargs) -> dict:
    return {
        'source': source,
        'external_id': str(external_id or ''),
        'title': (title or '').strip() or 'Untitled role',
        'company': (company or '').strip() or 'Unknown',
        'location': (kwargs.get('location') or '') or None,
        'salary': kwargs.get('salary') or None,
        'deadline': kwargs.get('deadline') or None,
        'url': url or '',
        'snippet': kwargs.get('snippet') or '',
        'job_type': kwargs.get('job_type') or 'other',
        'posted_at': kwargs.get('posted_at') or None,
    }


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------

def search_remoteok(query: str, opts=None):
    opts = opts or {}
    now = time.time()
    if not _remoteok_cache['data'] or now - _remoteok_cache['at'] > REMOTE_FEED_TTL_SECONDS:
        payload = _get_json(
            'https://remoteok.com/api',
            params={'tags': '', 'action': 'getList'},
        )
        if isinstance(payload, list):
            _remoteok_cache['data'] = [p for p in payload if isinstance(p, dict) and p.get('id')]
            _remoteok_cache['at'] = now
    feed = _remoteok_cache['data']
    if not feed:
        return []

    terms = [t for t in re.split(r'\s+', (query or '').lower()) if len(t) > 1]

    def relevance(posting):
        haystack = ' '.join([
            str(posting.get('position', '')),
            str(posting.get('company', '')),
            str(posting.get('location', '')),
            ' '.join(posting.get('tags') or []),
        ]).lower()
        if not terms:
            return 1.0
        return sum(1 for t in terms if t in haystack) / len(terms)

    ranked = [(relevance(p), p) for p in feed]
    ranked = sorted(
        [pair for pair in ranked if pair[0] > 0],
        key=lambda pair: pair[0], reverse=True,
    )[:MAX_PER_SOURCE]

    jobs = []
    for _, posting in ranked:
        salary = None
        low, high = posting.get('salary_min'), posting.get('salary_max')
        if low and high:
            salary = f'${round(low / 1000)}k–${round(high / 1000)}k'
        jobs.append(_raw(
            'remoteok',
            posting.get('id'),
            posting.get('position'),
            posting.get('company'),
            posting.get('url') or f"https://remoteok.com/remote-jobs/{posting.get('id')}",
            location=(posting.get('location') or '').strip() or 'Remote',
            salary=salary,
            snippet=strip_html((posting.get('description') or '')[:600]),
            job_type=_job_type_from_terms(posting.get('tags')),
            posted_at=posting.get('date'),
        ))
    return jobs


def search_arbeitnow(query: str, opts=None):
    payload = _get_json(
        'https://www.arbeitnow.com/api/job-board-api',
        params={'search': query or ''},
    )
    if not isinstance(payload, dict):
        return []
    rows = payload.get('data') if isinstance(payload.get('data'), list) else []
    jobs = []
    for posting in rows[:MAX_PER_SOURCE]:
        jobs.append(_raw(
            'arbeitnow',
            posting.get('id') or posting.get('slug'),
            posting.get('title'),
            posting.get('company_name'),
            posting.get('url'),
            location=posting.get('location') or ('Remote' if posting.get('remote') else None),
            snippet=_flatten(posting.get('description') or '', 600),
            job_type=_job_type_from_terms(posting.get('job_types')),
            posted_at=_iso_from_unix(posting.get('created_at')),
        ))
    return jobs


def search_themuse(query: str, opts=None):
    headers = {}
    api_key = _env('THEMUSE_API_KEY')
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    params = {'page': 0, 'descending': 'true'}
    if (query or '').strip():
        params['category'] = query.strip()
    payload = _get_json('https://www.themuse.com/api/public/jobs', params=params, headers=headers)
    if not isinstance(payload, dict):
        return []
    rows = payload.get('results') if isinstance(payload.get('results'), list) else []
    jobs = []
    for posting in rows[:MAX_PER_SOURCE]:
        company = (posting.get('company') or {})
        refs = (posting.get('refs') or {})
        levels = [str((lvl or {}).get('short_name', '')).lower() for lvl in (posting.get('levels') or [])]
        jobs.append(_raw(
            'themuse',
            posting.get('id'),
            posting.get('name'),
            company.get('name'),
            refs.get('landing_page') or f"https://www.themuse.com/jobs/{company.get('id')}/{posting.get('id')}",
            location=posting.get('location'),
            snippet=strip_html(posting.get('contents') or ''),
            job_type=_job_type_from_terms(levels),
            posted_at=posting.get('publication_date'),
        ))
    return jobs


ADZUNA_COUNTRIES = {
    'gb', 'us', 'au', 'at', 'be', 'br', 'ca', 'ch', 'de', 'es', 'fr', 'in',
    'it', 'mx', 'nl', 'nz', 'pl', 'sg', 'za',
}

ADZUNA_ALIASES = {
    'uk': 'gb', 'england': 'gb', 'united kingdom': 'gb',
    'america': 'us', 'united states': 'us', 'u.s.': 'us',
    'bangladesh': 'in', 'britain': 'gb',
}


def _adzuna_country(location: str) -> str:
    loc = (location or '').lower().strip()
    if not loc:
        return 'gb'
    tail = re.split(r'[,\s]+', loc)[-1]
    if tail in ADZUNA_COUNTRIES:
        return tail
    for alias, code in ADZUNA_ALIASES.items():
        if alias in loc:
            return code
    return 'gb'


def _adzuna_salary(result, country):
    low, high = result.get('salary_min'), result.get('salary_max')
    if not low and not high:
        return None
    symbol = '£' if country == 'gb' else ('$' if country == 'us' else '')

    def fmt(value):
        return f'{symbol}{round(value / 1000)}k' if value >= 1000 else f'{symbol}{value}'

    if low and high:
        return f'{fmt(low)}–{fmt(high)}'
    if low:
        return f'from {fmt(low)}'
    return f'up to {fmt(high)}'


def search_adzuna(query: str, opts=None):
    opts = opts or {}
    app_id = _env('ADZUNA_APP_ID', 'Adzuna_API_ID')
    app_key = _env('ADZUNA_APP_KEY', 'Adzuna_API_KEY')
    if not app_id or not app_key:
        logger.info('[adzuna] skipped — ADZUNA_APP_ID / ADZUNA_APP_KEY not set')
        return []

    country = _adzuna_country(opts.get('location', ''))
    params = {
        'app_id': app_id,
        'app_key': app_key,
        'results_per_page': 20,
        'what': query or '',
        'content-type': 'application/json',
    }
    if opts.get('location'):
        params['where'] = opts['location']
    payload = _get_json(f'https://api.adzuna.com/v1/api/jobs/{country}/search/1', params=params)
    if not isinstance(payload, dict):
        return []
    rows = payload.get('results') if isinstance(payload.get('results'), list) else []
    jobs = []
    for posting in rows[:MAX_PER_SOURCE]:
        jobs.append(_raw(
            'adzuna',
            posting.get('id'),
            posting.get('title'),
            (posting.get('company') or {}).get('display_name'),
            posting.get('redirect_url'),
            location=(posting.get('location') or {}).get('display_name'),
            salary=_adzuna_salary(posting, country),
            snippet=_flatten(posting.get('description') or '', 280),
            job_type=_job_type_from_terms([
                posting.get('contract_type'), posting.get('contract_time'),
            ]),
            posted_at=posting.get('created'),
        ))
    return jobs


def search_tavily(query: str, opts=None):
    """Optional web-search source. No key → silently skipped."""
    api_key = _env('TAVILY_API_KEY')
    if not api_key:
        return []
    location = (opts or {}).get('location')
    search_query = f'{query} job opening' + (f' {location}' if location else '')
    try:
        response = requests.post(
            'https://api.tavily.com/search',
            json={
                'api_key': api_key,
                'query': search_query,
                'search_depth': 'basic',
                'max_results': 10,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            logger.warning('[tavily] HTTP %s', response.status_code)
            return []
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning('[tavily] failed: %s', exc)
        return []

    jobs = []
    for result in (payload.get('results') or [])[:MAX_PER_SOURCE]:
        url = result.get('url') or ''
        host = urlparse(url).netloc.replace('www.', '') or 'web'
        jobs.append(_raw(
            'tavily',
            url,
            result.get('title'),
            host,
            url,
            snippet=_flatten(result.get('content') or '', 400),
            job_type='other',
        ))
    return jobs


SOURCES = [search_remoteok, search_arbeitnow, search_themuse, search_adzuna, search_tavily]


# ---------------------------------------------------------------------------
# Query parsing, fan-out, dedupe, ranking
# ---------------------------------------------------------------------------

def parse_query(query: str) -> dict:
    """Split "senior backend engineer in london" into role + location."""
    trimmed = (query or '').strip()
    if not trimmed:
        return {'role': '', 'location': None}

    match = re.match(r'^(.+?)\s+in\s+(.+)$', trimmed, flags=re.I)
    if match:
        return {'role': match.group(1).strip(), 'location': match.group(2).strip()}

    if ',' in trimmed:
        head, _, tail = trimmed.partition(',')
        location = tail.strip()
        if head.strip() and location:
            return {'role': head.strip(), 'location': location}
    return {'role': trimmed, 'location': None}


def canonical_url(url: str) -> str:
    try:
        parsed = urlparse(url or '')
        query = [
            (key, value) for key, value in parse_qsl(parsed.query)
            if not re.match(r'^utm_', key, flags=re.I)
            and key not in {'ref', 'fbclid', 'gclid'}
        ]
        cleaned = parsed._replace(fragment='', query=urlencode(query))
        return urlunparse(cleaned).lower()
    except ValueError:
        return (url or '').lower()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (char_a != char_b),
            ))
        previous = current
    return previous[-1]


def _fuzzy_close(a: str, b: str, max_distance: int = 3) -> bool:
    if abs(len(a) - len(b)) > max_distance:
        return False
    return _levenshtein(a, b) <= max_distance


def _city(location) -> str:
    return ((location or '').split(',')[0] or '').strip().lower()


def dedupe(jobs):
    """Collapse duplicates by canonical URL, then fuzzy (title, company, city)."""
    by_url = {}
    ordered = []
    for job in jobs:
        key = canonical_url(job.get('url', ''))
        existing = by_url.get(key)
        if existing is not None:
            if len(job.get('snippet') or '') > len(existing.get('snippet') or ''):
                by_url[key] = job
                ordered[ordered.index(existing)] = job
            continue
        by_url[key] = job
        ordered.append(job)

    result = []
    for job in ordered:
        city = _city(job.get('location'))
        title = (job.get('title') or '').strip().lower()
        company = (job.get('company') or '').strip().lower()
        duplicate = None
        for kept in result:
            if (_fuzzy_close((kept.get('title') or '').strip().lower(), title, 3)
                    and _fuzzy_close((kept.get('company') or '').strip().lower(), company, 2)
                    and _city(kept.get('location')) == city):
                duplicate = kept
                break
        if duplicate is None:
            result.append(job)
        else:
            new_score = bool(job.get('salary')) + bool(job.get('posted_at'))
            old_score = bool(duplicate.get('salary')) + bool(duplicate.get('posted_at'))
            if new_score > old_score:
                result[result.index(duplicate)] = job

    return sorted(
        result,
        key=lambda j: (bool(j.get('salary')), j.get('posted_at') or ''),
        reverse=True,
    )


def _location_matches(job, target: str) -> bool:
    location = (job.get('location') or '').lower()
    if not location.strip():
        return False
    tokens = [
        token for token in re.split(r'[\s,]+', (target or '').lower())
        if token and token not in {'remote', 'anywhere'}
    ]
    return bool(tokens) and all(token in location for token in tokens)


def _is_remote(job) -> bool:
    if REMOTE_LOC_HINTS.search((job.get('location') or '').strip()):
        return True
    return bool(REMOTE_TITLE_HINTS.search(job.get('title') or ''))


def rank_by_location(jobs, target_location):
    if not (target_location or '').strip():
        return {'local': [], 'remote': [], 'other': list(jobs)}
    ranked = {'local': [], 'remote': [], 'other': []}
    for job in jobs:
        if _location_matches(job, target_location):
            ranked['local'].append(job)
        elif _is_remote(job):
            ranked['remote'].append(job)
        else:
            ranked['other'].append(job)
    return ranked


def fan_out(query: str):
    """Run every source (twice when a location is given) and dedupe."""
    parsed = parse_query(query)
    if not parsed['role']:
        return []

    has_location = bool((parsed['location'] or '').strip())
    calls = []
    for source in SOURCES:
        calls.append((source, {'location': parsed['location'], 'raw_query': query}))
        if has_location:
            calls.append((source, {'raw_query': query}))

    merged = []
    for source, opts in calls:
        try:
            merged.extend(source(parsed['role'], opts) or [])
        except Exception as exc:  # noqa: BLE001 - one source must never kill the fan-out
            logger.warning('[sources] %s failed: %s', getattr(source, '__name__', source), exc)
    return dedupe(merged)
