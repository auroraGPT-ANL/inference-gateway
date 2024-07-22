from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from utils import serializer_utils

# TODO: overwrite is_valid or validate function to raise errors when passing extra parameters
# NOTE: All of utility fields and serializers are in utils/serializer_utils.py

# Mandatory and optionsl parameter arguments
MAND = {"required":True}
OPT = {"required": False}
OPT_NULL = {"required": False, "allow_null": True}


# OpenAI Legacy parameter serializer
# https://platform.openai.com/docs/api-reference/completions/create
class OpenAILegacyParamSerializer(serializer_utils.BaseSerializers):

    # Mandatory model parameters
    model = serializer_utils.TrueCharField(allow_blank=False, **MAND) #TODO: Provide validation on choices (ChoiceField)
    prompt = serializer_utils.OpenAIPromptField(**MAND)

    # Optional model parameters
    best_of = serializers.IntegerField(min_value=0, max_value=20, **OPT_NULL) #TODO: 1) dependent on n
    echo = serializers.BooleanField(**OPT_NULL)
    frequency_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    logit_bias = serializer_utils.OpenAILogitBiasField(**OPT)
    logprobs = serializers.IntegerField(min_value=0, max_value=5, **OPT_NULL)
    max_tokens = serializers.IntegerField(min_value=0, **OPT_NULL)
    n = serializers.IntegerField(min_value=1, max_value=128, **OPT_NULL)
    presence_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    seed = serializers.IntegerField(min_value=-9223372036854775808, max_value=9223372036854775807, **OPT_NULL)
    stop = serializer_utils.OpenAIStopField(**OPT_NULL)
    stream = serializers.BooleanField(**OPT_NULL)
    stream_options = serializer_utils.OpenAIStreamOptionsField(**OPT_NULL) # TODO: 1) Only if stream==True
    suffix = serializer_utils.TrueCharField(**OPT_NULL)
    temperature = serializers.FloatField(min_value=0, max_value=2, **OPT_NULL)
    top_p = serializers.FloatField(min_value=0, max_value=1, **OPT_NULL)
    user = serializer_utils.TrueCharField(**OPT)


# OpenAI chat parameter serializer
# https://platform.openai.com/docs/api-reference/chat/create
class OpenAIParamSerializer(serializer_utils.BaseSerializers):

    # Mandatory model parameters
    messages = serializers.ListField(child=serializer_utils.OpenAIMessageField(), allow_empty=False, **MAND)
    model = serializer_utils.TrueCharField(allow_blank=False, **MAND)

    # Optional model parameters
    frequency_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    logit_bias = serializer_utils.OpenAILogitBiasField(**OPT)
    logprobs = serializers.BooleanField(**OPT_NULL)
    top_logprobs = serializers.IntegerField(min_value=0, max_value=20, **OPT_NULL) #TODO: logsprobs must be True to use it
    max_tokens = serializers.IntegerField(min_value=0, **OPT_NULL)
    n = serializers.IntegerField(min_value=1, max_value=128, **OPT_NULL)
    presence_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    response_format = serializer_utils.OpenAIResponseFormatField(**OPT)
    seed = serializers.IntegerField(min_value=-9223372036854775808, max_value=9223372036854775807, **OPT_NULL)
    service_tier = serializers.ChoiceField(choices=["auto", "default"], **OPT_NULL)
    stop = serializer_utils.OpenAIStopField(**OPT_NULL)
    stream = serializers.BooleanField(**OPT_NULL)
    stream_options = serializer_utils.OpenAIStreamOptionsField(**OPT_NULL) # TODO: 1) Only if stream==True
    temperature = serializers.FloatField(min_value=0, max_value=2, **OPT_NULL)
    top_p = serializers.FloatField(min_value=0, max_value=1, **OPT_NULL)
    tools = serializers.ListField(child=serializer_utils.OpenAIToolSerializer(), max_length=128, **OPT)
    tool_choice = serializer_utils.OpenAIToolChoiceField(**OPT)
    parallel_tool_calls = serializers.BooleanField(**OPT)
    user = serializer_utils.TrueCharField(**OPT)


# OpenAI embeddings parameter serializer
# https://platform.openai.com/docs/api-reference/embeddings/create
class OpenAIEmbeddingsParamSerializer(serializer_utils.BaseSerializers):

    # Mandatory model parameters
    input = serializer_utils.OpenAIEmbeddingsInputField(**MAND)
    model = serializers.ChoiceField(choices=["text-embedding-ada-002", "text-embedding-3-small", "text-embedding-3-large"], **MAND)

    # Optional model parameters
    encoding_format = serializers.ChoiceField(choices=["float", "base64"], **OPT)
    dimensions = serializers.IntegerField(min_value=1, **OPT)
    user = serializer_utils.TrueCharField(**OPT)