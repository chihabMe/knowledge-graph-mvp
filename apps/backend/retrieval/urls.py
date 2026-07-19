from django.urls import path

from retrieval.views import QueryView

urlpatterns = [
    path("", QueryView.as_view(), name="query"),
]
