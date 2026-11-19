"""Transparent fit scoring — the heart of the Fit Score and Job Hunter features.

Ported from CareerPilot's ``lib/agents/fitScore.ts``. The score is
deliberately rule-based and explainable rather than an opaque LLM number:

    score = 0.7 * (matched must-have weight / total must-have weight)
          + 0.3 * (matched nice-to-have weight / total nice-to-have weight)

Verdict bands: strong >= 80, good >= 65, borderline >= 45, else weak.

The same breakdown powers "why did this job score 62?" in the UI and the
honest gap list the assistant quotes back.
"""

from __future__ import annotations

import re

# id, label, aliases, weight. Weights let a must-have skill that is central
# to a role dominate a peripheral one in the same bucket.
SKILLS = [
    {'id': 'python', 'label': 'Python', 'aliases': ['python'], 'weight': 3},
    {'id': 'javascript', 'label': 'JavaScript', 'aliases': ['javascript', 'js'], 'weight': 3},
    {'id': 'typescript', 'label': 'TypeScript', 'aliases': ['typescript'], 'weight': 3},
    {'id': 'java', 'label': 'Java', 'aliases': ['java'], 'weight': 3},
    {'id': 'csharp', 'label': 'C#', 'aliases': ['c#', 'csharp', '.net'], 'weight': 2},
    {'id': 'cpp', 'label': 'C++', 'aliases': ['c++', 'cpp'], 'weight': 2},
    {'id': 'c', 'label': 'C', 'aliases': ['c'], 'weight': 2},
    {'id': 'go', 'label': 'Go', 'aliases': ['golang'], 'weight': 2},
    {'id': 'rust', 'label': 'Rust', 'aliases': ['rust'], 'weight': 2},
    {'id': 'php', 'label': 'PHP', 'aliases': ['php', 'laravel'], 'weight': 2},
    {'id': 'ruby', 'label': 'Ruby', 'aliases': ['ruby', 'rails'], 'weight': 2},
    {'id': 'kotlin', 'label': 'Kotlin', 'aliases': ['kotlin'], 'weight': 2},
    {'id': 'swift', 'label': 'Swift', 'aliases': ['swift'], 'weight': 2},
    {'id': 'sql', 'label': 'SQL', 'aliases': ['sql'], 'weight': 3},
    {'id': 'django', 'label': 'Django', 'aliases': ['django'], 'weight': 3},
    {'id': 'flask', 'label': 'Flask', 'aliases': ['flask'], 'weight': 2},
    {'id': 'fastapi', 'label': 'FastAPI', 'aliases': ['fastapi'], 'weight': 2},
    {'id': 'node', 'label': 'Node.js', 'aliases': ['node.js', 'nodejs', 'node'], 'weight': 2},
    {'id': 'react', 'label': 'React', 'aliases': ['react', 'react.js', 'next.js', 'nextjs'], 'weight': 3},
    {'id': 'vue', 'label': 'Vue', 'aliases': ['vue', 'vue.js', 'nuxt'], 'weight': 2},
    {'id': 'angular', 'label': 'Angular', 'aliases': ['angular'], 'weight': 2},
    {'id': 'tailwind', 'label': 'Tailwind CSS', 'aliases': ['tailwind'], 'weight': 1},
    {'id': 'html_css', 'label': 'HTML/CSS', 'aliases': ['html', 'css', 'scss'], 'weight': 1},
    {'id': 'rest', 'label': 'REST APIs', 'aliases': ['rest', 'restful', 'api design'], 'weight': 2},
    {'id': 'graphql', 'label': 'GraphQL', 'aliases': ['graphql'], 'weight': 2},
    {'id': 'postgres', 'label': 'PostgreSQL', 'aliases': ['postgresql', 'postgres'], 'weight': 2},
    {'id': 'mysql', 'label': 'MySQL', 'aliases': ['mysql', 'mariadb'], 'weight': 2},
    {'id': 'mongodb', 'label': 'MongoDB', 'aliases': ['mongodb', 'mongo'], 'weight': 2},
    {'id': 'redis', 'label': 'Redis', 'aliases': ['redis'], 'weight': 1},
    {'id': 'docker', 'label': 'Docker', 'aliases': ['docker'], 'weight': 2},
    {'id': 'kubernetes', 'label': 'Kubernetes', 'aliases': ['kubernetes', 'k8s'], 'weight': 2},
    {'id': 'aws', 'label': 'AWS', 'aliases': ['aws', 'amazon web services'], 'weight': 2},
    {'id': 'gcp', 'label': 'Google Cloud', 'aliases': ['gcp', 'google cloud'], 'weight': 2},
    {'id': 'azure', 'label': 'Azure', 'aliases': ['azure'], 'weight': 2},
    {'id': 'ci_cd', 'label': 'CI/CD', 'aliases': ['ci/cd', 'cicd', 'github actions', 'jenkins'], 'weight': 2},
    {'id': 'terraform', 'label': 'Terraform', 'aliases': ['terraform'], 'weight': 2},
    {'id': 'linux', 'label': 'Linux', 'aliases': ['linux', 'unix', 'ubuntu'], 'weight': 2},
    {'id': 'git', 'label': 'Git', 'aliases': ['git', 'github', 'gitlab'], 'weight': 1},
    {'id': 'pandas', 'label': 'pandas', 'aliases': ['pandas'], 'weight': 2},
    {'id': 'numpy', 'label': 'NumPy', 'aliases': ['numpy'], 'weight': 2},
    {'id': 'ml', 'label': 'Machine Learning', 'aliases': ['machine learning', 'ml', 'deep learning'], 'weight': 3},
    {'id': 'nlp', 'label': 'NLP', 'aliases': ['nlp', 'natural language processing'], 'weight': 2},
    {'id': 'pytorch', 'label': 'PyTorch', 'aliases': ['pytorch', 'torch'], 'weight': 2},
    {'id': 'tensorflow', 'label': 'TensorFlow', 'aliases': ['tensorflow', 'keras'], 'weight': 2},
    {'id': 'sql_analytics', 'label': 'Data Analysis', 'aliases': ['data analysis', 'analytics', 'data analytics'], 'weight': 2},
    {'id': 'etl', 'label': 'ETL', 'aliases': ['etl', 'data pipeline', 'airflow'], 'weight': 2},
    {'id': 'spark', 'label': 'Spark', 'aliases': ['spark', 'pyspark'], 'weight': 2},
    {'id': 'powerbi', 'label': 'Power BI', 'aliases': ['power bi', 'powerbi', 'tableau'], 'weight': 1},
    {'id': 'excel', 'label': 'Excel', 'aliases': ['excel', 'spreadsheet'], 'weight': 1},
    {'id': 'statistics', 'label': 'Statistics', 'aliases': ['statistics', 'statistical', 'probability'], 'weight': 2},
    {'id': 'figma', 'label': 'Figma', 'aliases': ['figma'], 'weight': 1},
    {'id': 'ui_ux', 'label': 'UI/UX Design', 'aliases': ['ui/ux', 'ux design', 'ui design', 'user experience'], 'weight': 2},
    {'id': 'agile', 'label': 'Agile', 'aliases': ['agile', 'scrum', 'kanban'], 'weight': 1},
    {'id': 'project_mgmt', 'label': 'Project Management', 'aliases': ['project management', 'stakeholder management'], 'weight': 1},
    {'id': 'communication', 'label': 'Communication', 'aliases': ['communication', 'presentation', 'written communication'], 'weight': 1},
    {'id': 'leadership', 'label': 'Leadership', 'aliases': ['leadership', 'mentoring', 'team lead'], 'weight': 1},
    {'id': 'problem_solving', 'label': 'Problem Solving', 'aliases': ['problem solving', 'analytical'], 'weight': 1},
    {'id': 'testing', 'label': 'Testing', 'aliases': ['unit testing', 'pytest', 'jest', 'test automation', 'qa'], 'weight': 2},
    {'id': 'security', 'label': 'Security', 'aliases': ['security', 'cybersecurity', 'owasp'], 'weight': 2},
    {'id': 'mobile', 'label': 'Mobile Development', 'aliases': ['android', 'ios', 'react native', 'flutter'], 'weight': 2},
    {'id': 'textile', 'label': 'Textile Engineering', 'aliases': ['textile', 'spinning', 'weaving', 'knitting'], 'weight': 2},
    {'id': 'production', 'label': 'Production Engineering', 'aliases': ['industrial engineering', 'production planning', 'lean manufacturing', 'supply chain'], 'weight': 2},
    {'id': 'cad', 'label': 'CAD', 'aliases': ['cad', 'solidworks', 'autocad', 'catia'], 'weight': 2},
    {'id': 'quality', 'label': 'Quality Assurance', 'aliases': ['quality assurance', 'quality control', 'six sigma'], 'weight': 2},
    {'id': 'research', 'label': 'Research', 'aliases': ['research', 'thesis', 'literature review'], 'weight': 1},
    {'id': 'writing', 'label': 'Technical Writing', 'aliases': ['technical writing', 'documentation'], 'weight': 1},
]

