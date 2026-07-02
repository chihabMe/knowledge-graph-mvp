from django.urls import path

from health.views import HealthView

urlpatterns = [
    path("", HealthView.as_view(), name="health"),
]
