"""JSON endpoints for the CareerPilot features.

Every view returns JSON and is scoped to ``request.user`` — there is no
cross-user read path. Authentication failures return 401 JSON instead of
redirecting, because these endpoints are called by fetch/XHR.

Routes live in ``careerpilot/urls.py`` and are mounted from
``config/urls.py`` at /cv/, /hunter/, /fit-score/, /chat/, /tracker/, and
/calendar/.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from careerpilot.models import (
    APPLICATION_STATUSES,
    Application,
    ASSISTANT_MODES,
    ChatMessage,
    ChatThread,
    Cv,
    FitScore,
    Goal,
    JobHunt,
    SavedJob,
    Todo,
)
from careerpilot.services import assistant as assistant_service
from careerpilot.services import cv as cv_service
from careerpilot.services import fit_score as fit_service
from careerpilot.services import sources as source_service
from careerpilot.services import stats as stats_service

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
HUNT_CACHE_MINUTES = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_login_required(view):
    """Return 401 JSON for anonymous callers instead of redirecting."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {'error': 'Authentication required.', 'authenticated': False},
                status=401,
            )
        return view(request, *args, **kwargs)
    return wrapper


def _payload(request):
    """Parse a JSON body, falling back to form-encoded POST data."""
    if request.content_type and 'application/json' in request.content_type:
        try:
            return json.loads(request.body or b'{}')
        except ValueError:
            return {}
    return request.POST.dict()


def _method(request, *allowed):
    return request.method in allowed


def _err(message, status=400, **extra):
    return JsonResponse({'error': message, **extra}, status=status)


def _cv_json(cv, include_chunks=False):
    data = {
        'id': cv.pk,
        'name': cv.name,
        'version': cv.version,
        'source': cv.source,
        'status': cv.status,
        'error_message': cv.error_message,
        'is_active': cv.is_active,
        'needs_ocr': cv.needs_ocr,
        'chunk_count': cv.chunks.count(),
        'text_chars': len(cv.raw_text or ''),
        'created_at': cv.created_at.isoformat(),
        'updated_at': cv.updated_at.isoformat(),
    }
    if include_chunks:
        data['chunks'] = [
            {
                'id': chunk.pk,
                'section': chunk.section,
                'section_label': chunk.section_label,
                'content': chunk.content,
                'ordinality': chunk.ordinality,
                'token_count': chunk.token_count,
                'has_embedding': bool(chunk.embedding),
            }
            for chunk in cv.chunks.all()
        ]
    return data


def _neighbour_of(value, days):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() + timedelta(days=days)
    except (TypeError, ValueError):
        return date.today() + timedelta(days=days)


# ---------------------------------------------------------------------------
# HTML pages (dark Bunk UI — JSON API lives under /api/)
# ---------------------------------------------------------------------------

def _career_page(request, template, page, active, icon):
    return render(request, template, {
        'cp_page': page,
        'cp_active': active,
        'cp_icon': icon,
    })


@login_required
def cv_page(request):
    return _career_page(request, 'careerpilot/cv.html', 'cv', 'cv', 'fa-file-lines')


@login_required
def hunter_page(request):
    return _career_page(request, 'careerpilot/hunter.html', 'hunter', 'hunter', 'fa-briefcase')


@login_required
def fit_page(request):
    return _career_page(request, 'careerpilot/fit_score.html', 'fit-score', 'fit', 'fa-gauge-high')


@login_required
def chat_page(request):
    return _career_page(request, 'careerpilot/chat.html', 'chat', 'chat', 'fa-comments')


@login_required
def tracker_page(request):
    return _career_page(request, 'careerpilot/tracker.html', 'tracker', 'tracker', 'fa-table-columns')


@login_required
def calendar_page(request):
    return _career_page(request, 'careerpilot/calendar.html', 'calendar', 'calendar', 'fa-calendar-days')


# ---------------------------------------------------------------------------
# CV
# ---------------------------------------------------------------------------

@api_login_required
def cv_list(request):
    """GET /cv/ — list the user's CVs (newest first)."""
    if not _method(request, 'GET'):
        return _err('Method not allowed.', 405)
    cvs = list(Cv.objects.filter(user=request.user))
    active = next((cv for cv in cvs if cv.is_active), None)
    return JsonResponse({
        'active_cv_id': active.pk if active else None,
        'count': len(cvs),
        'cvs': [_cv_json(cv) for cv in cvs],
    })


