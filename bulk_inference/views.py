from rest_framework.views import APIView
from rest_framework.response import Response
from .serializers import BatchInputSerializer, BatchSerializer
from rest_framework import status
from .models import Batch

class Batches(APIView):

    def post(self, request, *args, **kwargs):
        input_serializer = BatchInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        # We want to save the machine selection in exactly one place in the DB
        # Extract it from the metadata object and save it at the top level of the Batch object
        validated_data = input_serializer.validated_data
        metadata = validated_data['metadata'].copy()
        machine = metadata.pop('machine')

        batch_data = {
            'input_file_id': validated_data['input_file_id'],
            'endpoint': validated_data['endpoint'],
            'metadata': metadata,
            'machine': machine
        }

        batch_serializer = BatchSerializer(data=batch_data)
        if not batch_serializer.is_valid():
            return Response(batch_serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        batch_serializer.save()
        return Response(batch_serializer.data, status=status.HTTP_201_CREATED)

    def get(self, request, id=None, *args, **kwargs):
        try:
            batch = Batch.objects.get(id=id)
            output_serializer = BatchSerializer(batch)
            return Response(output_serializer.data, status=status.HTTP_200_OK)
        except Batch.DoesNotExist:
            return Response({'error': 'Batch not found'}, status=status.HTTP_404_NOT_FOUND)
