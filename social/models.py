from django.db import models
from django.contrib.auth.models import User


def upload_thread_image_path(instance, filename):
    """Upload path for thread images: media/forum/threads/<thread_id>/<filename>"""
    return f'forum/threads/{instance.thread.pk}/{filename}'


def upload_post_image_path(instance, filename):
    """Upload path for post images: media/forum/posts/<post_id>/<filename>"""
    return f'forum/posts/{instance.post.pk}/{filename}'


class ForumCategory(models.Model):
    """A category for organizing forum threads."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveIntegerField(default=0, db_index=True, help_text='Display order (lower = left)')

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Forum Category'
        verbose_name_plural = 'Forum Categories'

    def __str__(self):
        return self.name


class ForumThread(models.Model):
    """A discussion thread (topic) within a forum category."""
    subject = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='forum_threads',
    )
    category = models.ForeignKey(
        ForumCategory,
        on_delete=models.CASCADE,
        related_name='threads',
    )
    is_pinned = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Pinned threads appear at the top of the category',
    )
    is_locked = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Locked threads cannot have new replies',
    )
    # Track first post for quick reference
    first_post_created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_pinned', '-updated_at', '-created_at']
        indexes = [
            models.Index(fields=['category', '-is_pinned', '-updated_at']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f'{self.subject} (by {self.created_by.username})'


class ForumPost(models.Model):
    """A single post/reply within a forum thread."""
    thread = models.ForeignKey(
        ForumThread,
        on_delete=models.CASCADE,
        related_name='posts',
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='forum_posts',
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    # Optional image upload per post
    image = models.ImageField(
        upload_to=upload_post_image_path,
        blank=True,
        null=True,
        help_text='Optional image attached to this post',
    )

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['thread', 'created_at']),
            models.Index(fields=['author', 'created_at']),
        ]

    def __str__(self):
        return f'Post by {self.author.username} in {self.thread.subject[:30]}'


class StudyGroup(models.Model):
    """A study group for collaborative learning and revision."""
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    subject = models.CharField(max_length=100, help_text='e.g. Mathematics, Physics, Programming')
    creator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_study_groups',
    )
    members = models.ManyToManyField(
        User,
        blank=True,
        related_name='joined_study_groups',
        help_text='Users who are members of this group',
    )
    is_public = models.BooleanField(
        default=True,
        db_index=True,
        help_text='Visible to all students or club members only',
    )
    max_members = models.PositiveIntegerField(
        default=20,
        help_text='Maximum number of members allowed',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Study Group'
        verbose_name_plural = 'Study Groups'

    def __str__(self):
        return self.name


class StudyGroupMembership(models.Model):
    """Membership record for a user in a study group with a role."""
    class RoleChoices(models.TextChoices):
        MEMBER = 'member',
        MODERATOR = 'moderator',
        ADMIN = 'admin',

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='study_group_memberships',
    )
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    role = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        default=RoleChoices.MEMBER,
    )
    joined_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('user', 'group')
        ordering = ['-joined_at']

    def __str__(self):
        return f'{self.user.username} → {self.group.name} ({self.get_role_display()})'


class StudyGroupPost(models.Model):
    """A post or discussion entry within a study group."""
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.CASCADE,
        related_name='posts',
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='study_group_posts',
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    image = models.ImageField(
        upload_to='study_groups/posts/%Y/%m/%d/',
        blank=True,
        null=True,
        help_text='Optional image attached to this post',
    )

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['group', 'created_at']),
            models.Index(fields=['author', 'created_at']),
        ]

    def __str__(self):
        return f'Post by {self.author.username} in {self.group.name}'


class StudyGroupNote(models.Model):
    """A shared note or resource within a study group."""
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.CASCADE,
        related_name='notes',
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='authored_notes',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    is_pinned = models.BooleanField(
        default=False,
        db_index=True,
    )

    class Meta:
        ordering = ['-is_pinned', '-updated_at']
        indexes = [
            models.Index(fields=['group', 'is_pinned', '-updated_at']),
        ]

    def __str__(self):
        return self.title


class DMThread(models.Model):
    """A 1-on-1 direct message conversation between two users."""
    participants = models.ManyToManyField(
        User,
        related_name='dm_threads',
        help_text='The two users participating in this conversation',
    )
    last_message_at = models.DateTimeField(auto_now=True, db_index=True)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text='Archived threads are hidden but not deleted',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        indexes = [
            models.Index(fields=['is_active', '-last_message_at']),
        ]

    def __str__(self):
        usernames = self.participants.order_by('username').values_list('username', flat=True)
        return f'DM: {", ".join(usernames)}'

    @property
    def other_participant(self, user):
        """Return the other participant in the thread given one participant."""
        return self.participants.exclude(pk=user.pk).first()


class DirectMessage(models.Model):
    """A single message within a DM thread."""
    thread = models.ForeignKey(
        DMThread,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_direct_messages',
    )
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_direct_messages',
    )
    content = models.TextField()
    # Read status per recipient (since each user has their own read state)
    is_read_by_recipient = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['thread', 'created_at']),
            models.Index(fields=['sender', 'created_at']),
            models.Index(fields=['recipient', 'is_read_by_recipient']),
        ]

    def __str__(self):
        return f'{self.sender.username} → {self.recipient.username}: {self.content[:30]}...' if len(self.content) > 30 else f'{self.sender.username} → {self.recipient.username}: {self.content}'