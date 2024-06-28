from rest_framework.test import APITestCase
from rest_framework.exceptions import ValidationError
from utils.serializers import OpenAILegacyParamSerializer
import json

# Test utils/serializers.py
class UtilsSerializersTestCase(APITestCase):

    # Initialization
    @classmethod
    def setUp(self):
        """
            Initialization that will only happen once before running all tests.
        """

        # Path to the test json files
        base_path = "utils/tests/json"

        # Load valid OpenAI model parameters
        with open(f"{base_path}/valid_legacy_openai.json") as json_file:
            self.valid_legacy_openai_params = json.load(json_file)

        # Load invalid OpenAI model parameters
        with open(f"{base_path}/invalid_legacy_openai.json") as json_file:
            self.invalid_legacy_openai_params = json.load(json_file)


    # Test OpenAILegacyParamSerializer for validation
    def test_OpenAILegacyParamSerializer_validation(self):

        # For each valid set of parameters ...
        for valid_params in self.valid_legacy_openai_params:

            # Send the data to the serializer
            serializer = OpenAILegacyParamSerializer(data=valid_params)
            self.assertTrue(serializer.is_valid(raise_exception=True))

        # For each invalid set of parameters ...
        for invalid_params in self.invalid_legacy_openai_params:

            # Send the data to the serializer
            serializer = OpenAILegacyParamSerializer(data=invalid_params)
            if serializer.is_valid(raise_exception=False):
                print(invalid_params)
            self.assertFalse(serializer.is_valid(raise_exception=False))

        data = {
        "model": "model",
        "prompt": "prompt",
        
        }
        serializer = OpenAILegacyParamSerializer(data=data)
        serializer.is_valid(raise_exception=True)

    
              
            
        #self.assertEqual("a", "b")