from pydantic import BaseModel, Field, AfterValidator, model_validator
from typing import List, Optional, Union, Dict, Annotated, Any, Literal
from enum import Enum

# Extra validation for the metadata field
def metadata_validator(value: dict) -> dict:
    if len (value) > 16:
        raise ValueError("'metadata' must have at most 16 key-value pairs.")
    if any(len(k) > 64 for k in value.keys()):
        raise ValueError("all 'metadata' keys must be at most 64 characters.")
    if any(len(v) > 512 for v in value.values()):
        raise ValueError("all 'metadata' values must be at most 512 characters.")
    return value

# ================================
#  Enum classes for fixed options
# ================================

# Modalities
class Modalities(str, Enum):
    text = "text"
    audio = "audio"

# Reasoning_effort
class ReasoningEffort(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

# Prediction - type
class PredictionType(str, Enum):
    content = "content"

# Prediction - content - type
class PredictionContentType(str, Enum):
    text = "text"

# Response_format - text - type
class TextType(str, Enum):
    text = "text"

# Response_format - json_schema - type
class JsonSchemaType(str, Enum):
    json_schema = "json_schema"

# Response_format - json_object - type
class JsonObjectType(str, Enum):
    json_object = "json_object"

# Response_format - type
class ResponseFormatType(str, Enum):
    text = TextType.text.value
    json_schema = JsonSchemaType.json_schema.value
    json_object = JsonObjectType.json_object.value

# Service_tier
class ServiceTier(str, Enum):
    auto = "auto"
    default = "default"

# Tool_choice - type
class ToolChoiceType(str, Enum):
    function = "function"

# Tool - type
class ToolType(str, Enum):
    function = "function"

# Web_search_options - search_context_size
class WebSearchOptionsSearchContextSize(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

# Web_search_options - user_location - type
class WebSearchOptionsUserLocationType(str, Enum):
    approximate = "approximate"

# Developer_message - role
class DeveloperMessageRole(str, Enum):
    developer = "developer"

# System_message - role
class SystemMessageRole(str, Enum):
    system = "system"

# User_message - role
class UserMessageRole(str, Enum):
    user = "user"

# Assistant_message - role
class AssistantMessageRole(str, Enum):
    assistant = "assistant"

# Tool_message - role
class ToolMessageRole(str, Enum):
    tool = "tool"

# Message - role
class MessageRole(str, Enum):
    developer = DeveloperMessageRole.developer.value
    system = SystemMessageRole.system.value
    user = UserMessageRole.user.value
    assistant = AssistantMessageRole.assistant.value
    tool = ToolMessageRole.tool.value

# User_message - content - text_content - type
class TextContentType(str, Enum):
    text = "text"

# User_message - content - image_content - type
class ImageContentType(str, Enum):
    image_url = "image_url"

# User_message - content - image_content - image_url - detail
class ImageURLDetail(str, Enum):
    low = "low"
    high = "high"
    auto = "auto"

# User_message - content - audio_content - type
class AudioContentType(str, Enum):
    input_audio = "input_audio"

# User_message - content - audio_content - input_audio - format
class InputAudioFormat(str, Enum):
    wav = "wav"
    mp3 = "mp3"

# User_message - content - file_content - type
class FileContentType(str, Enum):
    file = "file"

# Assistant_message - content - refusal - type
class RefusalContentType(str, Enum):
    refusal = "refusal"

# Assistant_message - tool_calls - type
class ToolCallsType(str, Enum):
    function = "function"

# User Message - content
class UserContentType(str, Enum):
    text = TextContentType.text.value
    image_url = ImageContentType.image_url.value
    input_audio = AudioContentType.input_audio.value
    file = FileContentType.file.value

# Assistant Message - content
class AssistantContentType(str, Enum):
    text = TextContentType.text.value
    refusal = RefusalContentType.refusal.value

# Tool Choice
class ToolChoice(str, Enum):
    none = "none"
    auto = "auto"
    required = "required"

# ========================
#  Pydantic utils classes 
# ========================

# Extention of the Pydantic BaseModel that prevent extra attributes
class BaseModelExtraForbid(BaseModel):
    class Config:
        extra = 'forbid'

# vLLM extra_body field
class ExtraBody(BaseModelExtraForbid):
    use_beam_search: bool

# Prediction - content
# TODO: Do more vetting on what is allowed (e.g. text)
class PredictionContent(BaseModelExtraForbid):
    text: str
    type: PredictionContentType

# Prediction
class Prediction(BaseModelExtraForbid):
    content: Union[str, List[PredictionContent]]
    type: PredictionType

# Response_format - text
class ResponseFormatText(BaseModelExtraForbid):
    type: TextType

# Response_format - json_schema - json_schema
class ResponseFormatJsonSchemaJsonSchema(BaseModelExtraForbid):
    name: str = Field(..., max_length=64)
    description: Optional[str] = Field(default=None)
    schema: Optional[dict] = Field(default_factory=dict)
    strict: Optional[bool] = Field(default=False)

# Response_format - json_schema
class ResponseFormatJsonSchema(BaseModelExtraForbid):
    type: JsonSchemaType
    json_schema: ResponseFormatJsonSchemaJsonSchema

# Response_format - json_object
class ResponseFormatJsonObject(BaseModelExtraForbid):
    type: JsonObjectType

# Response_format
class ResponseFormat(BaseModelExtraForbid):
    type: ResponseFormatType
    json_schema: Optional[dict] = Field(default={})
    
    # Providing more human-readable (simpler) error messages
    @model_validator(mode='before')
    def set_dynamic_content_type(cls, values):

        # Check if type was provided
        if "type" not in values:
            raise ValueError(f"'type' must be provided.")

        # Validate the input type
        response_type = values.get('type')
        valid_types = [o.value for o in ResponseFormatType]
        if not response_type in valid_types:
            raise ValueError(f"'type' must be one of {valid_types}.")
        
        # Define the validation class options
        pydantic_class = {
            TextType.text.value: ResponseFormatText,
            JsonSchemaType.json_schema.value: ResponseFormatJsonSchema,
            JsonObjectType.json_object.value: ResponseFormatJsonObject
        }

        # Validate inputs
        _ = pydantic_class[response_type](**values)
        
        # Return values if nothing wrong happened in the valudation step
        return values
    
# Tool_choice - function
class ToolChoiceFunction(BaseModelExtraForbid):
    name: str

# Tool_choice 
class ToolChoiceObject(BaseModelExtraForbid):
    function: ToolChoiceFunction
    type: ToolChoiceType

# Tool - function 
class ToolFunction(BaseModelExtraForbid):
    name: str = Field(..., max_length=64)
    description: Optional[str] = Field(default=None)
    parameters: Optional[dict] = Field(default=None)
    strict: Optional[bool] = Field(default=False)

    # Extra validations
    @model_validator(mode='after')
    def extra_validations(cls, values):

        # Check if name includes weird characters
        test_data = values.name.replace("-","").replace("_","")
        if not test_data.isalnum():
            raise ValueError("'Tolls-function-name' must Must be a-z, A-Z, 0-9, or contain underscores and dashes.")

# Tool
class Tool(BaseModelExtraForbid):
    function: ToolFunction
    type: ToolType

# Web_search_options - user_location - approximate
class WebSearchOptionsUserLocationApproximate(BaseModelExtraForbid):
    city: Optional[str] = Field(default=None)
    country: Optional[str] = Field(default=None)
    region: Optional[str] = Field(default=None)
    timezone: Optional[str] = Field(default=None)

# Web_search_options - user_location
class WebSearchOptionsUserLocation(BaseModelExtraForbid):
    approximate: WebSearchOptionsUserLocationApproximate
    type: WebSearchOptionsUserLocationType

# Web_search_options
class WebSearchOptions(BaseModelExtraForbid):
    search_context_size: Optional[WebSearchOptionsSearchContextSize] = Field(default=WebSearchOptionsSearchContextSize.medium.value)
    user_location: Optional[WebSearchOptionsUserLocation] = Field(default=None)

# User_message - content - text_content
class MessageTextContent(BaseModelExtraForbid):
    text: str
    type: TextContentType

# User_message - content - image_content - image_url
class MessageImageURL(BaseModelExtraForbid):
    url: str
    detail: Optional[ImageURLDetail] = Field(default=ImageURLDetail.auto.value)

# User_message - content - image_content
class MessageImageContent(BaseModelExtraForbid):
    image_url: MessageImageURL
    type: ImageContentType

# User_message - content - audio_content - input_audio
class MessageInputAudio(BaseModelExtraForbid):
    data: str
    format: InputAudioFormat
    
# User_message - content - audio_ccontent 
class MessageAudioContent(BaseModelExtraForbid):
    input_audio: MessageInputAudio
    type: AudioContentType

# User_message - content - file_content - file
class MessageFile(BaseModelExtraForbid):
    file_data: Optional[str] = Field(default=None)
    file_id: Optional[str] = Field(default=None)
    filename: Optional[str] = Field(default=None)

# User_message - content - file_content
class MessageFileContent(BaseModelExtraForbid):
    file: MessageFile
    type: FileContentType

# Assistant_message - content - refusal_content 
class MessageRefusalContent(BaseModelExtraForbid):
    refusal: str
    type: RefusalContentType

# Assistant_message - audio
class AssistantMessageAudio(BaseModelExtraForbid):
    id: str

# Assistant_message - tool_calls - function
class ToolCallsFunction(BaseModelExtraForbid):
    arguments: str
    name: str

# Assistant_message - tool_calls
class AssistantMessageToolCalls(BaseModelExtraForbid):
    function: ToolCallsFunction
    id: str
    type: ToolCallsType

# Developer_message
class DeveloperMessage(BaseModelExtraForbid):
    content: Union[str, List[str]]
    role: DeveloperMessageRole
    name: Optional[str] = Field(default=None)

# System_message
class SystemMessage(BaseModelExtraForbid):
    content: Union[str, List[str]]
    role: SystemMessageRole
    name: Optional[str] = Field(default=None)

# User message - content - object (general class that will re-route the validation according to the targeted content type)
class UserMessageContent(BaseModelExtraForbid):
    type: str
    text: Optional[Any] = Field(default=None)
    image_url: Optional[Any] = Field(default=None)
    input_audio: Optional[Any] = Field(default=None)
    file: Optional[Any] = Field(default=None)

    # Providing more human-readable (simpler) error messages
    @model_validator(mode='before')
    def set_dynamic_content_type(cls, values):

        # Check if type was provided
        if "type" not in values:
            raise ValueError(f"'type' must be provided in each user message content item.")

        # Validate the input type
        input_type = values.get('type')
        valid_types = [o.value for o in UserContentType]
        if not input_type in valid_types:
            raise ValueError(f"'messages-user-content-type' must be one of {valid_types}.")
        
        # Define the validation class options
        pydantic_class = {
            TextContentType.text.value: MessageTextContent,
            ImageContentType.image_url.value: MessageImageContent,
            AudioContentType.input_audio.value: MessageAudioContent,
            FileContentType.file.value: MessageFileContent
        }

        # Validate inputs
        _ = pydantic_class[input_type](**values)
        
        # Return values if nothing wrong happened in the valudation step
        return values
    
# User_message
class UserMessage(BaseModelExtraForbid):
    #content: Union[str, List[Union[MessageTextContent, MessageImageContent, MessageAudioContent, MessageFileContent]]]
    content: Union[str, List[UserMessageContent]]
    role: UserMessageRole
    name: Optional[str] = Field(default=None)

# Assistant message - content 
class AssistantMessageContent(BaseModelExtraForbid):
    type: str
    text: Optional[Any] = Field(default=None)
    refusal: Optional[Any] = Field(default=None)

    # Providing more human-readable (simpler) error messages
    @model_validator(mode='before')
    def set_dynamic_content_type(cls, values):

        # Check if type was provided
        if "type" not in values:
            raise ValueError(f"'type' must be provided in each assistant message content item.")

        # Validate the input type
        input_type = values.get('type')
        valid_types = [o.value for o in AssistantContentType]
        if not input_type in valid_types:
            raise ValueError(f"'messages-assistant-content-type' must be one of {valid_types}.")
        
        # Define the validation class options
        pydantic_class = {
            TextContentType.text.value: MessageTextContent,
            RefusalContentType.refusal.value: MessageRefusalContent
        }

        # Validate inputs
        _ = pydantic_class[input_type](**values)
        
        # Return values if nothing wrong happened in the valudation step
        return values

# Assistant_message
class AssistantMessage(BaseModelExtraForbid):
    role: AssistantMessageRole
    audio: Optional[AssistantMessageAudio] = Field(default=None)
    #content: Optional[Union[str, List[Union[MessageTextContent, MessageRefusalContent]]]] = Field(default=None)
    content: Optional[Union[str, List[AssistantMessageContent]]] = Field(default=None)
    name: Optional[str] = Field(default=None)
    refusal: Optional[str] = Field(default=None)
    tool_calls: Optional[List[AssistantMessageToolCalls]] = Field(default=None)

# Tool message
class ToolMessage(BaseModelExtraForbid):
    content: Union[str, List[str]]
    role: ToolMessageRole
    tool_call_id: str

# Message (general class that will re-route the validation according to the targeted role)
class Message(BaseModelExtraForbid):
    role: str
    content: Optional[Any] = Field(default=None)
    name: Optional[Any] = Field(default=None)
    audio: Optional[Any] = Field(default=None)
    refusal: Optional[Any] = Field(default=None)
    tool_calls: Optional[Any] = Field(default=None)
    tool_call_id: Optional[Any] = Field(default=None)

    # Providing more human-readable (simpler) error messages
    @model_validator(mode='before')
    def set_dynamic_message_role(cls, values):

        # Check if role was provided
        if "role" not in values:
            raise ValueError(f"'role' must be provided in each message object.")

        # Validate the input role
        input_role = values.get('role')
        valid_roles = [o.value for o in MessageRole]
        if not input_role in valid_roles:
            raise ValueError(f"'messages-role' must be one of {valid_roles}.")
        
        # Define the validation class options
        pydantic_class = {
            DeveloperMessageRole.developer.value: DeveloperMessage,
            SystemMessageRole.system.value: SystemMessage,
            UserMessageRole.user.value: UserMessage,
            AssistantMessageRole.assistant.value: AssistantMessage,
            ToolMessageRole.tool.value: ToolMessage
        }

        # Validate inputs
        _ = pydantic_class[input_role](**values)
        
        # Return values if nothing wrong happened in the valudation step
        return values

# OpenAI chat completions
# https://platform..com/docs/api-reference/chat/create
class OpenAIChatCompletionsPydantic(BaseModelExtraForbid):

    #messages: List[Union[
    #    DeveloperMessage,
    #    SystemMessage,
    #    UserMessage,
    #    AssistantMessage,
    #    ToolMessage]
    #]
    messages: List[Message]
    model: str = Field(..., min_length=1)
    frequency_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    logit_bias: Optional[Dict[str, float]] = Field(default=None)
    logprobs: Optional[bool] = Field(default=False)
    max_completion_tokens: Optional[int] = Field(default=None, ge=0)
    max_tokens: Optional[int] = Field(default=None, ge=0)
    metadata: Optional[Annotated[Dict[str, str], AfterValidator(metadata_validator)]] = Field(default_factory=dict)
    modalities: Optional[List[Modalities]] = Field(default=None, min_items=1, max_items=2)
    n: Optional[int] = Field(default=1, ge=1, le=128)
    parallel_tool_calls: Optional[bool] = Field(default=True)
    prediction: Optional[Prediction] = Field(default_factory=dict)
    presence_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    reasoning_effort: Optional[ReasoningEffort] = Field(default=ReasoningEffort.medium.value)
    #response_format: Optional[Union[
    #    ResponseFormatText,
    #    ResponseFormatJsonSchema,
    #    ResponseFormatJsonObject]
    #] = Field(default_factory=dict)
    response_format: Optional[ResponseFormat] = Field(default_factory=dict)
    seed: Optional[int] = Field(default=None, ge=-9223372036854775808, le=9223372036854775807)
    service_tier: Optional[ServiceTier] = Field(default=ServiceTier.auto.value)
    stop: Optional[Union[str, List[str]]] = Field(default=None)
    stream: Optional[bool] = Field(default=False)
    store: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=1, ge=0, le=2)
    tool_choice: Optional[Union[ToolChoice, ToolChoiceObject]] = Field(default=None)
    tools: Optional[List[Tool]] = Field(default=[], max_length=128)
    top_logprobs: Optional[int] = Field(default=None, ge=0, le=20)
    top_p: Optional[float] = Field(default=1, ge=0, le=1)
    user: Optional[str] = Field(default=None)
    web_search_options: Optional[WebSearchOptions] = Field(default_factory=dict)

    # vLLM extra options relative to OpenAI
    extra_body: Optional[ExtraBody] = Field(default=None)  

    # Extra validations
    @model_validator(mode='after')
    def extra_validations(cls, values):

        # Check if logsprobs is set to True when top_logprobs is used
        if isinstance(values.top_logprobs, int):
            if values.logprobs == False:
                raise ValueError("'logprobs' must be set to True when 'top_logprobs' is used.")
            
        # Validate logit_bias bias values
        if isinstance(values.logit_bias, dict):
            for bias in values.logit_bias.values():
                if bias < -100 or bias > 100:
                    raise ValueError("'logit_bias' bias values must be from -100 to 100.")
                
        # Validate stop list
        if isinstance(values.stop, list):
            if len(values.stop) < 1 or len(values.stop) > 4:
                raise ValueError("'stop' list must have between 1 to 4 items.")
            
        # Raise error if stream == True, since we do not have the capability yet
        if isinstance(values.stream, bool):
            if values.stream == True:
                raise ValueError("'stream' is currently not available and the value must be set to False.")

        # Return values if nothing wrong happened in the valudation step
        return values