@api_login_required
def cv_upload(request):
    """POST /cv/upload/ — upload, parse, chunk, and embed a CV.

    Ingestion runs inline. That keeps the API synchronous and dependency
    free (no Huey task wiring), which matters because chunk counts are in
    the tens; switch to a Huey task if CVs grow large.
    """
    if not _method(request, 'POST'):
        return _err('Method not allowed.', 405)

    upload = request.FILES.get('file')
    if upload is None:
        return _err('No file uploaded. Send a multipart "file" field.')
    if upload.size > MAX_UPLOAD_BYTES:
        return _err('That file is larger than 20 MB.', 413)

    name = (request.POST.get('name') or upload.name or 'CV')[:255]
    try:
        cv_service.extract_text(upload, upload.name)  # validate before saving
    except cv_service.CvParseError as exc:
        return _err(str(exc), 422)

    upload.seek(0)
    with transaction.atomic():
        Cv.objects.filter(user=request.user, is_active=True).update(is_active=False)
        cv = Cv.objects.create(
            user=request.user,
            name=name,
            file=upload,
            source=Cv.SOURCE_UPLOAD,
            is_active=True,
            status=Cv.STATUS_PROCESSING,
        )

    try:
        chunks = cv_service.ingest_cv(cv)
    except cv_service.CvParseError as exc:
        return JsonResponse(
            {'error': str(exc), 'cv': _cv_json(cv)}, status=422,
        )

    cv.refresh_from_db()
    return JsonResponse({'cv': _cv_json(cv), 'chunks_created': chunks}, status=201)


@api_login_required
def cv_detail(request, cv_id):
    """GET or DELETE /cv/<id>/ — inspect one CV or remove it."""
    cv = get_object_or_404(Cv, pk=cv_id, user=request.user)

    if request.method == 'GET':
        return JsonResponse({'cv': _cv_json(cv, include_chunks=True)})

    if request.method == 'DELETE':
        was_active = cv.is_active
        cv.delete()
        promoted = None
        if was_active:
            promoted = (
                Cv.objects.filter(user=request.user).order_by('-created_at').first()
            )
            if promoted:
                promoted.is_active = True
                promoted.save(update_fields=['is_active', 'updated_at'])
        return JsonResponse({
            'deleted': cv_id,
            'promoted_cv_id': promoted.pk if promoted else None,
        })

    return _err('Method not allowed.', 405)


@api_login_required
def cv_chunks(request, cv_id):
    """GET /cv/<id>/chunks/ — the chunk inspector payload."""
    if not _method(request, 'GET'):
        return _err('Method not allowed.', 405)
    cv = get_object_or_404(Cv, pk=cv_id, user=request.user)
    section = request.GET.get('section')
    chunks = cv.chunks.all()
    if section:
        chunks = chunks.filter(section=section)
    payload = [
        {
            'id': chunk.pk,
            'section': chunk.section,
            'section_label': chunk.section_label,
            'content': chunk.content,
            'ordinality': chunk.ordinality,
            'token_count': chunk.token_count,
            'embedding_dimensions': len(chunk.embedding or []),
            'structured_payload': chunk.structured_payload,
        }
        for chunk in chunks
    ]
    return JsonResponse({'cv_id': cv.pk, 'count': len(payload), 'chunks': payload})


@api_login_required
def cv_activate(request, cv_id):
    """POST /cv/<id>/activate/ — make this CV the active one."""
    if not _method(request, 'POST'):
        return _err('Method not allowed.', 405)
    cv = get_object_or_404(Cv, pk=cv_id, user=request.user)
    with transaction.atomic():
        Cv.objects.filter(user=request.user, is_active=True).exclude(pk=cv.pk).update(
            is_active=False,
        )
        cv.is_active = True
        cv.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'cv': _cv_json(cv)})


# ---------------------------------------------------------------------------
# Job Hunter
# ---------------------------------------------------------------------------

def _score_jobs(jobs, cv_text):
    scored = []
    for job in jobs:
        entry = dict(job)
        if cv_text:
            entry.update(fit_service.job_fit(job, cv_text))
        else:
            entry.setdefault('fit_score', None)
        scored.append(entry)
    scored.sort(key=lambda job: (job.get('fit_score') or 0), reverse=True)
    return scored


