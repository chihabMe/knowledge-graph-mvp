from django.urls import path

from integrations.views import DriveRootListView, DriveRootSelectionView, DriveSyncView

urlpatterns = [
    path("drive/roots/", DriveRootListView.as_view(), name="drive-roots"),
    path("drive/connection/root/", DriveRootSelectionView.as_view(), name="drive-root-selection"),
    path("drive/sync/", DriveSyncView.as_view(), name="drive-sync"),
]
