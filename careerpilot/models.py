"""CareerPilot domain models — the Django port of the CareerPilot backend.

The original project (Next.js + Supabase) kept six feature pillars in
Postgres tables: CV ingestion (`cvs`, `cv_chunks`), job hunting
(`hunter_hunts`, `hunter_saved`), fit scoring (`fit_scores`), the AI
assistant (`chat_threads`, `chat_messages`), and the productivity tracker
(`applications`, `goals`, `todos`).

Here they are ordinary Django models owned by ``AUTH_USER_MODEL`` instead
of a Clerk ``user_id`` string. Embeddings are stored as JSON lists rather
than a pgvector column because this project runs on SQLite in development
and plain Postgres in production (no pgvector extension assumed).
"""

from datetime import date

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

CV_SECTIONS = [
    ('header', 'Header'),
    ('summary', 'Summary'),
    ('objective', 'Objective'),
    ('experience', 'Experience'),
    ('work_experience', 'Work experience'),
    ('education', 'Education'),
    ('skills', 'Skills'),
    ('technical_skills', 'Technical skills'),
    ('projects', 'Projects'),
    ('certifications', 'Certifications'),
    ('publications', 'Publications'),
    ('awards', 'Awards'),
    ('image_ocr', 'Image OCR'),
    ('other', 'Other'),
]

JOB_TYPES = [
    ('internship', 'Internship'),
    ('full-time', 'Full-time'),
    ('contract', 'Contract'),
    ('research', 'Research'),
    ('part-time', 'Part-time'),
    ('other', 'Other'),
]

APPLICATION_STATUSES = [
    ('applied', 'Applied'),
    ('interviewing', 'Interviewing'),
    ('offer', 'Offer'),
    ('rejected', 'Rejected'),
]

ASSISTANT_MODES = [
    ('general', 'General'),
    ('readiness', 'Readiness'),
    ('gap_analysis', 'Gap analysis'),
    ('roadmap', 'Roadmap'),
    ('cover_letter', 'Cover letter'),
    ('guide', 'Guide'),
    ('consultant', 'Consultant'),
    ('judge', 'Judge'),
]


class Cv(models.Model):
    """One uploaded (or builder-saved) CV version.

    A user may keep many versions; at most one row has ``is_active=True``
    (enforced by a partial unique constraint). The active CV is what the
    fit scorer, hunter, and assistant read from.
    """

    SOURCE_UPLOAD = 'upload'
    SOURCE_BUILDER = 'builder'
    SOURCE_CHOICES = [
        (SOURCE_UPLOAD, 'Upload'),
        (SOURCE_BUILDER, 'Builder'),
    ]

    STATUS_PROCESSING = 'processing'
    STATUS_READY = 'ready'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_READY, 'Ready'),
        (STATUS_FAILED, 'Failed'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_cvs',
    )
    # Cosmetic display label — the default is the uploaded filename.
    name = models.CharField(max_length=255, blank=True)
    version = models.PositiveIntegerField(default=1)
    file = models.FileField(upload_to='careerpilot/cvs/%Y/%m/', blank=True, null=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_UPLOAD)
    raw_text = models.TextField(blank=True)
    # Byte ranges per section, e.g. {"experience": [1200, 4800]}.
    section_index = models.JSONField(default=dict, blank=True)
    needs_ocr = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_READY)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active', '-created_at'],
                         name='cp_cv_user_active_idx'),
            models.Index(fields=['user', '-created_at'], name='cp_cv_user_recent_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(is_active=True),
                name='cp_one_active_cv_per_user',
            ),
        ]

    def __str__(self):
        return self.name or f'CV #{self.pk}'


class CvChunk(models.Model):
    """A section-scoped, embedded slice of ``Cv.raw_text``."""

    cv = models.ForeignKey(Cv, on_delete=models.CASCADE, related_name='chunks')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_cv_chunks',
    )
    section = models.CharField(max_length=32, choices=CV_SECTIONS, default='other')
    section_label = models.CharField(max_length=255)
    content = models.TextField()
    embedding = models.JSONField(default=list, blank=True)
    ordinality = models.PositiveIntegerField(default=1)
    token_count = models.PositiveIntegerField(default=0)
    ocr_source = models.CharField(max_length=20, blank=True)
    source_image_url = models.CharField(max_length=500, blank=True)
    structured_payload = models.JSONField(default=dict, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['cv', 'section', 'ordinality']
        indexes = [
            models.Index(fields=['cv', 'section', 'ordinality'], name='cp_chunk_cv_idx'),
            models.Index(fields=['user'], name='cp_chunk_user_idx'),
        ]

    def __str__(self):
        return f'{self.section_label} ({self.section})'


class JobHunt(models.Model):
    """Cached result of one job search (30-minute freshness, like the original)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_hunts',
    )
    query = models.TextField()
    query_hash = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    refreshed_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-refreshed_at']
        indexes = [
            models.Index(fields=['user', 'query_hash'], name='cp_hunt_user_query_idx'),
            models.Index(fields=['user', '-refreshed_at'], name='cp_hunt_user_recent_idx'),
        ]

    def __str__(self):
        return self.query

    @property
    def is_fresh(self):
        return self.expires_at > timezone.now()


class SavedJob(models.Model):
    """A bookmarked job. Idempotent per (user, url)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_saved_jobs',
    )
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=255)
    company = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True)
    salary = models.CharField(max_length=120, blank=True)
    deadline = models.CharField(max_length=40, blank=True)
    job_type = models.CharField(max_length=20, choices=JOB_TYPES, default='other')
    snippet = models.TextField(blank=True)
    description = models.TextField(blank=True)
    fit_score = models.PositiveSmallIntegerField(null=True, blank=True)
    fit_reason = models.TextField(blank=True)
    match_highlights = models.JSONField(default=list, blank=True)
    concerns = models.JSONField(default=list, blank=True)
    breakdown = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=40, blank=True)
    is_remote_fallback = models.BooleanField(default=False)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-saved_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'url'], name='cp_saved_job_user_url_uniq'),
        ]

    def __str__(self):
        return f'{self.title} @ {self.company}'


