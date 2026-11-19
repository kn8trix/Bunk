"""CV ingestion: text extraction, section-aware chunking, embedding, retrieval.

Mirrors CareerPilot's ``lib/cv/parse.ts`` + ``lib/cv/chunk.ts`` +
``lib/cv/ingest.ts`` + ``lib/rag/retrieve-cv.ts``, but uses this project's
existing embedding layer (``services.embeddings``) instead of Gemini.

Extraction:
  * ``.pdf``  → pypdf
  * ``.docx`` → python-docx (paragraphs + table cells)
  * ``.txt``  → plain text

Chunks are section-scoped (header / experience / education / skills / …)
so citations can point at "Experience > Acme Corp (2024)" rather than a
meaningless byte range. Nothing here touches the network unless an
``EMBEDDINGS_API_KEY`` is configured — ``services.embeddings`` falls back
to deterministic hashing embeddings otherwise.
"""

from __future__ import annotations

import math
import re

from services import embeddings as embedding_service

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = ('.pdf', '.docx', '.txt')


class CvParseError(ValueError):
    """Raised when a CV file cannot be turned into text."""


def _ext(filename: str) -> str:
    name = (filename or '').lower()
    for ext in SUPPORTED_EXTENSIONS:
        if name.endswith(ext):
            return ext
    raise CvParseError(
        'Unsupported file type. Upload a PDF, DOCX, or TXT file.'
    )


def extract_text(fileobj, filename: str) -> str:
    """Return plain text for a PDF/DOCX/TXT file object."""
    ext = _ext(filename)
    if ext == '.pdf':
        text = _extract_pdf(fileobj)
    elif ext == '.docx':
        text = _extract_docx(fileobj)
    else:
        text = _extract_txt(fileobj)
    text = _normalise(text)
    if not text:
        raise CvParseError(
            'No text could be extracted from that file. '
            'If it is a scanned PDF, upload a text-based copy.'
        )
    return text


def _extract_pdf(fileobj) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(fileobj)
    except Exception as exc:  # noqa: BLE001 - surfaced to the API caller
        raise CvParseError(f'Could not read that PDF: {exc}') from exc
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or '')
        except Exception:  # noqa: BLE001 - one bad page shouldn't kill the CV
            continue
    return '\n'.join(pages)


def _extract_docx(fileobj) -> str:
    import docx

    try:
        document = docx.Document(fileobj)
    except Exception as exc:  # noqa: BLE001
        raise CvParseError(f'Could not read that DOCX: {exc}') from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(' | '.join(cells))
    return '\n'.join(parts)


