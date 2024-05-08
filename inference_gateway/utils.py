from typing import List
import re

class BackendUtilsError(Exception):
    pass

# Convert a text field into a list of strings
def textfield_to_strlist(textfield: str) -> List[str]:
    try:
        str_list = re.split(r"[\s;]+", textfield.strip())
        return [s for s in str_list if s]
    except Exception as e:
        raise BackendUtilsError(
            "Could not convert textfield into a list of strings."
        ) from e