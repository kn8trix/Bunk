"""The career assistant: intent routing, RAG grounding, and mode personas.

Port of CareerPilot's ``lib/agents/assistant.ts``, extended with Bunk's
tri-role framing (Guide · Consultant · Judge) alongside the original
readiness / gap_analysis / roadmap / cover_letter / general modes.

Every reply is grounded in the user's active CV chunks and returns those
chunks as citations, so the UI can show exactly which CV lines an answer
came from. When no LLM key is configured the service degrades to a
deterministic, still-grounded answer instead of failing.
"""

from __future__ import annotations

import logging
import re

from careerpilot.services import fit_score as fit_service
from careerpilot.services.cv import active_cv, retrieve_chunks
from services import openrouter

logger = logging.getLogger(__name__)

MODE_READINESS = 'readiness'
MODE_GAP = 'gap_analysis'
MODE_ROADMAP = 'roadmap'
MODE_COVER_LETTER = 'cover_letter'
MODE_GENERAL = 'general'
MODE_GUIDE = 'guide'
MODE_CONSULTANT = 'consultant'
MODE_JUDGE = 'judge'

ALL_MODES = [
    MODE_GENERAL, MODE_READINESS, MODE_GAP, MODE_ROADMAP, MODE_COVER_LETTER,
    MODE_GUIDE, MODE_CONSULTANT, MODE_JUDGE,
]

PERSONAS = {
    MODE_GENERAL: (
        'You are Bunk\'s career assistant. Answer using the CV excerpts provided. '
        'Be specific, concise, and never invent experience the candidate does not have.'
    ),
    MODE_GUIDE: (
        'You are Bunk\'s Guide: a patient career navigator. Map the candidate\'s '
        'next concrete steps from where they actually are, using only the CV '
        'excerpts provided. Prefer sequenced, achievable actions over generic advice.'
    ),
    MODE_CONSULTANT: (
        'You are Bunk\'s Consultant: an industry-insider advisor. Give market-aware '
        'guidance on roles, skills employers currently pay for, and positioning. '
        'Ground claims about the candidate in the CV excerpts provided; label '
        'industry trends as general market context rather than personal fact.'
    ),
    MODE_JUDGE: (
        'You are Bunk\'s Judge: a blunt, fair evaluator. State readiness plainly, '
        'name the strongest evidence from the CV excerpts, and name the specific '
        'gaps. Do not flatter. Give a verdict and what would change it.'
    ),
    MODE_READINESS: (
        'You are a candid hiring evaluator. Based only on the CV excerpts, answer '
        'whether this candidate is ready for the target role. Give a verdict, the '
        'top 3 supporting strengths, and the top gaps. No filler.'
    ),
    MODE_GAP: (
        'You are a skills-gap analyst. List the missing must-have skills for the '
        'target role, ranked by hiring impact, and one concrete way to close each. '
        'Only cite evidence present in the CV excerpts.'
    ),
    MODE_ROADMAP: (
        'You are a career planner. Produce a week-by-week plan to close the named '
        'gaps for the target role. Each week needs one focus and one deliverable '
        'the candidate can show. Keep it realistic for a student.'
    ),
    MODE_COVER_LETTER: (
        'You draft cover letters. Use only experience present in the CV excerpts. '
        'Professional tone, three short paragraphs, no invented metrics, and no '
        'placeholders except [Company] if the company is unknown.'
    ),
}

INTENT_PATTERNS = [
    (MODE_COVER_LETTER, re.compile(r'\bcover letter|covering letter\b', re.I)),
    (MODE_ROADMAP, re.compile(r'\broadmap|study plan|\d+[ -]?week plan|learning path\b', re.I)),
    (MODE_GAP, re.compile(r'\b(gaps?|missing|what am i lacking|skill gap)\b', re.I)),
    (MODE_READINESS, re.compile(r'\b(am i ready|readiness|ready for|qualified for)\b', re.I)),
]

HISTORY_TURNS = 10
MAX_CITATIONS = 5


def detect_intent(message: str) -> str:
    """Cheap deterministic intent router (no model call needed)."""
    for mode, pattern in INTENT_PATTERNS:
        if pattern.search(message or ''):
            return mode
    return MODE_GENERAL


def resolve_mode(mode, message: str) -> str:
    if mode in ALL_MODES:
        return mode
    return detect_intent(message)


def _cv_text(user) -> str:
    cv = active_cv(user)
    return (cv.raw_text if cv else '') or ''


def _citation_block(citations) -> str:
    if not citations:
        return 'No CV excerpts were retrieved for this question.'
    lines = []
    for index, citation in enumerate(citations, start=1):
        lines.append(
            f'[{index}] {citation["section_label"]} '
            f'(relevance {citation["similarity"]:.2f})\n{citation["excerpt"]}'
        )
    return '\n\n'.join(lines)


def _history_messages(thread) -> list:
    if thread is None:
        return []
    turns = list(thread.messages.order_by('-created_at')[:HISTORY_TURNS])
    turns.reverse()
    return [
        {
            'role': 'user' if turn.role == 'user' else 'assistant',
            'content': turn.content,
        }
        for turn in turns
        if turn.content
    ]


