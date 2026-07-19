from collections.abc import Mapping

from rest_framework import serializers


class QueryRequestSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000, trim_whitespace=True)

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown_fields = sorted(set(data) - set(self.fields))
            if unknown_fields:
                raise serializers.ValidationError(
                    {field: ["Unexpected field."] for field in unknown_fields}
                )
        return super().to_internal_value(data)
