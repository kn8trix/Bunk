"""Dashboard metrics: streak, weekly progress, and the summary card payload.

Port of CareerPilot's ``lib/productivity/streak.ts`` + ``v_weekly_stats``.
The SQL view in the original project is replaced by ORM aggregates because
the tracker tables here are ordinary Django models.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone


def _as_date(value):
    """Date for a stored datetime, aware or naive (this project runs USE_TZ=False)."""
    if value is None:
        return None
    return timezone.localtime(value).date() if settings.USE_TZ else value.date()


def _activity_dates(user, since):
    """Distinct dates with tracker activity since ``since``."""
    from careerpilot.models import Application, Todo

    dates = set()
    for value in (
        Application.objects.filter(user=user, status_updated_at__gte=since)
        .values_list('status_updated_at', flat=True)
    ):
        if value:
            dates.add(_as_date(value))
    for value in (
        Todo.objects.filter(user=user, done=True, done_at__gte=since)
        .values_list('done_at', flat=True)
    ):
        if value:
            dates.add(_as_date(value))
    return dates


def streak_days(user, lookback_days: int = 365) -> int:
    """Consecutive days of tracker activity ending today (or yesterday).

    A streak survives if the user was active yesterday but not yet today —
    otherwise every morning would reset it to zero.
    """
    if not user or not user.is_authenticated:
        return 0

    today = date.today()
    since = timezone.now() - timedelta(days=lookback_days)
    active = _activity_dates(user, since)
    if not active:
        return 0

    cursor = today if today in active else today - timedelta(days=1)
    if cursor not in active:
        return 0

    streak = 0
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def week_bounds(today=None):
    """Monday..Sunday bounds for the current week."""
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def weekly_progress(user) -> dict:
    from careerpilot.models import Application, Goal, Todo

    start, end = week_bounds()
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end + timedelta(days=1), time.min)
    if settings.USE_TZ:
        start_dt = timezone.make_aware(start_dt)
        end_dt = timezone.make_aware(end_dt)

    apps_sent = Application.objects.filter(
        user=user, status_updated_at__gte=start_dt, status_updated_at__lt=end_dt,
    ).count()
    todos_done = Todo.objects.filter(
        user=user, done=True, done_at__gte=start_dt, done_at__lt=end_dt,
    ).count()
    goals = Goal.objects.filter(user=user, due_date__gte=start, due_date__lte=end)
    goals_total = goals.count()
    goals_done = goals.filter(completed=True).count()

    return {
        'week_start': start.isoformat(),
        'week_end': end.isoformat(),
        'apps_sent': apps_sent,
        'todos_done': todos_done,
        'goals_total': goals_total,
        'goals_done': goals_done,
        'roadmap_pct': round((goals_done / goals_total) * 100) if goals_total else 0,
        'weekly_progress': (
            round(((apps_sent + todos_done) / max(1, goals_total + todos_done)) * 100)
            if (todos_done or apps_sent) else 0
        ),
    }


def summary(user) -> dict:
    """Everything the dashboard stat cards need, in one query set."""
    from careerpilot.models import Application, SavedJob, Todo

    if not user or not user.is_authenticated:
        return {
            'applications_count': 0,
            'saved_jobs_count': 0,
            'streak_days': 0,
            'open_todos': 0,
            'by_status': {},
            'weekly': {},
        }

    by_status = {
        row['status']: row['count']
        for row in Application.objects.filter(user=user).values('status').annotate(count=Count('id'))
    }
    return {
        'applications_count': Application.objects.filter(user=user).count(),
        'saved_jobs_count': SavedJob.objects.filter(user=user).count(),
        'streak_days': streak_days(user),
        'open_todos': Todo.objects.filter(user=user, done=False).count(),
        'by_status': by_status,
        'weekly': weekly_progress(user),
    }
