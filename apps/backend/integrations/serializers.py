from rest_framework import serializers

from integrations.models import DriveConnection


class DriveRootSelectionSerializer(serializers.Serializer):
    scope_type = serializers.ChoiceField(choices=DriveConnection.ScopeType.choices)
    root_id = serializers.CharField(max_length=255, trim_whitespace=True)
