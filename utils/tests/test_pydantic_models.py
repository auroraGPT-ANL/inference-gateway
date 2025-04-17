from utils.pydantic_models.openai_chat_completions import OpenAIChatCompletions
from utils.pydantic_models.openai_completions import OpenAICompletions
from utils.pydantic_models.openai_embeddings import OpenAIEmbeddings
from pydantic import ValidationError
from rest_framework.test import APITestCase
import json

# Constants
COMPLETIONS = "completions/"
CHAT_COMPLETIONS = "chat/completions/"
EMBEDDINGS = "embeddings/"

# Test OpenAI pydantic models
class UtilsPydanticModelsTestCase(APITestCase):

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

        # Assign pydantic models
        self.pydantic_models = {
            COMPLETIONS: OpenAICompletions,
            CHAT_COMPLETIONS: OpenAIChatCompletions,
            EMBEDDINGS: OpenAIEmbeddings
        }

    # Test OpenAICompletions pydantic model for validation
    def test_OpenAICompletions_validation(self):
        self.__generic_serializer_validation(COMPLETIONS)
    
    # Test OpenAIChatCompletions pydantic model for validation
    def test_OpenAIChatCompletions_validation(self):
        self.__generic_serializer_validation(CHAT_COMPLETIONS)

    # Test OpenAIEmbeddings pydantic model for validation
    def test_OpenAIEmbeddings_validation(self):
        self.__generic_serializer_validation(EMBEDDINGS)

    # Reusable function to validate pydantic model definitions
    def __generic_serializer_validation(self, model_key):

        # For each valid set of parameters ...
        for valid_params in self.valid_params[model_key]:

            # Make sure the pydantic model does not raise a validation error
            try:
                self.pydantic_models[model_key](**valid_params)
            except ValidationError:
                self.fail(f"The following data was supposed to be valid, but was flagged as invalid: {valid_params}")

        # For each invalid set of parameters ...
        for invalid_params in self.invalid_params[model_key]:

            # Make sure the pydantic model raises a validation error
            try:
                self.pydantic_models[model_key](**invalid_params)
                self.fail(f"The following data was supposed to be invalid, but was flagged as valid: {valid_params}")
            except ValidationError:
                pass

