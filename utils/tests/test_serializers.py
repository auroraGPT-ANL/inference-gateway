from rest_framework.test import APITestCase
from utils.serializers import OpenAICompletionsParamSerializer, OpenAIChatCompletionsParamSerializer, OpenAIEmbeddingsParamSerializer
import json

# Constants
COMPLETIONS = "completions/"
CHAT_COMPLETIONS = "chat/completions/"
EMBEDDINGS = "embeddings/"

# Test utils/serializers.py
class UtilsSerializersTestCase(APITestCase):

    # Initialization
    @classmethod
    def setUp(self):
        """
            Initialization that will only happen once before running all tests.
        """

        # Load test input data (OpenAI format)
        base_path = "utils/tests/json"
        self.valid_params = {}
        with open(f"{base_path}/valid_completions.json") as json_file:
            self.valid_params[COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/valid_chat_completions.json") as json_file:
            self.valid_params[CHAT_COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/valid_embeddings.json") as json_file:
            self.valid_params[EMBEDDINGS] = json.load(json_file)
        self.invalid_params = {}
        with open(f"{base_path}/invalid_completions.json") as json_file:
            self.invalid_params[COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/invalid_chat_completions.json") as json_file:
            self.invalid_params[CHAT_COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/invalid_embeddings.json") as json_file:
            self.invalid_params[EMBEDDINGS] = json.load(json_file)

        # Assign serializers
        self.serializers = {
            COMPLETIONS: OpenAICompletionsParamSerializer,
            CHAT_COMPLETIONS: OpenAIChatCompletionsParamSerializer,
            EMBEDDINGS: OpenAIEmbeddingsParamSerializer
        }


    # Test OpenAICompletionsParamSerializer for validation
    def test_OpenAICompletionsParamSerializer_validation(self):
        self.__generic_serializer_validation(COMPLETIONS)
    

    # Test OpenAIChatCompletionsParamSerializer for validation
    def test_OpenAIChatCompletionsParamSerializer_validation(self):
        self.__generic_serializer_validation(CHAT_COMPLETIONS)


    # Test OpenAIEmbeddingsParamSerializer for validation
    def test_OpenAIEmbeddingsParamSerializer_validation(self):
        self.__generic_serializer_validation(EMBEDDINGS)


    # Reusable generic serializer validation
    def __generic_serializer_validation(self, serializer_key):

        # For each valid set of parameters ...
        for valid_params in self.valid_params[serializer_key]:

            # Send the data to the serializer and make sure the data is valid
            serializer = self.serializers[serializer_key](data=valid_params)
            self.assertTrue(serializer.is_valid(raise_exception=True))

        # For each invalid set of parameters ...
        for invalid_params in self.invalid_params[serializer_key]:

            # Send the data to the serializer
            serializer = self.serializers[serializer_key](data=invalid_params)

            # Make sure the data is not valid
            if serializer.is_valid(raise_exception=False):
                print("  ",invalid_params) # TODO: Is there a better way to print out info with self.assertFalse?
            self.assertFalse(serializer.is_valid(raise_exception=False))

