from django.conf import settings
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
    path("drive/connection/root/", DriveRootSelectionView.as_view(), name="drive-root-selection"),
    path("drive/sync/", DriveSyncView.as_view(), name="drive-sync"),
]

if settings.GOOGLE_PERMISSION_AUTHORITY == "delegated_acl":
    urlpatterns += [
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
    ]