VERDICT_BANDS = [
    ('strong', 80),
    ('good', 65),
    ('borderline', 45),
    ('weak', 0),
]

MUST_HEADING = re.compile(
    r'^(requirements|must[ -]haves?|required|qualifications|essential|'
    r'what you.ll need|who you are)\b', re.I,
)
NICE_HEADING = re.compile(
    r'^(nice[ -]to[ -]haves?|preferred|bonus|desirable|plus|additional)\b', re.I,
)
MUST_INLINE = re.compile(r'\b(must|required|essential|minimum)\b', re.I)

MAX_JD_EXCERPT = 8192


def _token_map():
    mapping = {}
    for skill in SKILLS:
        for alias in [skill['label'], *skill['aliases']]:
            mapping[alias.lower()] = skill
    return mapping


def extract_skills(text: str):
    """Unique skills mentioned in ``text`` (token-bounded, case-insensitive)."""
    mapping = _token_map()
    lowered = f' {(text or "").lower()} '
    raw = text or ''
    found = {}
    for token, skill in mapping.items():
        if skill['id'] in found:
            continue
        if len(token) <= 2:
            if re.search(rf'\b{re.escape(token)}\b', raw, flags=re.I):
                found[skill['id']] = skill
        elif token in lowered:
            found[skill['id']] = skill
    return list(found.values())


