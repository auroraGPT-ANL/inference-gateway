from rest_framework import serializers
from rest_framework.exceptions import ValidationError


# Reusable serializer base class
# Raises error if extra/unexpected parameters are passed in the payload
# TODO: This does not always prevent extra arguments
class BaseSerializers(serializers.Serializer):
    def is_valid(self, raise_exception=False):
        if hasattr(self, 'initial_data'):
            payload_fields = self.initial_data.keys()
            serializer_fields = self.fields.keys()
            extra_fields = set(payload_fields) - set(serializer_fields)
            if len(extra_fields) > 0:
                if raise_exception:
                    raise ValidationError(f"Unexpected input field(s) ({str(extra_fields)})")
                else:
                    return False
        return super(BaseSerializers, self).is_valid(raise_exception=raise_exception)


# True string field that rejects non-string inputs
class TrueCharField(serializers.CharField):

    # Overwrite to_representation function with no edits to the value
    def to_representation(self, value):
        return value
    
    # Overwrite to_internal_value to raise errors if the data has the wrong types
    def to_internal_value(self, data):
        if isinstance(data, str):
            return data
        else:
            raise ValidationError("Value not a string.") # TODO: Should make sure to give more details to users


# Reusable custom serializer field with multiple allowed types
class BaseCustomField(serializers.Field):

    # Overwrite to_representation function with no edits to the value
    def to_representation(self, value):
        return value
    
    # Overwrite to_internal_value to raise errors if the data has the wrong types
    def to_internal_value(self, data):
        if self.has_valid_types(data):
            return data
        else:
            raise ValidationError(self.custom_error_message)


# OpenAI Legacy prompt field
class OpenAIPromptField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIPromptField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'prompt' must be a string, a list of strings, a list of tokens (int), or a list of token lists."

    # Check if the data has a valid type
    def has_valid_types(self, data):

        # Single string
        if isinstance(data, str):
            return True
        
        # List
        if isinstance(data, list):

            # List of strings
            if all(isinstance(x, str) for x in data):
                return True
            
            # List of integers (tokens)
            if all(isinstance(x, int) for x in data):
                return True
            
            # List of integer lists (list of tokens)
            if all(isinstance(x, list) for x in data):
                for data_list in data:
                    if not all(isinstance(x, int) for x in data_list):
                        return False # wrong format
                return True
        
        # Wrong format
        return False


# OpenAI logit_bias field
class OpenAILogitBiasField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAILogitBiasField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'logit_bias' must a dictionary in the form of {'50256': -100}, with values between -100 and 100."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if isinstance(data, dict):
            if all(isinstance(x, str) for x in data.keys()):
                values = data.values()
                if all(isinstance(x, (float,int)) for x in values):
                    if (-100) <= min(values) and max(values) <= 100:
                        return True
        return False
    

# OpenAI stop field
class OpenAIStopField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIStopField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'stop' must a string or a list of strings with no more than 4 sequences."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if isinstance(data, str):
            return True
        if isinstance(data, list):
            if all(isinstance(x, str) for x in data) and len(data) <= 4:
                return True
        return False
    

# OpenAI stream_options field
class OpenAIStreamOptionsField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIStreamOptionsField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'stream_options' must be a dictionary with {'include_usage': True or False}"

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if data == {"include_usage": True} or data == {"include_usage": False}:
            return True
        return False
    

# OpenAI response_format field
class OpenAIResponseFormatField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIResponseFormatField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'response_format' must either be {'type': 'text'} or {'type': 'json_object'}"

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if data == {"type": "text"} or data == {"type": "json_object"}:
            return True
        return False


# OpenAI image URL serializer (needed for OpenAIUserContentField)
class OpenAIImageURLSerializer(BaseSerializers):
    url = TrueCharField(required=True)
    detail = serializers.ChoiceField(choices=["auto", "high", "low"], required=False)


# OpenAI image serializer (needed for OpenAIUserContentField)
class OpenAIImageSerializer(BaseSerializers):
    type = serializers.ChoiceField(choices=["image_url"], required=True)
    image_url = OpenAIImageURLSerializer(required=True)


# OpenAI text serializer (needed for OpenAIUserContentField)
class OpenAITextSerializer(BaseSerializers):
    type = serializers.ChoiceField(choices=["text"], required=True)
    text = TrueCharField(required=True)


