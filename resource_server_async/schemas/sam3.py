from ninja import Schema
from typing import Literal
from pathlib import Path


class Sam3Request(Schema):
    inference_type: Literal["single-image", "batch"]
    data_uri: str
    single_image_prompt: str | None
    weights_dir_override: Path | None = None