def _extract_txt(fileobj) -> str:
    raw = fileobj.read()
    if isinstance(raw, bytes):
        for encoding in ('utf-8', 'utf-16', 'latin-1'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', 'replace')
    return str(raw)


def _normalise(text: str) -> str:
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Section detection (ported from lib/cv/chunk.ts SECTION_RULES)
# ---------------------------------------------------------------------------

SECTION_RULES = [
    (r'professional experience', 'experience', 'exp'),
    (r'work experience', 'experience', 'exp'),
    (r'research experience', 'experience', 'exp'),
    (r'teaching experience', 'experience', 'exp'),
    (r'experience', 'experience', 'exp'),
    (r'internships?', 'experience', 'exp'),
    (r'education', 'education', 'edu'),
    (r'technical skills', 'technical_skills', 'skills'),
    (r'skills', 'technical_skills', 'skills'),
    (r'languages', 'technical_skills', 'skills'),
    (r'projects', 'projects', 'proj'),
    (r'publications and patents', 'publications', 'pub'),
    (r'publications?', 'publications', 'pub'),
    (r'patents?', 'publications', 'pub'),
    (r'scholastic achievements', 'awards', 'awards'),
    (r'achievements', 'awards', 'awards'),
    (r'awards', 'awards', 'awards'),
    (r'certifications?', 'certifications', 'cert'),
    (r'positions of responsibility', 'other', 'other'),
    (r'extra[ -]?curricular', 'other', 'other'),
    (r'courses', 'other', 'other'),
    (r'summary', 'summary', 'summary'),
    (r'objective', 'summary', 'summary'),
    (r'profile', 'summary', 'summary'),
]

_HEADING_MAX_CHARS = 64
_CONTACT_HINTS = re.compile(
    r'(@|\+?\d[\d\s\-()]{6,}|linkedin|github|portfolio|https?://)', re.I
)

DEFAULT_CHUNK_CHARS = 1000
MIN_CHUNK_CHARS = 60


def _match_heading(line: str):
    """Return (section, slug) when ``line`` is a recognised section heading."""
    candidate = line.strip().rstrip(':').strip()
    if not candidate or len(candidate) > _HEADING_MAX_CHARS:
        return None
    # Headings are short, title/upper-case lines with no sentence punctuation.
    if candidate.endswith('.'):
        return None
    lowered = candidate.lower()
    for pattern, section, slug in SECTION_RULES:
        if re.fullmatch(pattern, lowered, flags=re.I):
            return section, slug
    return None


def split_into_sections(text: str):
    """Bucket raw CV lines into (section, heading, body) triples.

    The leading block before any recognised heading is reclassified as
    ``header`` when it looks like contact info (email / phone / links),
    otherwise it stays a ``summary``.
    """
    lines = text.split('\n')
    sections = []
    current = {'section': 'summary', 'heading': '', 'lines': []}

    for line in lines:
        heading = _match_heading(line)
        if heading:
            if current['lines'] or current['heading']:
                sections.append(current)
            current = {
                'section': heading[0],
                'heading': line.strip().rstrip(':').strip(),
                'lines': [],
            }
        else:
            current['lines'].append(line)
    sections.append(current)

    # Reclassify a leading contact block as `header`.
    if sections:
        first = sections[0]
        body = '\n'.join(first['lines']).strip()
        if first['section'] == 'summary' and not first['heading']:
            is_short = len(body.split('\n')) <= 8
            if body and (is_short and _CONTACT_HINTS.search(body)):
                first['section'] = 'header'
                first['heading'] = first['heading'] or 'Contact'

    return sections


def _split_body(body: str, limit: int = DEFAULT_CHUNK_CHARS):
    """Split a section body into ~``limit``-character pieces on blank lines."""
    body = body.strip()
    if len(body) <= limit:
        return [body] if body else []
    blocks = re.split(r'\n\s*\n', body)
    chunks, buffer = [], ''
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(buffer) + len(block) + 2 <= limit:
            buffer = f'{buffer}\n\n{block}'.strip()
        else:
            if buffer:
                chunks.append(buffer)
            # A single oversized block is hard-wrapped.
            while len(block) > limit:
                chunks.append(block[:limit])
                block = block[limit:]
            buffer = block
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS] or ([body] if body else [])


def chunk_cv(text: str, cv_name: str = 'CV'):
    """Return chunk dicts ready for ``CvChunk`` rows.

    Each dict: ``section``, ``section_label``, ``content``, ``ordinality``,
    ``token_count``. The section heading is prepended to the embedded text
    so a fragment like "Acme Corp — backend intern" keeps its context.
    """
    chunks = []
    seen_per_kind = {}
    ordinality = 0

    for raw in split_into_sections(text):
        heading = raw['heading'] or raw['section'].replace('_', ' ').title()
        seen_per_kind[raw['section']] = seen_per_kind.get(raw['section'], 0) + 1
        kind_ordinal = seen_per_kind[raw['section']]

        for piece in _split_body('\n'.join(raw['lines'])):
            ordinality += 1
            first_line = next(
                (ln.strip() for ln in piece.split('\n') if ln.strip()), ''
            )[:70]
            label = heading if kind_ordinal == 1 else f'{heading} ({kind_ordinal})'
            if first_line and first_line.lower() != heading.lower():
                label = f'{label} > {first_line}'
            content = f'{heading}\n{piece}'.strip()
            chunks.append({
                'section': raw['section'],
                'section_label': label[:255].rstrip(' >'),
                'content': content,
                'ordinality': ordinality,
                'token_count': len(content.split()),
            })
    return chunks


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_cv(cv) -> int:
    """Parse ``cv.file``, chunk it, embed it, and persist ``CvChunk`` rows.

    Returns the number of chunks written. Marks the CV ``ready`` on success
    and ``failed`` (with ``error_message``) otherwise, so a bad upload never
    leaves a half-built CV looking usable.
    """
    from careerpilot.models import CvChunk

    if not cv.file:
        raise CvParseError('That CV has no uploaded file to parse.')

    try:
        with cv.file.open('rb') as handle:
            text = extract_text(handle, cv.file.name)
    except CvParseError as exc:
        cv.status = cv.STATUS_FAILED
        cv.error_message = str(exc)
        cv.save(update_fields=['status', 'error_message', 'updated_at'])
        raise

    chunks = chunk_cv(text, cv.name)

    cv.chunks.all().delete()
    if chunks:
        vectors = embedding_service.embed_texts([c['content'] for c in chunks])
    else:
        vectors = []

    CvChunk.objects.bulk_create([
        CvChunk(
            cv=cv,
            user=cv.user,
            section=chunk['section'],
            section_label=chunk['section_label'],
            content=chunk['content'],
            embedding=vectors[index] if index < len(vectors) else [],
            ordinality=chunk['ordinality'],
            token_count=chunk['token_count'],
        )
        for index, chunk in enumerate(chunks)
    ])

    cv.raw_text = text
    cv.section_index = {
        c['section']: [c['ordinality'], c['ordinality']] for c in chunks
    }
    cv.status = cv.STATUS_READY
    cv.error_message = ''
    cv.save(update_fields=[
        'raw_text', 'section_index', 'status', 'error_message', 'updated_at',
    ])
    return len(chunks)