@api_login_required
def hunt_search(request):
    """GET /hunter/?q=role&refresh=1 — search, dedupe, score, and cache."""
    if not _method(request, 'GET', 'POST'):
        return _err('Method not allowed.', 405)

    params = request.GET if request.method == 'GET' else request.POST
    query = (params.get('q') or '').strip()
    if not query:
        return _err('Pass a search query, e.g. ?q=backend engineer in dhaka')

    parsed = source_service.parse_query(query)
    query_hash = hashlib.sha256(
        '|'.join([parsed['role'].lower(), (parsed['location'] or '').lower()]).encode()
    ).hexdigest()

    force = (params.get('refresh') or '') in {'1', 'true', 'yes'}
    cached = JobHunt.objects.filter(user=request.user, query_hash=query_hash).first()
    if cached and cached.is_fresh and not force:
        return JsonResponse({
            'query': query,
            'parsed': parsed,
            'cached': True,
            'refreshed_at': cached.refreshed_at.isoformat(),
            'jobs': cached.result.get('jobs', []),
        })

    raw_jobs = source_service.fan_out(query)
    cv = cv_service.active_cv(request.user)
    jobs = _score_jobs(raw_jobs, (cv.raw_text if cv else '') or '')
    ranked = source_service.rank_by_location(jobs, parsed['location'])

    payload = {
        'query': query,
        'parsed': parsed,
        'jobs': jobs,
        'counts': {
            'total': len(jobs),
            'local': len(ranked['local']),
            'remote': len(ranked['remote']),
            'other': len(ranked['other']),
        },
        'scored_against_cv_id': cv.pk if cv else None,
        'sources_unavailable': [
            name for name in ('adzuna', 'tavily')
            if name not in {job.get('source') for job in jobs}
        ],
    }

    now = timezone.now()
    if cached:
        cached.result = payload
        cached.refreshed_at = now
        cached.expires_at = now + timedelta(minutes=HUNT_CACHE_MINUTES)
        cached.query = query
        cached.save()
    else:
        JobHunt.objects.create(
            user=request.user,
            query=query,
            query_hash=query_hash,
            result=payload,
            refreshed_at=now,
            expires_at=now + timedelta(minutes=HUNT_CACHE_MINUTES),
        )

    return JsonResponse({**payload, 'cached': False})


@api_login_required
def hunt_saved_list(request):
    """GET /hunter/saved/ — bookmarked jobs."""
    if not _method(request, 'GET'):
        return _err('Method not allowed.', 405)
    jobs = SavedJob.objects.filter(user=request.user)
    return JsonResponse({
        'count': jobs.count(),
        'jobs': [
            {
                'id': job.pk,
                'url': job.url,
                'title': job.title,
                'company': job.company,
                'location': job.location,
                'salary': job.salary,
                'job_type': job.job_type,
                'fit_score': job.fit_score,
                'fit_reason': job.fit_reason,
                'match_highlights': job.match_highlights,
                'concerns': job.concerns,
                'source': job.source,
                'is_remote_fallback': job.is_remote_fallback,
                'saved_at': job.saved_at.isoformat(),
            }
            for job in jobs
        ],
    })


@api_login_required
def hunt_save(request):
    """POST /hunter/save/ — bookmark a job (idempotent per url)."""
    if not _method(request, 'POST'):
        return _err('Method not allowed.', 405)

    data = _payload(request)
    url = (data.get('url') or '').strip()
    title = (data.get('title') or '').strip()
    company = (data.get('company') or '').strip()
    if not url or not title or not company:
        return _err('url, title, and company are required.')

    defaults = {
        'title': title[:255],
        'company': company[:255],
        'location': (data.get('location') or '')[:255],
        'salary': (data.get('salary') or '')[:120],
        'deadline': (data.get('deadline') or '')[:40],
        'job_type': data.get('job_type') if data.get('job_type') in dict(
            (choice[0], choice[1]) for choice in SavedJob._meta.get_field('job_type').choices
        ) else 'other',
        'snippet': data.get('snippet') or '',
        'description': data.get('description') or '',
        'fit_score': data.get('fit_score'),
        'fit_reason': data.get('fit_reason') or '',
        'match_highlights': data.get('match_highlights') or [],
        'concerns': data.get('concerns') or [],
        'breakdown': data.get('breakdown') or {},
        'source': (data.get('source') or '')[:40],
        'is_remote_fallback': bool(data.get('is_remote_fallback')),
    }
    job, created = SavedJob.objects.update_or_create(
        user=request.user, url=url, defaults=defaults,
    )
    return JsonResponse(
        {'id': job.pk, 'created': created, 'saved': True}, status=201 if created else 200,
    )


