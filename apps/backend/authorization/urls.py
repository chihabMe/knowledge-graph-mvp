from django.conf import settings
from django.urls import path

from authorization.views import PermissionSyncDetailView, PermissionSyncView

urlpatterns = []
if settings.GOOGLE_PERMISSION_AUTHORITY == "delegated_acl":
    urlpatterns = [
        path("sync/", PermissionSyncView.as_view(), name="permission-sync"),
        path(
            "sync/<int:run_id>/",
            PermissionSyncDetailView.as_view(),
            name="permission-sync-detail",
        ),
    ]