# OpenAI user content field
class OpenAIUserContentField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIUserContentField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'content' with user role must be a string or an Array content."

    # Check if the data has a valid type
    def has_valid_types(self, data):

        # Simple string
        if isinstance(data, str):
            return True
        
        # List of content parts
        if isinstance(data, list):
            for content in data:
                if "type" not in content:
                    raise ValidationError("'type' must be present for each Array content part of a user message content.")
                else:

                    # Text content (raises exception if something is wrong)
                    if content["type"] == "text":
                        text_serializer = OpenAITextSerializer(data=content)
                        text_serializer.is_valid(raise_exception=True)
                    
                    # Image URL content (raises exception if something is wrong)
                    elif content["type"] == "image_url":
                        image_url_serializer = OpenAIImageSerializer(data=content)
                        image_url_serializer.is_valid(raise_exception=True)
                        
                    # Wrong type
                    else:
                        raise ValidationError("'type' can either be equal to 'text' or 'image_url'.")
            
            # Return True if nothing wrong was spotted in the list of content
            return True
                
        # Wrong format
        return False


# OpenAI function name field
class OpenAIFunctionNameField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIFunctionNameField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'name' must be a string (max length of 64) and can only include a-z, A-Z, 0-9, underscores, and dashes."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if isinstance(data, str):
            if len(data) <= 64:
                test_data = data.replace("-","").replace("_","")
                if test_data.isalnum():
                    return True
        return False


# OpenAI tool function serializer (needed for OpenAIParamSerializer)
class OpenAIToolFunctionSerializer(BaseSerializers):
    description = TrueCharField(required=False)
    name = OpenAIFunctionNameField(required=True)
    parameters = serializers.DictField(required=False) # TODO: Should do checks for what goes inside


# OpenAI tool serializer (needed for OpenAIParamSerializer)
class OpenAIToolSerializer(BaseSerializers):
    type = serializers.ChoiceField(choices=["function"], required=True)
    function = OpenAIToolFunctionSerializer(required=True)


# OpenAI tool choice object function serializer (needed for OpenAIToolChoiceField)
class OpenAIToolChoiceObjectFunctionSerializer(BaseSerializers):
    name = TrueCharField(required=True)


# OpenAI tool choice object serializer (needed for OpenAIToolChoiceField)
class OpenAIToolChoiceObjectSerializer(BaseSerializers):
    type = serializers.ChoiceField(choices=["function"], required=True)
    function = OpenAIToolChoiceObjectFunctionSerializer(required=True)


# OpenAI tool choicefield
class OpenAIToolChoiceField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIToolChoiceField, self).__init__(*args, **kwargs)
        form = "{ 'type': 'function', 'function': {'name': 'my_function'} }"
        self.choices = ["none", "auto", "required"]
        self.custom_error_message = f"'tool_choice' must be a string ({self.choices}) or a dictionary in the form of {form}."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if isinstance(data, str):
            if data in self.choices:
                return True
        if isinstance(data, dict):
            serializer = OpenAIToolChoiceObjectSerializer(data=data)
            if serializer.is_valid(raise_exception=True):
                return True
        return False


# OpenAI tool call function serializer (needed for OpenAIToolCallSerializer)
class OpenAIToolCallFunctionSerializer(BaseSerializers):
    name = TrueCharField(required=True)
    arguments = TrueCharField(required=True)


# OpenAI tool call serializer (needed for OpenAIAssistantMessageSerializer)
class OpenAIToolCallSerializer(BaseSerializers):
    id = TrueCharField(required=True)
    type = serializers.ChoiceField(choices=["function"], required=True)
    function = OpenAIToolCallFunctionSerializer(required=True)


# OpenAI system-role message serializer (needed for OpenAIMessageField)
class OpenAISystemMessageSerializer(BaseSerializers):
    content = TrueCharField(required=True)
    role = serializers.ChoiceField(choices=["system"], required=True)
    name = TrueCharField(required=False)


# OpenAI user-role message serializer (needed for OpenAIMessageField)
class OpenAIUserMessageSerializer(BaseSerializers):
    content = OpenAIUserContentField(required=True)
    role = serializers.ChoiceField(choices=["user"], required=True)
    name = TrueCharField(required=False)


# OpenAI assistant-role message serializer (needed for OpenAIMessageField)
class OpenAIAssistantMessageSerializer(BaseSerializers):
    content = TrueCharField(allow_null=True, required=True) # TODO: not required if tool_calls is specified
    role = serializers.ChoiceField(choices=["assistant"], required=True)
    name = TrueCharField(required=False)
    tool_calls = serializers.ListField(child=OpenAIToolCallSerializer(), required=False)


# OpenAI tool-role message serializer (needed for OpenAIMessageField)
class OpenAIToolMessageSerializer(BaseSerializers):
    content = TrueCharField(required=True)
    role = serializers.ChoiceField(choices=["tool"], required=True)
    tool_call_id = TrueCharField(required=True)


# OpenAI function-role message serializer (needed for OpenAIMessageField)
class OpenAIFunctionMessageSerializer(BaseSerializers):
    content = TrueCharField(allow_null=True, required=True)
    role = serializers.ChoiceField(choices=["function"], required=True)
    name = TrueCharField(required=True)


