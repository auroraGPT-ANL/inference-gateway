from rest_framework import serializers
from utils import fields

# TODO: overwrite is_valid or validate function to raise errors when passing extra parameters
# NOTE: All of utility fields and serializers are in utils/fields.py

# Mandatory and optionsl parameter arguments
MAND = {"required":True}
OPT = {"required": False}
OPT_NULL = {"required": False, "allow_null": True}


# OpenAI Legacy parameter serializer
# https://platform.openai.com/docs/api-reference/completions/create
class OpenAILegacyParamSerializer(serializers.Serializer):

    # Mandatory model parameters
    model = fields.TrueCharField(allow_blank=False, **MAND) #TODO: Provide validation on choices (ChoiceField)
    prompt = fields.OpenAIPromptField(**MAND)

    # Optional model parameters
    best_of = serializers.IntegerField(**OPT_NULL) #TODO: 1) dependent on n, 2) needs limits min max?
    echo = serializers.BooleanField(**OPT_NULL)
    frequency_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    logit_bias = fields.OpenAILogitBiasField(**OPT)
    logprobs = serializers.IntegerField(min_value=0, max_value=5, **OPT_NULL)
    max_tokens = serializers.IntegerField(min_value=0, **OPT_NULL) # TODO: is min_value = 1 ok?
    n = serializers.IntegerField(min_value=0, **OPT_NULL) # TODO: is min_value = 0 ok?
    presence_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    seed = serializers.IntegerField(**OPT_NULL) # TODO: Any constraints on this value?
    stop = fields.OpenAIStopField(**OPT_NULL) # TODO: Did I get this one right?
    stream = serializers.BooleanField(**OPT_NULL)
    stream_options = fields.OpenAIStreamOptionsField(**OPT_NULL) # TODO: 1) Only if stream==True, 2) Did I get this one right?
    suffix = fields.TrueCharField(**OPT_NULL) #TODO: only supported for gpt-3.5-turbo-instruct
    temperature = serializers.FloatField(min_value=0, max_value=2, **OPT_NULL)
    top_p = serializers.FloatField(min_value=0, max_value=1, **OPT_NULL) # TODO: Is the min/max range ok?
    user = fields.TrueCharField(**OPT)


# OpenAI chat parameter serializer
# https://platform.openai.com/docs/api-reference/chat/create
class OpenAIParamSerializer(serializers.Serializer):

    # Mandatory model parameters
    messages = serializers.ListField(child=fields.OpenAIMessageField(), allow_empty=False, **MAND)
    model = fields.TrueCharField(allow_blank=False, **MAND)

    # Optional model parameters
    frequency_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    logit_bias = fields.OpenAILogitBiasField(**OPT)
    logprobs = serializers.BooleanField(**OPT_NULL)
    top_logprobs = serializers.IntegerField(min_value=0, max_value=20, **OPT_NULL) #TODO: logsprobs must be True to use it
    max_tokens = serializers.IntegerField(min_value=0, **OPT_NULL) #TODO: check min/max restrictions
    n = serializers.IntegerField(min_value=0, **OPT_NULL) # TODO: is min_value = 0 ok?    
    presence_penalty = serializers.FloatField(min_value=-2, max_value=2, **OPT_NULL)
    response_format = fields.OpenAIResponseFormatField(**OPT)
    seed = serializers.IntegerField(**OPT_NULL) # TODO: min/max?
    service_tier = serializers.ChoiceField(choices=["auto", "default"], **OPT_NULL) # TODO: did I get this right?
    stop = fields.OpenAIStopField(**OPT_NULL) # TODO: Did I get this one right?
    stream = serializers.BooleanField(**OPT_NULL)
    stream_options = fields.OpenAIStreamOptionsField(**OPT_NULL) # TODO: 1) Only if stream==True, 2) Did I get this one right?
    temperature = serializers.FloatField(min_value=0, max_value=2, **OPT_NULL)
    top_p = serializers.FloatField(min_value=0, max_value=1, **OPT_NULL) # TODO: Is the min/max range ok?
    tools = serializers.ListField(child=fields.OpenAIToolSerializer(), max_length=128, **OPT)
    tool_choice = fields.OpenAIToolChoiceField(**OPT)
    parallel_tool_calls = serializers.BooleanField(**OPT)
    user = fields.TrueCharField(**OPT)
