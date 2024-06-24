from rest_framework import serializers
from .models import Batch

ENDPOINT_CHOICES = [
    ('/v1/chat/completions', 'Chat Completions'),
    ('/v1/completions', 'Completions'),
    ('/v1/embeddings', 'Embeddings'),
]

MACHINE_CHOICES = set(['polaris', 'aurora'])

class BatchInputSerializer(serializers.Serializer):
    input_file_id = serializers.CharField(max_length=250, required=True)
    endpoint = serializers.ChoiceField(choices=ENDPOINT_CHOICES, required=True)
    metadata = serializers.JSONField(required=True)

    def validate_metadata(self, value):
        if 'machine' not in value:
            raise serializers.ValidationError("Metadata must contain a 'machine' field")
        if value['machine'] not in MACHINE_CHOICES:
            raise serializers.ValidationError(f"Invalid machine choice. Must be one of: {', '.join(MACHINE_CHOICES)}")
        return value

class BatchSerializer(serializers.ModelSerializer):
    object = serializers.CharField(default='batch', read_only=True)
    metadata = serializers.JSONField(required=True)

    class Meta:
        model = Batch
        exclude = ['name', 'username']

    # We store the `machine` field at the top level in the DB for easy querying
    # But we want to return it back to the user in the metadata field
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['metadata']['machine'] = instance.machine
        return representation