@api_login_required
def hunt_save_delete(request, job_id):
    """DELETE /hunter/saved/<id>/ — remove a bookmark."""
    if not _method(request, 'DELETE', 'POST'):
        return _err('Method not allowed.', 405)
    job = get_object_or_404(SavedJob, pk=job_id, user=request.user)
    job.delete()
    return JsonResponse({'deleted': job_id, 'saved': False})


# ---------------------------------------------------------------------------
# Fit Score
# ---------------------------------------------------------------------------

@api_login_required
def fit_score_view(request):
    """POST /fit-score/ — compute (or reuse) a fit result.

    Body: {"jd": "...", "target_role": "...", "benchmark_key": "...",
           "force": false}
    Repeat runs on the same JD return the stored row with ``cached: true``.
    """
    if not _method(request, 'POST', 'GET'):
        return _err('Method not allowed.', 405)

    data = _payload(request) if request.method == 'POST' else request.GET
    jd_text = (data.get('jd') or data.get('job_description') or '').strip()
    target_role = (data.get('target_role') or data.get('role') or '').strip()
    benchmark_key = (data.get('benchmark_key') or '_freeform').strip() or '_freeform'
    force = str(data.get('force') or '').lower() in {'1', 'true', 'yes'}

    if not jd_text and not target_role:
        return _err('Provide a job description (jd) or a target_role.')

    jd_hash = hashlib.sha256(
        json.dumps(
            {'jd': jd_text, 'role': target_role, 'benchmark': benchmark_key},
            sort_keys=True,
        ).encode()
    ).hexdigest()

    if not force:
        existing = FitScore.objects.filter(
            user=request.user, jd_hash=jd_hash,
        ).order_by('-computed_at').first()
        if existing:
            return JsonResponse({
                'cached': True,
                'id': existing.pk,
                'computed_at': existing.computed_at.isoformat(),
                'result': existing.result,
            })

    cv = cv_service.active_cv(request.user)
    if cv is None:
        return _err(
            'Upload a CV first — fit scoring is grounded in your active CV.',
            409,
        )

    result = fit_service.score_fit(
        cv.raw_text, jd_text=jd_text, target_role=target_role,
        benchmark_key=benchmark_key,
    )
    record = FitScore.objects.create(
        user=request.user,
        benchmark_key=benchmark_key,
        jd_hash=jd_hash,
        jd_excerpt=jd_text[:fit_service.MAX_JD_EXCERPT],
        result=result,
    )
    return JsonResponse({
        'cached': False,
        'id': record.pk,
        'computed_at': record.computed_at.isoformat(),
        'cv_id': cv.pk,
        'result': result,
    }, status=201)


@api_login_required
def fit_score_history(request):
    """GET /fit-score/history/ — recent computations for the dashboard."""
    if not _method(request, 'GET'):
        return _err('Method not allowed.', 405)
    records = FitScore.objects.filter(user=request.user)[:20]
    return JsonResponse({
        'count': len(records),
        'results': [
            {
                'id': record.pk,
                'benchmark_key': record.benchmark_key,
                'score': record.result.get('score'),
                'verdict': record.result.get('verdict'),
                'computed_at': record.computed_at.isoformat(),
            }
            for record in records
        ],
    })


# ---------------------------------------------------------------------------
# AI Assistant
# ---------------------------------------------------------------------------

def _message_json(message):
    return {
        'id': message.pk,
        'role': message.role,
        'content': message.content,
        'mode': message.mode,
        'citations': message.citations,
        'structured_result': message.structured_result,
        'created_at': message.created_at.isoformat(),
    }