# OpenAI message field
class OpenAIMessageField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIMessageField, self).__init__(*args, **kwargs)
        self.serializer_choices = {
            "system": OpenAISystemMessageSerializer,
            "user": OpenAIUserMessageSerializer,
            "assistant": OpenAIAssistantMessageSerializer,
            "tool": OpenAIToolMessageSerializer,
            "function": OpenAIFunctionMessageSerializer
        }
        self.custom_error_message = f"Message object must have one of the following 'roles': {self.serializer_choices.keys()}."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if "role" in data:
            if data["role"] in self.serializer_choices:
                serializer = self.serializer_choices[data["role"]](data=data)
                if serializer.is_valid(raise_exception=True):
                    return True
        return False
    

# OpenAI embeddings input field
class OpenAIEmbeddingsInputField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIEmbeddingsInputField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'input' must be string, array of strings, array of tokens, or array of token arrays."
        self.min_items = 1
        self.max_items = 2048

    # Check if the data has a valid type
    def has_valid_types(self, data):

        # Single string
        if isinstance(data, str):
            return True
        
        # List
        if isinstance(data, list):

            # Check length
            if len(data) < self.min_items or len(data) > self.max_items:
                raise ValidationError(f"Length of 'input' lists must be between {self.min_items} and {self.max_items}, inclusively.")

            # List of strings
            if all(isinstance(x, str) for x in data):
                return True
            
            # List of integers (tokens)
            if all(isinstance(x, int) for x in data):
                return True
            
            # List of integer lists (list of tokens)
            if all(isinstance(x, list) for x in data):
                for data_list in data:
                    if not all(isinstance(x, int) for x in data_list):
                        return False # wrong format
                return True
        
        # Wrong format
        return False


# OpenAI content part serializer (needed for the content field of the chat completions prediction field)
class OpenAIContentPartSerializer(BaseSerializers):
    text = TrueCharField(required=True)
    type = TrueCharField(required=True)


# OpenAI content object (needed for the content field of the chat completions prediction field)
class OpenAIContentObjectField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIContentObjectField, self).__init__(*args, **kwargs)
        form = "{ 'text': 'string', 'type': 'string' }"
        self.custom_error_message = f"'content' must be a string or an array of dictionaries in the form of {form}."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if isinstance(data, str):
            return True
        if isinstance(data, list):
            if len(data) == 0:
                return False
            for item in data:
                if not isinstance(item, dict):
                    return False
                serializer = OpenAIContentPartSerializer(data=item)
                if not serializer.is_valid(raise_exception=True):
                    return False
            return True
        return False


# OpenAI static content serializer (needed for chat completions prediction field)
class OpenAIStaticContentSerializer(BaseSerializers):
    content = OpenAIContentObjectField(required=True)
    type = serializers.ChoiceField(choices=["content"], required=True)


# OpenAI user location serializer (needed for chat completions web search options serializer)
class OpenAIApproximateSerializer(BaseSerializers):
    city = TrueCharField(required=False)
    country = TrueCharField(required=False)
    region = TrueCharField(required=False)
    timezone = TrueCharField(required=False)


# OpenAI user location serializer (needed for chat completions web search options serializer)
class OpenAIUserLocationSerializer(BaseSerializers):
    approximate = OpenAIApproximateSerializer(required=True)
    type = serializers.ChoiceField(choices=["approximate"], required=True)


# OpenAI web search options serializer (needed for chat completions web_search_options field)
class OpenAIWebSearchOptionsSerializer(BaseSerializers):
    search_context_size = serializers.ChoiceField(choices=["low", "medium", "high"], required=False)
    user_location = OpenAIUserLocationSerializer(required=False, allow_null=True)


# OpenAI modalities field (needed for chat completions fields)
class OpenAIModalitiesField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIModalitiesField, self).__init__(*args, **kwargs)
        self.allowed = [["text"], ["text", "audio"]]
        self.custom_error_message = f"Must be one of the following arrays: {self.allowed}."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if isinstance(data, list):
            if len(data) == 0:
                return False
            if not data in self.allowed:
                return False
            return True
        return False


# OpenAI metadata field (needed for chat completions fields)
class OpenAIMetaDataField(BaseCustomField):

    # Add to the existing initialization
    def __init__(self, *args, **kwargs):
        super(OpenAIMetaDataField, self).__init__(*args, **kwargs)
        self.custom_error_message = "'metadata' must a dictionary with a maximum of 16 key-value pairs. Keys are strings with a maximum length of 64 characters. Values are strings with a maximum length of 512 characters."

    # Check if the data has a valid type
    def has_valid_types(self, data):
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(key, str) and isinstance(val, str):
                    if len(key) > 64 or len(val) > 512:
                        return False
                else:
                    return False
            return True
        else:
            return False