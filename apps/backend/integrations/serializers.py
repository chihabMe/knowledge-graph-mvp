from rest_framework import serializers

from integrations.models import DriveConnection


class DriveRootSelectionSerializer(serializers.Serializer):
    scope_type = serializers.ChoiceField(choices=DriveConnection.ScopeType.choices)
    root_id = serializers.CharField(max_length=255, trim_whitespace=True)


class DriveDelegatedSubjectSerializer(serializers.Serializer):
    delegated_subject_email = serializers.EmailField(
        allow_blank=True,
        required=True,
        trim_whitespace=True,
    )