def split_requirements(jd_text: str):
    """Split a JD into must-have and nice-to-have text buckets.

    Headings win (``Requirements`` → must, ``Nice to have`` → nice). With no
    headings we fall back to must-flag keywords, then treat unmatched lines
    as nice-to-have so nothing is silently dropped.
    """
    must, nice = [], []
    bucket = None
    for line in (jd_text or '').split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if MUST_HEADING.match(stripped):
            bucket = must
            continue
        if NICE_HEADING.match(stripped):
            bucket = nice
            continue
        if len(stripped) > 200:
            if bucket is must:
                must.append(stripped)
            elif bucket is nice:
                nice.append(stripped)
            else:
                (must if MUST_INLINE.search(stripped) else nice).append(stripped)
            continue
        if bucket is not None:
            bucket.append(stripped)
        elif MUST_INLINE.search(stripped):
            must.append(stripped)
        else:
            nice.append(stripped)
    return ' \n'.join(must), ' \n'.join(nice)


def verdict_for(score: int) -> str:
    for label, minimum in VERDICT_BANDS:
        if score >= minimum:
            return label
    return 'weak'


def _weight(skill) -> int:
    return int(skill.get('weight') or 1)


def _score_bucket(required, available_ids):
    """Return (matched, missing, coverage_ratio) for one skill bucket."""
    matched, missing = [], []
    total = sum(_weight(skill) for skill in required) or 1
    matched_weight = 0
    for skill in required:
        if skill['id'] in available_ids:
            matched.append(skill)
            matched_weight += _weight(skill)
        else:
            missing.append(skill)
    return matched, missing, matched_weight / total


