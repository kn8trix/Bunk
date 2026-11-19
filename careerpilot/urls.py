"""CareerPilot URL routes, mounted from config/urls.py.

Two layers:

  * **Pages** — the dark Bunk UI at /cv/, /hunter/, /fit-score/, /chat/,
    /tracker/, /calendar/.
  * **JSON API** — the same six features under /api/… for the front-end and
    any future native client.

The API moved under /api/ when the HTML pages took the clean URLs; the
view functions and payloads are unchanged from the original port.
"""

from django.urls import path

from careerpilot import views

app_name = 'careerpilot'

urlpatterns = [
    # --- HTML pages --------------------------------------------------------
    path('cv/', views.cv_page, name='cv_page'),
    path('hunter/', views.hunter_page, name='hunter_page'),
    path('fit-score/', views.fit_page, name='fit_page'),
    path('chat/', views.chat_page, name='chat_page'),
    path('tracker/', views.tracker_page, name='tracker_page'),
    path('calendar/', views.calendar_page, name='calendar_page'),

    # --- My CV API ---------------------------------------------------------
    path('api/cv/', views.cv_list, name='cv_list'),
    path('api/cv/upload/', views.cv_upload, name='cv_upload'),
    path('api/cv/<int:cv_id>/', views.cv_detail, name='cv_detail'),
    path('api/cv/<int:cv_id>/chunks/', views.cv_chunks, name='cv_chunks'),
    path('api/cv/<int:cv_id>/activate/', views.cv_activate, name='cv_activate'),

    # --- Job Hunter API ----------------------------------------------------
    path('api/hunter/', views.hunt_search, name='hunt_search'),
    path('api/hunter/saved/', views.hunt_saved_list, name='hunt_saved_list'),
    path('api/hunter/save/', views.hunt_save, name='hunt_save'),
    path('api/hunter/saved/<int:job_id>/', views.hunt_save_delete, name='hunt_save_delete'),

    # --- Fit Score API -----------------------------------------------------
    path('api/fit-score/', views.fit_score_view, name='fit_score'),
    path('api/fit-score/history/', views.fit_score_history, name='fit_score_history'),

    # --- Assistant API -----------------------------------------------------
    path('api/chat/threads/', views.chat_threads, name='chat_threads'),
    path('api/chat/threads/<int:thread_id>/', views.chat_thread_detail, name='chat_thread_detail'),
    path('api/chat/threads/<int:thread_id>/messages/', views.chat_messages, name='chat_messages'),
    path('api/chat/modes/', views.chat_modes, name='chat_modes'),

    # --- Tracker API -------------------------------------------------------
    path('api/tracker/applications/', views.applications, name='applications'),
    path('api/tracker/applications/<int:application_id>/', views.application_detail, name='application_detail'),
    path('api/tracker/stats/', views.tracker_stats, name='tracker_stats'),

    # --- Calendar API ------------------------------------------------------
    path('api/calendar/', views.calendar, name='calendar'),
    path('api/calendar/todos/', views.todos, name='todos'),
    path('api/calendar/todos/<int:todo_id>/', views.todo_detail, name='todo_detail'),
    path('api/calendar/todos/<int:todo_id>/done/', views.todo_done, name='todo_done'),
    path('api/calendar/goals/', views.goals, name='goals'),
]