@api_login_required
def chat_threads(request):
    """GET /chat/threads/ (list) or POST /chat/threads/ (create + first turn)."""
    if request.method == 'GET':
        threads = ChatThread.objects.filter(user=request.user)[:50]
        return JsonResponse({
            'count': len(threads),
            'threads': [
                {
                    'id': thread.pk,
                    'title': thread.title,
                    'message_count': thread.messages.count(),
                    'updated_at': thread.updated_at.isoformat(),
                }
                for thread in threads
            ],
        })

    if request.method != 'POST':
        return _err('Method not allowed.', 405)

    data = _payload(request)
    message_text = (data.get('message') or '').strip()
    thread = ChatThread.objects.create(
        user=request.user,
        title=(data.get('title') or assistant_service.thread_title(message_text))[:255],
    )

    first_turn = None
    if message_text:
        first_turn = _append_turn(thread, message_text, data.get('mode'))

    return JsonResponse({
        'thread': {'id': thread.pk, 'title': thread.title},
        'messages': [first_turn] if first_turn else [],
    }, status=201)


@api_login_required
def chat_thread_detail(request, thread_id):
    """GET or DELETE /chat/threads/<id>/."""
    thread = get_object_or_404(ChatThread, pk=thread_id, user=request.user)

    if request.method == 'GET':
        return JsonResponse({
            'thread': {'id': thread.pk, 'title': thread.title},
            'messages': [_message_json(m) for m in thread.messages.all()],
        })

    if request.method == 'DELETE':
        thread.delete()
        return JsonResponse({'deleted': thread_id})

    if request.method in {'PATCH', 'POST'}:
        data = _payload(request)
        title = (data.get('title') or '').strip()
        if not title:
            return _err('title is required.')
        thread.title = title[:255]
        thread.save(update_fields=['title', 'updated_at'])
        return JsonResponse({'thread': {'id': thread.pk, 'title': thread.title}})

    return _err('Method not allowed.', 405)


def _append_turn(thread, message_text, mode=None):
    """Persist the user turn, compute the reply, persist it, return both."""
    user_message = ChatMessage.objects.create(
        thread=thread,
        user=thread.user,
        role=ChatMessage.ROLE_USER,
        content=message_text,
    )
    result = assistant_service.answer(
        thread.user, message_text, mode=mode, thread=thread,
    )
    model_message = ChatMessage.objects.create(
        thread=thread,
        user=thread.user,
        role=ChatMessage.ROLE_MODEL,
        content=result['message'],
        citations=result['citations'],
        mode=result['mode'],
        structured_result=result['structured_result'],
    )
    thread.save(update_fields=['updated_at'])
    return {
        'user': _message_json(user_message),
        'model': _message_json(model_message),
        'engine': result.get('model'),
        'grounded': result.get('grounded', False),
    }


@api_login_required
def chat_messages(request, thread_id):
    """GET or POST /chat/threads/<id>/messages/."""
    thread = get_object_or_404(ChatThread, pk=thread_id, user=request.user)

    if request.method == 'GET':
        return JsonResponse({
            'thread_id': thread.pk,
            'messages': [_message_json(m) for m in thread.messages.all()],
        })

    if request.method != 'POST':
        return _err('Method not allowed.', 405)

    data = _payload(request)
    message_text = (data.get('message') or '').strip()
    if not message_text:
        return _err('message is required.')
    return JsonResponse(_append_turn(thread, message_text, data.get('mode')), status=201)


def chat_modes(request):
    """GET /chat/modes/ — the modes the assistant accepts."""
    return JsonResponse({
        'modes': [{'value': value, 'label': label} for value, label in ASSISTANT_MODES],
    })


# ---------------------------------------------------------------------------
# Tracker (Kanban)
# ---------------------------------------------------------------------------

def _application_json(application):
    return {
        'id': application.pk,
        'company': application.company,
        'role': application.role,
        'url': application.url,
        'location': application.location,
        'salary': application.salary,
        'deadline': application.deadline.isoformat() if application.deadline else None,
        'status': application.status,
        'notes': application.notes,
        'applied_at': application.applied_at.isoformat(),
        'status_updated_at': application.status_updated_at.isoformat(),
        'history': application.history,
    }


