from django.urls import path

from integrations.views import (
    DriveDelegatedSubjectView,
    DrivePermissionCheckView,
    DriveRootListView,
    DriveRootSelectionView,
    DriveSyncView,
)

urlpatterns = [
    path("drive/roots/", DriveRootListView.as_view(), name="drive-roots"),
    path(
        "drive/permissions/check/",
        DrivePermissionCheckView.as_view(),
        name="drive-permission-check",
    ),
    path(
        "drive/connection/delegated-subject/",
        DriveDelegatedSubjectView.as_view(),
        name="drive-delegated-subject",
    ),
    path("drive/connection/root/", DriveRootSelectionView.as_view(), name="drive-root-selection"),
    path("drive/sync/", DriveSyncView.as_view(), name="drive-sync"),
]
