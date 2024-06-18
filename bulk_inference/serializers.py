from rest_framework import serializers
from .models import Batch

ENDPOINT_CHOICES = [
    ('/v1/chat/completions', 'Chat Completions'),
    ('/v1/completions', 'Completions'),
    ('/v1/embeddings', 'Embeddings'),
]

MACHINE_CHOICES = [
    ('polaris', 'Polaris'),
    ('aurora', 'Aurora'),
]


class BatchInputSerializer(serializers.Serializer):
    input_file_path = serializers.CharField(max_length=250)
    endpoint = serializers.ChoiceField(choices=ENDPOINT_CHOICES)
    machine = serializers.ChoiceField(choices=MACHINE_CHOICES)
    metadata = serializers.JSONField()


class BatchSerializer(serializers.ModelSerializer):
    object = serializers.CharField(default='batch', read_only=True)

    class Meta:
        model = Batch
        fields = '__all__'
        fields.append('object')
