import json
from collections.abc import Mapping

from rest_framework import serializers

MAX_OPEN_WEBUI_MESSAGES = 32
MAX_OPEN_WEBUI_MESSAGE_CHARS = 8_000
MAX_OPEN_WEBUI_TOTAL_MESSAGE_CHARS = 24_000
MAX_OPEN_WEBUI_TOOLS = 64
MAX_OPEN_WEBUI_TOOLS_CHARS = 128_000
MAX_QUERY_CHARS = 2_000


class RejectUnknownFieldsSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown_fields = sorted(set(data) - set(self.fields))
            if unknown_fields:
                raise serializers.ValidationError(
                    {field: ["Unexpected field."] for field in unknown_fields}
                )
        return super().to_internal_value(data)


class QueryRequestSerializer(RejectUnknownFieldsSerializer):
    question = serializers.CharField(max_length=MAX_QUERY_CHARS, trim_whitespace=True)


class OpenWebUIMessageSerializer(RejectUnknownFieldsSerializer):
    role = serializers.ChoiceField(choices=("system", "assistant", "user"))
    content = serializers.CharField(
        allow_blank=True,
        max_length=MAX_OPEN_WEBUI_MESSAGE_CHARS,
        trim_whitespace=False,
    )


class OpenWebUIChatCompletionRequestSerializer(RejectUnknownFieldsSerializer):
    model = serializers.CharField(max_length=128, trim_whitespace=False)
    messages = OpenWebUIMessageSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_OPEN_WEBUI_MESSAGES,
    )
    stream = serializers.BooleanField(default=False)
    # Open WebUI 0.10.2 includes its bounded tool inventory even when this
    # deployment does not permit tool execution. Accept it only for protocol
    # compatibility, validate its size, and discard it before orchestration.
    tools = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=True,
        max_length=MAX_OPEN_WEBUI_TOOLS,
        required=False,
        write_only=True,
    )

    def validate_model(self, value):
        from django.conf import settings

        if value != settings.OPEN_WEBUI_MODEL_ID:
            raise serializers.ValidationError("Unknown model.")
        return value

    def validate_tools(self, value):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > MAX_OPEN_WEBUI_TOOLS_CHARS:
            raise serializers.ValidationError("Tool metadata is too large.")
        return value

    def validate(self, attrs):
        attrs.pop("tools", None)
        messages = attrs["messages"]
        if (
            sum(len(message["content"]) for message in messages)
            > MAX_OPEN_WEBUI_TOTAL_MESSAGE_CHARS
        ):
            raise serializers.ValidationError(
                {"messages": ["Combined message content is too large."]}
            )

        question = next(
            (
                message["content"].strip()
                for message in reversed(messages)
                if message["role"] == "user" and message["content"].strip()
            ),
            "",
        )
        if not question:
            raise serializers.ValidationError(
                {"messages": ["A non-empty user message is required."]}
            )
        if len(question) > MAX_QUERY_CHARS:
            raise serializers.ValidationError(
                {"messages": [f"The selected user question exceeds {MAX_QUERY_CHARS} characters."]}
            )
        attrs["question"] = question
        return attrs


class OpenWebUIModelSerializer(serializers.Serializer):
    id = serializers.CharField()
    object = serializers.CharField()
    created = serializers.IntegerField()
    owned_by = serializers.CharField()


class OpenWebUIModelListSerializer(serializers.Serializer):
    object = serializers.CharField()
    data = OpenWebUIModelSerializer(many=True)