def _fallback_answer(mode: str, message: str, citations, fit_result=None) -> str:
    """Grounded, deterministic reply used when no LLM is available."""
    evidence = (
        f'From your CV: {citations[0]["section_label"]} — '
        f'{citations[0]["excerpt"][:220].strip()}'
        if citations else
        'No CV is indexed yet, so I can only give general guidance.'
    )

    if mode in {MODE_READINESS, MODE_JUDGE} and fit_result:
        gaps = ', '.join(item['label'] for item in fit_result['missing'][:3]) or 'no critical gaps'
        strengths = (
            ', '.join(item['label'] for item in fit_result['matched'][:3]) or 'nothing detected yet'
        )
        return (
            f'Verdict: {fit_result["verdict"]} ({fit_result["score"]}/100). '
            f'Evidence for: {strengths}. Gaps to close: {gaps}. {evidence}'
        )
    if mode == MODE_GAP and fit_result:
        gaps = fit_result['missing'][:3]
        if not gaps:
            return f'No must-have gaps detected in this description. {evidence}'
        bullets = '\n'.join(f'- {gap["label"]}' for gap in gaps)
        return f'Missing must-haves, highest impact first:\n{bullets}\n\n{evidence}'
    if mode == MODE_ROADMAP:
        return (
            'A four-week starting plan:\n'
            '- Week 1: audit the gaps above and pick one primary skill.\n'
            '- Week 2: build one small project that exercises that skill.\n'
            '- Week 3: rewrite the CV bullet for that project with a measurable result.\n'
            '- Week 4: apply to three target roles and note the rejections.\n\n'
            f'{evidence}'
        )
    if mode == MODE_COVER_LETTER:
        return (
            'I need a live model to draft a tailored letter.\n\n'
            f'Available material: {evidence}'
        )
    if mode == MODE_CONSULTANT:
        return (
            'Market positioning summary (model unavailable, so no live trend data): '
            'lead with your strongest shipped work, quantify it, and match the '
            f'vocabulary of the job description.\n\n{evidence}'
        )
    if mode == MODE_GUIDE:
        return (
            'Next steps, in order: (1) close the top listed gap, (2) ship one '
            'portfolio artefact that proves it, (3) apply to roles that name that '
            f'skill in the requirements.\n\n{evidence}'
        )
    return f'Here is what your CV supports:\n\n{evidence}'


def build_structured_result(mode: str, message: str, fit_result, weeks: int = 6):
    """Mode-specific payload persisted alongside the reply."""
    if fit_result is None:
        return {}
    if mode in {MODE_READINESS, MODE_GAP, MODE_JUDGE}:
        return {
            'fit_score': fit_result['score'],
            'verdict': fit_result['verdict'],
            'matched': fit_result['matched'],
            'missing': fit_result['missing'],
            'breakdown': fit_result['breakdown'],
            'rationale': fit_result['rationale'],
        }
    if mode == MODE_ROADMAP:
        gaps = [item['label'] for item in fit_result['missing'][:weeks]] or ['core skill']
        return {
            'weeks': [
                {
                    'week': index + 1,
                    'focus': gap,
                    'deliverable': f'A small project or write-up demonstrating {gap}',
                }
                for index, gap in enumerate(gaps[:weeks])
            ],
            'fit_score': fit_result['score'],
        }
    return {'fit_score': fit_result['score'], 'verdict': fit_result['verdict']}


def answer(user, message: str, mode=None, thread=None):
    """Produce one assistant turn.

    Returns ``{'mode', 'message', 'citations', 'structured_result',
    'model', 'grounded'}``. Never raises for LLM/transport problems.
    """
    message = (message or '').strip()
    resolved_mode = resolve_mode(mode, message)
    citations = retrieve_chunks(user, message, k=MAX_CITATIONS)
    cv_text = _cv_text(user)

    fit_result = None
    if resolved_mode in {MODE_READINESS, MODE_GAP, MODE_ROADMAP, MODE_JUDGE}:
        fit_result = fit_service.score_fit(
            cv_text, jd_text=message, target_role=message[:120],
        )

    structured = build_structured_result(resolved_mode, message, fit_result)

    prompt = (
        f'Mode: {resolved_mode}\n'
        f'Question: {message}\n\n'
        f'CV excerpts:\n{_citation_block(citations)}'
    )
    messages = _history_messages(thread) + [{'role': 'user', 'content': prompt}]

    if openrouter.is_enabled():
        try:
            text, model_used = openrouter.call_openrouter(
                messages, system_prompt=PERSONAS.get(resolved_mode, PERSONAS[MODE_GENERAL]),
            )
            return {
                'mode': resolved_mode,
                'message': (text or '').strip(),
                'citations': citations,
                'structured_result': structured,
                'model': model_used,
                'grounded': bool(citations),
            }
        except Exception as exc:  # noqa: BLE001 - degrade, never 500 the chat
            logger.warning('[assistant] LLM call failed (%s) — using offline reply', exc)

    return {
        'mode': resolved_mode,
        'message': _fallback_answer(resolved_mode, message, citations, fit_result),
        'citations': citations,
        'structured_result': structured,
        'model': 'offline',
        'grounded': bool(citations),
    }


def thread_title(message: str) -> str:
    """First ~60 chars of the opening message, used as the thread title."""
    cleaned = re.sub(r'\s+', ' ', message or '').strip()
    return (cleaned[:60] or 'New chat')