class FitScore(models.Model):
    """One persisted fit-score computation, keyed by (user, jd_hash)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_fit_scores',
    )
    benchmark_key = models.CharField(max_length=80, default='_freeform')
    jd_hash = models.CharField(max_length=64)
    jd_excerpt = models.TextField(blank=True)
    result = models.JSONField(default=dict)
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-computed_at']
        indexes = [
            models.Index(fields=['user', '-computed_at'], name='cp_fit_user_recent_idx'),
            models.Index(fields=['user', 'jd_hash', '-computed_at'], name='cp_fit_user_jd_idx'),
        ]

    def __str__(self):
        return f'Fit {self.result.get("score", "?")}/100 ({self.benchmark_key})'


class ChatThread(models.Model):
    """A conversation with the career assistant."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_threads',
    )
    title = models.CharField(max_length=255, default='New chat')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at'], name='cp_thread_user_idx'),
        ]

    def __str__(self):
        return self.title


class ChatMessage(models.Model):
    ROLE_USER = 'user'
    ROLE_MODEL = 'model'
    ROLE_CHOICES = [
        (ROLE_USER, 'User'),
        (ROLE_MODEL, 'Model'),
    ]

    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE, related_name='messages')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_messages',
    )
    role = models.CharField(max_length=8, choices=ROLE_CHOICES)
    content = models.TextField()
    citations = models.JSONField(default=list, blank=True)
    mode = models.CharField(max_length=20, choices=ASSISTANT_MODES, blank=True)
    structured_result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['thread', 'created_at'], name='cp_msg_thread_idx'),
            models.Index(fields=['user'], name='cp_msg_user_idx'),
        ]

    def __str__(self):
        return f'{self.role}: {self.content[:40]}'


class Goal(models.Model):
    """A weekly or deadline goal (count target or one-shot)."""

    TYPE_COUNT = 'count'
    TYPE_ONE_SHOT = 'one_shot'
    TYPE_CHOICES = [
        (TYPE_COUNT, 'Count'),
        (TYPE_ONE_SHOT, 'One shot'),
    ]

    PERIOD_WEEK = 'week'
    PERIOD_DATE = 'date'
    PERIOD_CHOICES = [
        (PERIOD_WEEK, 'Week'),
        (PERIOD_DATE, 'Date'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_goals',
    )
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_ONE_SHOT)
    target_count = models.PositiveIntegerField(null=True, blank=True)
    period = models.CharField(max_length=6, choices=PERIOD_CHOICES, default=PERIOD_DATE)
    due_date = models.DateField()
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_date', 'id']
        indexes = [
            models.Index(fields=['user', 'due_date'], name='cp_goal_user_due_idx'),
            models.Index(fields=['user', 'completed'], name='cp_goal_user_open_idx'),
        ]

    def __str__(self):
        return self.title


class Application(models.Model):
    """One row on the Kanban application pipeline."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_applications',
    )
    saved_job = models.ForeignKey(
        SavedJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='applications',
    )
    company = models.CharField(max_length=255)
    role = models.CharField(max_length=255)
    url = models.URLField(max_length=1000, blank=True)
    location = models.CharField(max_length=255, blank=True)
    salary = models.CharField(max_length=120, blank=True)
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=APPLICATION_STATUSES, default='applied')
    notes = models.TextField(blank=True)
    applied_at = models.DateField(default=date.today)
    status_updated_at = models.DateTimeField(auto_now=True)
    # Append-only [{status, at}] trail, like the original jsonb column.
    history = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-status_updated_at']
        indexes = [
            models.Index(fields=['user', '-status_updated_at'], name='cp_app_user_recent_idx'),
            models.Index(fields=['user', 'status'], name='cp_app_user_status_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'url'],
                condition=~Q(url=''),
                name='cp_app_user_url_uniq',
            ),
        ]

    def __str__(self):
        return f'{self.role} @ {self.company} [{self.status}]'


class Todo(models.Model):
    """A dated to-do, optionally linked to a goal or an application."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='careerpilot_todos',
    )
    goal = models.ForeignKey(
        Goal, on_delete=models.SET_NULL, null=True, blank=True, related_name='todos',
    )
    application = models.ForeignKey(
        Application, on_delete=models.SET_NULL, null=True, blank=True, related_name='todos',
    )
    title = models.CharField(max_length=255)
    due_date = models.DateField(default=date.today)
    done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_date', 'id']
        indexes = [
            models.Index(fields=['user', 'due_date'], name='cp_todo_user_due_idx'),
            models.Index(fields=['user', 'done'], name='cp_todo_user_done_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'dedupe_key'],
                condition=~Q(dedupe_key=''),
                name='cp_todo_user_dedupe_uniq',
            ),
        ]

    def __str__(self):
        return self.title
