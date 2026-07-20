from django.urls import path

from retrieval.open_webui_views import (
    OpenWebUIChatCompletionsView,
    OpenWebUIModelsView,
)

app_name = "open-webui"

urlpatterns = [
    path("models", OpenWebUIModelsView.as_view(), name="models"),
    path("chat/completions", OpenWebUIChatCompletionsView.as_view(), name="chat-completions"),
]
