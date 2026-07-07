from django.urls import path

from integrations.views import DriveSyncView

urlpatterns = [
    path("drive/sync/", DriveSyncView.as_view(), name="drive-sync"),
]