def save_builder_cv(user, text: str, name: str = 'Builder CV'):
    """Store a builder-serialised CV (no uploaded file) and chunk it."""
    from careerpilot.models import Cv

    Cv.objects.filter(user=user, is_active=True).update(is_active=False)
    cv = Cv.objects.create(
        user=user, name=name, source=Cv.SOURCE_BUILDER,
        raw_text=text, is_active=True,
    )
    chunks = chunk_cv(text, name)
    if chunks:
        vectors = embedding_service.embed_texts([c['content'] for c in chunks])
        from careerpilot.models import CvChunk
        CvChunk.objects.bulk_create([
            CvChunk(
                cv=cv, user=user,
                section=c['section'], section_label=c['section_label'],
                content=c['content'],
                embedding=vectors[i] if i < len(vectors) else [],
                ordinality=c['ordinality'], token_count=c['token_count'],
            )
            for i, c in enumerate(chunks)
        ])
    cv.status = Cv.STATUS_READY
    cv.save(update_fields=['status', 'updated_at'])
    return cv


# ---------------------------------------------------------------------------
# Retrieval (the RAG seam the assistant and fit scorer use)
# ---------------------------------------------------------------------------

def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = sum(float(a[i]) * float(b[i]) for i in range(size))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a[:size]))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b[:size]))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_overlap(query_terms, text: str) -> float:
    if not query_terms:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for term in query_terms if term in lowered)
    return hits / len(query_terms)


def active_cv(user):
    if not user or not user.is_authenticated:
        return None
    from careerpilot.models import Cv

    return Cv.objects.filter(user=user, is_active=True).first()


def retrieve_chunks(user, query: str, k: int = 5):
    """Top-k CV chunks for ``query`` as citation dicts.

    Uses cosine similarity over stored embeddings; falls back to lexical
    overlap when a chunk has no vector (e.g. embeddings provider down), so
    the assistant always has some grounding and never 500s.
    """
    cv = active_cv(user)
    if cv is None:
        return []

    chunks = list(cv.chunks.all())
    if not chunks:
        return []

    query = (query or '').strip()
    if not query:
        return []

    terms = [t for t in re.split(r'\W+', query.lower()) if len(t) > 2]
    vector = embedding_service.embed_text(query)
    scored = []
    for chunk in chunks:
        if vector and chunk.embedding:
            score = _cosine(vector, chunk.embedding)
        else:
            score = _keyword_overlap(terms, chunk.content)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            'id': chunk.pk,
            'section': chunk.section,
            'section_label': chunk.section_label,
            'excerpt': chunk.content[:600],
            'similarity': round(score, 4),
            'source_image_url': chunk.source_image_url or None,
        }
        for score, chunk in scored[:k]
    ]
