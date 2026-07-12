from django.urls import path

from authorization.views import PermissionSyncDetailView, PermissionSyncView

urlpatterns = [
    path("sync/", PermissionSyncView.as_view(), name="permission-sync"),
    path("sync/<int:run_id>/", PermissionSyncDetailView.as_view(), name="permission-sync-detail"),
]
