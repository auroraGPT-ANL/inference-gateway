from rest_framework import serializers
from utils import fields

# TODO: overwrite is_valid or validate function to raise errors when passing extra parameters

# ===========================
# [Legacy] OpenAI Completions (following https://platform.openai.com/docs/api-reference/completions/create)
# ===========================

# [Legacy] OpenAI Legacy parameter serializer
class OpenAILegacyParamSerializer(serializers.Serializer):

    # Mandatory model parameters
    kwargs = {"required":True}
    model = fields.TrueCharField(allow_blank=False, **kwargs) #TODO: Provide validation on choices (ChoiceField)
    prompt = fields.OpenAIPromptField(**kwargs)

    # Optional model parameters
    kwargs = {"required": False}
    kwargs_null = {"required": False, "allow_null": True}
    best_of = serializers.IntegerField(**kwargs_null) #TODO: 1) dependent on n, 2) needs limits min max?
    echo = serializers.BooleanField(**kwargs_null)
    frequency_penalty = serializers.FloatField(min_value=-2, max_value=2, **kwargs_null)
    logit_bias = fields.OpenAILogitBiasField(**kwargs)
    logprobs = serializers.IntegerField(min_value=0, max_value=5, **kwargs_null)
    max_tokens = serializers.IntegerField(min_value=1, **kwargs_null) # TODO: is min_value = 1 ok?
    n = serializers.IntegerField(min_value=1, **kwargs_null) # TODO: is min_value = 1 ok?
    presence_penalty = serializers.FloatField(min_value=-2, max_value=2, **kwargs_null)
    seed = serializers.IntegerField(**kwargs_null) # TODO: Any constraints on this value?
    stop = fields.OpenAIStopField(**kwargs_null) # TODO: Did I get this one right?
    stream = serializers.BooleanField(**kwargs_null)
    stream_options = fields.OpenAIStreamOptionsField(**kwargs_null) # TODO: 1) Only if stream==True, 2) Did I get this one right?
    suffix = fields.TrueCharField(max_length="256", **kwargs_null) #TODO: only supported for gpt-3.5-turbo-instruct
    temperature = serializers.FloatField(min_value=0, max_value=2, **kwargs_null)
    top_p = serializers.FloatField(min_value=0, max_value=1, **kwargs_null) # TODO: Is the min/max range ok?
    user = fields.TrueCharField(max_length=256, **kwargs)


# =======================
# OpenAI Chat/Completions (following https://platform.openai.com/docs/api-reference/chat/create)
# =======================


# OpenAI tool function serializer (needed for OpenAIParamSerializer)
class OpenAIToolFunctionSerializer(serializers.Serializer):
    description = fields.TrueCharField(required=False)
    name = fields.OpenAIFunctionNameField(required=True)
    parameters = serializers.DictField(required=False) # TODO: Should do checks for what goes inside


# OpenAI tool serializer (needed for OpenAIParamSerializer)
class OpenAIToolSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["function"], required=True)
    function = OpenAIToolFunctionSerializer(required=True)

    
# OpenAI base parameter Serializer
class OpenAIParamSerializer(serializers.Serializer):

    # Mandatory model parameters
    kwargs = {"required": True}
    messages = serializers.ListField(child=fields.OpenAIMessageField(), allow_empty=False, **kwargs)
    model = fields.TrueCharField(max_length="256", allow_blank=False, **kwargs)

    # Optional model parameters
    kwargs = {"required": False}
    kwargs_null = {"required": False, "allow_null": True}
    frequency_penalty = serializers.FloatField(min_value=-2, max_value=2, **kwargs_null)
    logit_bias = fields.OpenAILogitBiasField(**kwargs)
    logprobs = serializers.BooleanField(**kwargs_null)
    top_logprobs = serializers.IntegerField(min_value=0, max_value=20, **kwargs_null) #TODO: logsprobs must be True to use it
    max_tokens = serializers.IntegerField(min_value=0, **kwargs_null) #TODO: check min/max restrictions
    n = serializers.IntegerField(min_value=1, **kwargs_null) # TODO: is min_value = 1 ok?    
    presence_penalty = serializers.FloatField(min_value=-2, max_value=2, **kwargs_null)
    response_format = fields.OpenAIResponseFormatField(**kwargs)
    seed = serializers.IntegerField(**kwargs_null) # TODO: min/max?
    service_tier = serializers.ChoiceField(choices=["auto", "default"], **kwargs_null) # TODO: did I get this right?
    stop = fields.OpenAIStopField(**kwargs_null) # TODO: Did I get this one right?
    stream = serializers.BooleanField(**kwargs_null)
    stream_options = fields.OpenAIStreamOptionsField(**kwargs_null) # TODO: 1) Only if stream==True, 2) Did I get this one right?
    temperature = serializers.FloatField(min_value=0, max_value=2, **kwargs_null)
    top_p = serializers.FloatField(min_value=0, max_value=1, **kwargs_null) # TODO: Is the min/max range ok?
    tools = serializers.ListField(child=OpenAIToolSerializer(), max_length=128, **kwargs)
    tool_choice = fields.OpenAIToolChoiceField(**kwargs)
    parallel_tool_calls = serializers.BooleanField(**kwargs)
    user = fields.TrueCharField(max_length=256, **kwargs)




