from django.urls import path

from . import views

app_name = 'social'

urlpatterns = [
    # Forum URLs
    path('', views.forum_category_list, name='forum_category_list'),
    path('forum/<slug:category_slug>/', views.forum_category_threads, name='forum_category_threads'),
    path('thread/<int:thread_id>/', views.forum_thread_detail, name='forum_thread_detail'),
    path('profile/<int:user_id>/', views.ProfileView.as_view(), name='profile_view'),
    path('profile/edit/', views.ProfileEditView.as_view(), name='profile_edit'),
    # Study groups - these MUST come before the generic category slug pattern
    path('study-groups/', views.study_group_list, name='study_group_list'),
    path('study-group/create/', views.StudyGroupCreateView.as_view(), name='study_group_create'),
    path('study-group/<slug:slug>/', views.study_group_detail, name='study_group_detail'),
    path('study-group/<slug:slug>/join/', views.study_group_join, name='study_group_join'),
    path('study-group/<slug:slug>/leave/', views.study_group_leave, name='study_group_leave'),
    path('study-group/<slug:slug>/posts/', views.study_group_posts, name='study_group_posts'),
    path('study-group/<slug:slug>/notes/', views.study_group_notes, name='study_group_notes'),
    # Direct messages
    path('dm/', views.dm_inbox, name='dm_inbox'),
    path('dm/start/<int:recipient_id>/', views.dm_start, name='dm_start'),
    path('dm/thread/<int:thread_id>/', views.dm_thread_detail, name='dm_thread_detail'),
    # This pattern must come LAST - it catches generic category slugs
    # Study groups and DM routes above must be defined before this
    path('<slug:category_slug>/', views.forum_category_threads, name='forum_category_threads'),
]