@api_login_required
def applications(request):
    """GET /tracker/applications/ (list) or POST (create)."""
    if request.method == 'GET':
        queryset = Application.objects.filter(user=request.user)
        status = request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        rows = list(queryset)
        return JsonResponse({
            'count': len(rows),
            'by_status': {
                value: len([row for row in rows if row.status == value])
                for value, _ in APPLICATION_STATUSES
            },
            'applications': [_application_json(row) for row in rows],
        })

    if request.method != 'POST':
        return _err('Method not allowed.', 405)

    data = _payload(request)
    company = (data.get('company') or '').strip()
    role = (data.get('role') or '').strip()
    if not company or not role:
        return _err('company and role are required.')

    status = data.get('status') or 'applied'
    if status not in {value for value, _ in APPLICATION_STATUSES}:
        return _err('status must be one of applied, interviewing, offer, rejected.')

    deadline = None
    if data.get('deadline'):
        try:
            deadline = datetime.strptime(str(data['deadline']), '%Y-%m-%d').date()
        except ValueError:
            return _err('deadline must be YYYY-MM-DD.')

    saved_job = None
    if data.get('saved_job_id'):
        saved_job = SavedJob.objects.filter(
            pk=data['saved_job_id'], user=request.user,
        ).first()

    try:
        application = Application.objects.create(
            user=request.user,
            saved_job=saved_job,
            company=company[:255],
            role=role[:255],
            url=(data.get('url') or '')[:1000],
            location=(data.get('location') or '')[:255],
            salary=(data.get('salary') or '')[:120],
            deadline=deadline,
            status=status,
            notes=data.get('notes') or '',
            history=[{
                'status': status,
                'at': timezone.now().isoformat(),
            }],
        )
    except IntegrityError:
        return _err('You already track an application for that URL.', 409)

    return JsonResponse({'application': _application_json(application)}, status=201)


@api_login_required
def application_detail(request, application_id):
    """PATCH/POST (status update) or DELETE /tracker/applications/<id>/."""
    application = get_object_or_404(
        Application, pk=application_id, user=request.user,
    )

    if request.method == 'DELETE':
        application.delete()
        return JsonResponse({'deleted': application_id})

    if request.method not in {'PATCH', 'POST'}:
        return _err('Method not allowed.', 405)

    data = _payload(request)
    status = data.get('status')
    if status and status not in {value for value, _ in APPLICATION_STATUSES}:
        return _err('status must be one of applied, interviewing, offer, rejected.')
    if status and status != application.status:
        history = list(application.history or [])
        history.append({'status': status, 'at': timezone.now().isoformat()})
        application.history = history
        application.status = status

    if 'notes' in data:
        application.notes = data.get('notes') or ''

    application.save()
    return JsonResponse({'application': _application_json(application)})


@api_login_required
def tracker_stats(request):
    """GET /tracker/stats/ — dashboard stat cards."""
    if not _method(request, 'GET'):
        return _err('Method not allowed.', 405)
    return JsonResponse(stats_service.summary(request.user))


# ---------------------------------------------------------------------------
# Calendar & to-dos
# ---------------------------------------------------------------------------

def _todo_json(todo):
    return {
        'id': todo.pk,
        'title': todo.title,
        'due_date': todo.due_date.isoformat(),
        'done': todo.done,
        'done_at': todo.done_at.isoformat() if todo.done_at else None,
        'goal_id': todo.goal_id,
        'application_id': todo.application_id,
    }


def _parse_date(value, fallback=None):
    if not value:
        return fallback
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


@api_login_required
def calendar(request):
    """GET /calendar/?date=YYYY-MM-DD — one day's agenda plus counts."""
    if not _method(request, 'GET'):
        return _err('Method not allowed.', 405)

    day = _parse_date(request.GET.get('date'), date.today())
    if day is None:
        return _err('date must be YYYY-MM-DD.')

    todos = Todo.objects.filter(user=request.user, due_date=day)
    deadlines = Application.objects.filter(
        user=request.user, deadline=day,
    )
    goals = Goal.objects.filter(user=request.user, due_date=day)

    return JsonResponse({
        'date': day.isoformat(),
        'prev_date': _neighbour_of(day.isoformat(), -1).isoformat(),
        'next_date': _neighbour_of(day.isoformat(), 1).isoformat(),
        'todos': [_todo_json(todo) for todo in todos],
        'goal_due': [
            {'id': goal.pk, 'title': goal.title, 'completed': goal.completed}
            for goal in goals
        ],
        'application_deadlines': [_application_json(row) for row in deadlines],
        'counts': {
            'todos': todos.count(),
            'todos_done': todos.filter(done=True).count(),
            'deadlines': deadlines.count(),
        },
    })


