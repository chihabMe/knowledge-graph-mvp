from django.urls import path

from core.views import SmokeTaskView

urlpatterns = [
    path("tasks/smoke-test/", SmokeTaskView.as_view(), name="smoke-task"),
]
