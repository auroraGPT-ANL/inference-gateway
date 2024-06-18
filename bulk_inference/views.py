from rest_framework.views import APIView
from rest_framework.response import Response
from .serializers import BatchInputSerializer, BatchSerializer
from rest_framework import status
from .models import Batch

from utils.auth_utils import globus_authenticated

# Create your views here.
class Batches(APIView):

    # @globus_authenticated
    def post(self, request, framework, *args, **kwargs):
        input_serializer = BatchInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        # Create and save a Batch object
        batch_request = input_serializer.validated_data
        batch = Batch.objects.create(**batch_request)
        batch.save()
        
        # Return batch as JSON
        output_serializer = BatchSerializer(batch)
        return Response(output_serializer.validated_data, status=status.HTTP_201_CREATED)

    # @globus_authenticated
    def get(self, request, framework, *args, **kwargs):
        pass