@api_login_required
def todos(request):
    """GET /calendar/todos/ (list) or POST (create)."""
    if request.method == 'GET':
        queryset = Todo.objects.filter(user=request.user)
        day = _parse_date(request.GET.get('date'))
        if day:
            queryset = queryset.filter(due_date=day)
        if request.GET.get('open'):
            queryset = queryset.filter(done=False)
        rows = list(queryset[:200])
        return JsonResponse({'count': len(rows), 'todos': [_todo_json(t) for t in rows]})

    if request.method != 'POST':
        return _err('Method not allowed.', 405)

    data = _payload(request)
    title = (data.get('title') or '').strip()
    if not title:
        return _err('title is required.')

    due_date = _parse_date(data.get('due_date'), date.today())
    if due_date is None:
        return _err('due_date must be YYYY-MM-DD.')

    goal = None
    if data.get('goal_id'):
        goal = Goal.objects.filter(pk=data['goal_id'], user=request.user).first()

    application = None
    if data.get('application_id'):
        application = Application.objects.filter(
            pk=data['application_id'], user=request.user,
        ).first()

    try:
        todo = Todo.objects.create(
            user=request.user,
            title=title[:255],
            due_date=due_date,
            goal=goal,
            application=application,
            dedupe_key=(data.get('dedupe_key') or '')[:120],
        )
    except IntegrityError:
        return _err('That to-do already exists for this user.', 409)

    return JsonResponse({'todo': _todo_json(todo)}, status=201)


@api_login_required
def todo_detail(request, todo_id):
    """DELETE /calendar/todos/<id>/ or POST/PATCH to toggle done."""
    todo = get_object_or_404(Todo, pk=todo_id, user=request.user)

    if request.method == 'DELETE':
        todo.delete()
        return JsonResponse({'deleted': todo_id})

    if request.method not in {'PATCH', 'POST'}:
        return _err('Method not allowed.', 405)

    data = _payload(request)
    title = data.get('title')
    if title:
        todo.title = title[:255]

    if 'done' in data:
        done = bool(data.get('done'))
        todo.done = done
        todo.done_at = timezone.now() if done else None

    todo.save()
    return JsonResponse({'todo': _todo_json(todo)})


@api_login_required
def todo_done(request, todo_id):
    """POST /calendar/todos/<id>/done/ — mark complete."""
    if not _method(request, 'POST'):
        return _err('Method not allowed.', 405)
    todo = get_object_or_404(Todo, pk=todo_id, user=request.user)
    todo.done = True
    todo.done_at = timezone.now()
    todo.save(update_fields=['done', 'done_at'])
    return JsonResponse({'todo': _todo_json(todo)})


@api_login_required
def goals(request):
    """GET /calendar/goals/ (list) or POST (create)."""
    if request.method == 'GET':
        queryset = Goal.objects.filter(user=request.user)[:100]
        return JsonResponse({
            'count': len(queryset),
            'goals': [
                {
                    'id': goal.pk,
                    'title': goal.title,
                    'type': goal.type,
                    'target_count': goal.target_count,
                    'period': goal.period,
                    'due_date': goal.due_date.isoformat(),
                    'completed': goal.completed,
                }
                for goal in queryset
            ],
        })

    if request.method != 'POST':
        return _err('Method not allowed.', 405)

    data = _payload(request)
    title = (data.get('title') or '').strip()
    due_date = _parse_date(data.get('due_date'))
    if not title or due_date is None:
        return _err('title and due_date (YYYY-MM-DD) are required.')

    goal_type = data.get('type') or Goal.TYPE_ONE_SHOT
    period = data.get('period') or Goal.PERIOD_DATE
    if goal_type not in {Goal.TYPE_COUNT, Goal.TYPE_ONE_SHOT}:
        return _err('type must be count or one_shot.')
    if period not in {Goal.PERIOD_WEEK, Goal.PERIOD_DATE}:
        return _err('period must be week or date.')

    goal = Goal.objects.create(
        user=request.user,
        title=title[:255],
        type=goal_type,
        target_count=data.get('target_count') or None,
        period=period,
        due_date=due_date,
    )
    return JsonResponse({
        'goal': {
            'id': goal.pk,
            'title': goal.title,
            'type': goal.type,
            'target_count': goal.target_count,
            'period': goal.period,
            'due_date': goal.due_date.isoformat(),
            'completed': goal.completed,
        },
    }, status=201)