def score_fit(cv_text: str, jd_text: str = '', target_role: str = '', benchmark_key: str = '_freeform'):
    """Compute the transparent fit result.

    ``cv_text`` is the active CV (or profile summary). ``jd_text`` is the job
    description; when omitted only the ``target_role`` phrase is scored, which
    keeps the endpoint useful for "am I ready for X?" without a JD pasted in.
    """
    cv_text = cv_text or ''
    combined_target = f'{target_role}\n{jd_text}'.strip()

    available = {skill['id']: skill for skill in extract_skills(cv_text)}
    must_text, nice_text = split_requirements(combined_target)

    must_skills = extract_skills(must_text) or extract_skills(combined_target)
    nice_skills = [
        skill for skill in extract_skills(nice_text)
        if skill['id'] not in {s['id'] for s in must_skills}
    ]

    matched_must, missing_must, must_ratio = _score_bucket(must_skills, available)
    matched_nice, missing_nice, nice_ratio = _score_bucket(nice_skills, available)

    if must_skills:
        total = 0.7 * must_ratio + 0.3 * (nice_ratio if nice_skills else must_ratio)
    elif available:
        # No JD → score on how well the CV covers its own target vocabulary.
        total = must_ratio if must_skills else 0.0
    else:
        total = 0.0

    score = int(round(max(0.0, min(1.0, total)) * 100))

    matched = [
        {'id': s['id'], 'label': s['label'], 'weight': _weight(s)} for s in matched_must
    ] + [
        {'id': s['id'], 'label': s['label'], 'weight': _weight(s)} for s in matched_nice
    ]
    missing = [
        {'id': s['id'], 'label': s['label'], 'weight': _weight(s)} for s in missing_must
    ]

    result = {
        'score': score,
        'verdict': verdict_for(score),
        'benchmark_used': benchmark_key or '_freeform',
        'breakdown': {
            'must_have_coverage': round(must_ratio, 4),
            'nice_to_have_coverage': round(nice_ratio, 4),
            'weights': {'must_have': 0.7, 'nice_to_have': 0.3},
            'must_have_total': len(must_skills),
            'nice_to_have_total': len(nice_skills),
        },
        'matched': matched,
        'missing': missing,
        'matched_nice_to_have': [
            {'id': s['id'], 'label': s['label'], 'weight': _weight(s)} for s in matched_nice
        ],
        'missing_nice_to_have': [
            {'id': s['id'], 'label': s['label'], 'weight': _weight(s)} for s in missing_nice
        ],
    }
    result['rationale'] = build_rationale(score, matched, missing, target_role or 'this role')
    return result


def build_rationale(score, matched, missing, target):
    strengths = ', '.join(item['label'] for item in matched[:4]) or 'none identified'
    gaps = ', '.join(item['label'] for item in missing[:3]) or 'no critical gaps'
    return (
        f'{score}/100 for {target}. Top strengths: {strengths}. '
        f'Top gaps: {gaps}.'
    )


def job_fit(job: dict, cv_text: str):
    """Score one hunter result against the CV, with readable highlights."""
    jd_text = '\n'.join(filter(None, [
        job.get('title', ''),
        job.get('snippet', ''),
        job.get('description', ''),
    ]))
    result = score_fit(cv_text, jd_text, target_role=job.get('title', ''))
    highlights = [item['label'] for item in result['matched'][:3]]
    concerns = [item['label'] for item in result['missing'][:3]]
    return {
        'fit_score': result['score'],
        'fit_reason': result['rationale'],
        'match_highlights': highlights,
        'concerns': concerns,
        'breakdown': result['breakdown'],
    }
