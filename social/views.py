from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.urls import reverse, reverse_lazy
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator


from .models import (
    ForumCategory,
    ForumThread,
    ForumPost,
    StudyGroup,
    StudyGroupMembership,
    StudyGroupPost,
    StudyGroupNote,
    DMThread,
    DirectMessage,
)
from core.models import StudentProfile


# ============================================================
#  Forum Views
# ============================================================


def forum_category_list(request):
    """List all active forum categories."""
    categories = ForumCategory.objects.filter(is_active=True).order_by('order', 'name')
    return render(request, 'social/forum/category_list.html', {'categories': categories})


def forum_category_threads(request, category_slug):
    """List threads within a specific forum category."""
    category = get_object_or_404(ForumCategory, slug=category_slug, is_active=True)
    threads = ForumThread.objects.filter(category=category, is_locked=False).order_by('-is_pinned', '-updated_at', '-created_at')

    # Pagination
    paginator = Paginator(threads, 10)  # 10 threads per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'social/forum/category_threads.html', {
        'category': category,
        'page_obj': page_obj,
    })


def forum_thread_detail(request, thread_id):
    """Display a thread with all its posts, and handle reply creation."""
    thread = get_object_or_404(ForumThread, pk=thread_id, is_locked=False)
    posts = ForumPost.objects.filter(thread=thread).order_by('created_at')

    # Pagination for posts
    paginator = Paginator(posts, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # If thread is locked, only show posts, don't allow reply
    can_reply = not thread.is_locked and request.user.is_authenticated

    if request.method == 'POST' and can_reply:
        message = request.POST.get('message', '')
        if message.strip():
            ForumPost.objects.create(
                thread=thread,
                author=request.user,
                message=message,
            )
            # Update thread's last updated time
            thread.updated_at = thread.posts.latest('created_at').created_at
            thread.save()
            messages.success(request, 'Your reply has been posted.')
            return redirect('social:forum_thread_detail', thread_id=thread.pk)

    return render(request, 'social/forum/thread_detail.html', {
        'thread': thread,
        'page_obj': page_obj,
        'can_reply': can_reply,
    })


def forum_post_reply(request, post_id):
    """Handle reply to a specific post (nested reply)."""
    post = get_object_or_404(ForumPost, pk=post_id)
    thread = post.thread

    # Only allow replies if thread is not locked and user is authenticated
    if thread.is_locked or not request.user.is_authenticated:
        return JsonResponse({'error': 'Thread is locked or you are not logged in.'}, status=403)

    if request.method == 'POST':
        message = request.POST.get('message', '')
        if message.strip():
            ForumPost.objects.create(
                thread=thread,
                author=request.user,
                message=message,
            )
            messages.success(request, 'Your reply has been posted.')

    return redirect('social:forum_thread_detail', thread_id=thread.pk)


def forum_search(request):
    """Search forum threads and posts."""
    query = request.GET.get('q', '')
    results = []

    if query:
        # Search in thread subjects and post messages
        thread_results = ForumThread.objects.filter(
            Q(subject__icontains=query) | Q(message__icontains=query),
            is_locked=False
        ).distinct()[:20]

        post_results = ForumPost.objects.filter(
            Q(message__icontains=query),
        ).distinct()[:20]

        results = {
            'threads': thread_results,
            'posts': post_results,
        }

    return render(request, 'social/forum/search_results.html', {
        'query': query,
        'results': results,
    })


# ============================================================
#  Profile Views
# ============================================================


@method_decorator(login_required, name='dispatch')
class ProfileView(View):
    """Display a user's profile with bio, skills, and interests."""

    def get(self, request, user_id):
        user = get_object_or_404(User, pk=user_id)
        profile = user.student_profile

        # Get user's study groups and forum activity
        study_groups = user.joined_study_groups.all()[:5]
        forum_posts = user.forum_posts.order_by('-created_at')[:5]

        return render(request, 'social/profile/view.html', {
            'profile_user': user,
            'profile': profile,
            'study_groups': study_groups,
            'forum_posts': forum_posts,
        })


@method_decorator(login_required, name='dispatch')
class ProfileEditView(View):
    """Edit profile bio, skills, and interests."""

    def get(self, request):
        profile = request.user.student_profile
        return render(request, 'social/profile/edit.html', {'profile': profile})

    def post(self, request):
        profile = request.user.student_profile
        bio = request.POST.get('bio', '')
        skills = request.POST.get('skills', '')
        interests = request.POST.get('interests', '')

        profile.bio = bio
        profile.skills = skills
        profile.interests = interests
        profile.save()

        messages.success(request, 'Your profile has been updated.')
        return redirect('social:profile_view', user_id=request.user.pk)


# ============================================================
#  Study Group Views
# ============================================================


def study_group_list(request):
    """List all study groups with filtering options."""
    groups = StudyGroup.objects.all().order_by('-created_at')

    # Filter by subject
    subject = request.GET.get('subject')
    if subject:
        groups = groups.filter(subject__icontains=subject)

    # Filter by public/private
    is_public = request.GET.get('is_public')
    if is_public is not None:
        groups = groups.filter(is_public=is_public == '1')

    # Pagination
    paginator = Paginator(groups, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'social/study_groups/list.html', {
        'page_obj': page_obj,
        'subject_filter': subject,
        'is_public_filter': is_public,
    })


@method_decorator(login_required, name='dispatch')
class StudyGroupCreateView(View):
    """Create a new study group."""

    def get(self, request):
        return render(request, 'social/study_groups/create.html')

    def post(self, request):
        name = request.POST.get('name')
        slug = request.POST.get('slug', '').lower().replace(' ', '-')
        description = request.POST.get('description', '')
        subject = request.POST.get('subject')

        # Generate slug if not provided
        if not slug:
            slug = name.lower().replace(' ', '-')

        StudyGroup.objects.create(
            name=name,
            slug=slug,
            description=description,
            subject=subject,
            creator=request.user,
        )

        # Add creator as admin/member
        StudyGroupMembership.objects.create(
            user=request.user,
            group=StudyGroup.objects.latest('pk'),
            role=StudyGroupMembership.RoleChoices.ADMIN,
        )

        messages.success(request, 'Study group created successfully.')
        return redirect('social:study_group_detail', slug=slug)


def study_group_detail(request, slug):
    """Display study group details and membership."""
    group = get_object_or_404(StudyGroup, slug=slug)

    # Check if user can view this group
    if not group.is_public and request.user not in group.members.all():
        if request.user.is_authenticated:
            # Check if user is a member or request to join
            membership = StudyGroupMembership.objects.filter(user=request.user, group=group).first()
            if not membership:
                messages.error(request, 'You do not have permission to view this private group.')
                return redirect('social:study_group_list')
        else:
            messages.error(request, 'You must be logged in to view this private group.')
            return redirect('social:login')

    # Get members
    members = StudyGroupMembership.objects.filter(group=group).select_related('user')

    # Pagination for posts
    posts = group.posts.order_by('-created_at')
    paginator = Paginator(posts, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Get notes
    notes = StudyGroupNote.objects.filter(group=group).order_by('-is_pinned', '-updated_at')

    # Check if current user is a member
    is_member = request.user in group.members.all() if request.user.is_authenticated else False

    return render(request, 'social/study_groups/detail.html', {
        'group': group,
        'members': members,
        'page_obj': page_obj,
        'notes': notes,
        'is_member': is_member,
    })


@method_decorator(login_required, name='dispatch')
def study_group_join(request, slug):
    """Add a user to a study group."""
    group = get_object_or_404(StudyGroup, slug=slug)

    # Public groups: auto-join
    # Private groups: check permissions
    if not group.is_public and request.user not in group.members.all():
        # Check if user already has a membership request or is creator
        membership, created = StudyGroupMembership.objects.get_or_create(
            user=request.user,
            group=group,
            defaults={'role': StudyGroupMembership.RoleChoices.MEMBER},
        )
        if not created:
            messages.info(request, 'You are already a member of this group.')
        else:
            messages.success(request, 'You have joined the study group.')
    elif request.user not in group.members.all():
        # Add user as member
        group.members.add(request.user)
        StudyGroupMembership.objects.get_or_create(
            user=request.user,
            group=group,
            defaults={'role': StudyGroupMembership.RoleChoices.MEMBER},
        )
        messages.success(request, 'You have joined the study group.')
    else:
        messages.info(request, 'You are already a member of this group.')

    return redirect('social:study_group_detail', slug=slug)


@method_decorator(login_required, name='dispatch')
def study_group_leave(request, slug):
    """Remove a user from a study group."""
    group = get_object_or_404(StudyGroup, slug=slug)

    # Don't allow the creator to leave if they're the only admin
    membership = StudyGroupMembership.objects.filter(user=request.user, group=group).first()
    if membership and membership.role == StudyGroupMembership.RoleChoices.ADMIN and group.creator == request.user:
        messages.error(request, 'Group creators cannot leave their own group. Transfer ownership first.')
        return redirect('social:study_group_detail', slug=slug)

    # Remove membership
    StudyGroupMembership.objects.filter(user=request.user, group=group).delete()
    group.members.remove(request.user)
    messages.success(request, 'You have left the study group.')

    return redirect('social:study_group_detail', slug=slug)


def study_group_posts(request, slug):
    """Display posts within a study group."""
    group = get_object_or_404(StudyGroup, slug=slug)
    posts = group.posts.order_by('-created_at')

    paginator = Paginator(posts, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'social/study_groups/posts.html', {
        'group': group,
        'page_obj': page_obj,
    })


def study_group_notes(request, slug):
    """Display notes within a study group."""
    group = get_object_or_404(StudyGroup, slug=slug)
    notes = StudyGroupNote.objects.filter(group=group).order_by('-is_pinned', '-updated_at')

    return render(request, 'social/study_groups/notes.html', {
        'group': group,
        'notes': notes,
    })


# ============================================================
#  Direct Message Views
# ============================================================


@login_required
def dm_inbox(request):
    """Display user's direct message inbox with active threads."""
    # Get threads where user is one of the participants
    threads = DMThread.objects.filter(participants=request.user).order_by('-last_message_at')

    # Pagination
    paginator = Paginator(threads, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'social/dm/inbox.html', {
        'page_obj': page_obj,
    })


@method_decorator(login_required, name='dispatch')
def dm_start(request, recipient_id):
    """Start a new DM conversation with another user."""
    recipient = get_object_or_404(User, pk=recipient_id)

    if request.user == recipient:
        messages.error(request, 'You cannot start a DM conversation with yourself.')
        return redirect('social:dm_inbox')

    # Check if a thread already exists between these two users
    # Simple approach: get or create thread with exactly these two participants
    thread, created = DMThread.objects.get_or_create(defaults={'is_active': True})
    thread.participants.add(request.user, recipient)

    return redirect('social:dm_thread_detail', thread_id=thread.pk)


@method_decorator(login_required, name='dispatch')
def dm_thread_detail(request, thread_id):
    """Display a DM thread with messages."""
    thread = get_object_or_404(DMThread, pk=thread_id, is_active=True)

    # Check if user is a participant
    if request.user not in thread.participants.all():
        messages.error(request, 'You are not a participant in this conversation.')
        return redirect('social:dm_inbox')

    # Get all messages in this thread
    messages = DirectMessage.objects.filter(thread=thread).order_by('created_at')

    # Mark messages as read for the current user
    DirectMessage.objects.filter(
        thread=thread,
        recipient=request.user,
        is_read_by_recipient=False
    ).update(is_read_by_recipient=True)

    # Pagination
    paginator = Paginator(messages, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Check if can send messages
    can_send = request.user in thread.participants.all()

    if request.method == 'POST' and can_send:
        content = request.POST.get('content', '')
        if content.strip():
            DirectMessage.objects.create(
                thread=thread,
                sender=request.user,
                recipient=thread.other_participant(request.user) if request.user in thread.participants else recipient,
                content=content,
            )
            # Update thread's last message time
            thread.last_message_at = timezone.now()
            thread.save()
            messages.success(request, 'Message sent.')
            return redirect('social:dm_thread_detail', thread_id=thread.pk)

    return render(request, 'social/dm/thread_detail.html', {
        'thread': thread,
        'page_obj': page_obj,
        'other_participant': thread.other_participant(request.user),
        'can_send': can_send,
    })


def dm_send_message(request, message_id):
    """API endpoint for sending a direct message."""
    message = get_object_or_404(DirectMessage, pk=message_id)
    # This is mainly for AJAX/JSON endpoints
    if request.user == message.sender:
        return JsonResponse({
            'id': message.id,
            'sender': message.sender.username,
            'content': message.content,
            'created_at': message.created_at.strftime('%H:%M:%S'),
            'is_read': message.is_read_by_recipient,
        })
    return JsonResponse({'error': 'Not your message'}, status=403)