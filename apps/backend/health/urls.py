from django.urls import path

from health.views import FreshnessView, HealthView

urlpatterns = [
    path("", HealthView.as_view(), name="health"),
    path("freshness/", FreshnessView.as_view(), name="health-freshness"),
]
