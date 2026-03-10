# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re

# https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/responsible-ai-and-usage-guidelines#safety-filters
VEO_SAFETY_ERROR_CODES = {
    "celebrity": ["29310472", "15236754"],
    "child": ["58061214", "17301594"],
    "dangerous content": ["62263041"],
    "hate": ["57734940", "22137204"],
    "other": ["74803281", "29578790", "42876398"],
    "personal": ["92201652"],
    "prohibited": ["89371032", "49114662", "72817394"],
    "sexual": ["90789179", "63429089", "43188360"],
    "toxic": ["78610348"],
    "safety": ["64151117", "42237218"],
    "violence": ["61493863", "56562880"],
    "vulgar": ["32635315"],
}

USER_FRIENDLY_MESSAGES = {
    "child": "Video generation failed. The prompt or input is not permitted because it may involve children.",
    "celebrity": "Video generation failed. The prompt or input is not permitted because it may involve a celebrity or prominent person.",
    "safety": "Video generation failed due to a general safety violation.",
    "dangerous content": "Video generation failed. The prompt or input was flagged as dangerous content.",
    "hate": "Video generation failed. The prompt or input was flagged as derogatory or hate speech.",
    "other": "Video generation failed due to an unspecified safety policy violation.",
    "personal": "Video generation failed. The prompt or input may contain personal identifiable information (PII).",
    "prohibited": "Video generation failed. The prompt or input requested content that is prohibited by the usage policy.",
    "sexual": "Video generation failed. The prompt or input was flagged for sexual content.",
    "toxic": "Video generation failed. The prompt or input was flagged as toxic.",
    "violence": "Video generation failed. The prompt or input was flagged for violent content.",
    "vulgar": "Video generation failed. The prompt or input was flagged as vulgar.",
}


class ShowableException(Exception):
    """
    Wraps an original exception, allowing a custom, user-facing message to be attached.
    """

    def __init__(self, showable_message: str, original_exception: Exception):
        """
        Initializes the ShowableException with a user-facing message and the original exception.

        Args:
            showable_message: A user-friendly string to be displayed.
            original_exception: The underlying exception that was caught.
        """
        super().__init__(showable_message)
        self.showable_message = showable_message
        self.original_exception = original_exception


def handle_veo_exception(exception_message: str | None = None, exception: Exception | None  = None) -> Exception:
    """
    Checks a Veo API error message for safety codes and raises a
    user-friendly VeoSafetyException if a known code is found.

    :params 
    exception_message: The error message from the Veo API.
    exception: The original exception from the Veo API.
    """

    if not exception_message and not exception:
        raise ValueError("Cannot call `handle_veo_exception` without exception_message nor the original exception")
    
    if not exception_message:
        exception_message = str(exception)
    
    if not exception:
        exception = RuntimeError(exception_message)

    exception_message_lower = exception_message.lower()

    # First, try to find a support code in the error message
    match = re.search(r"support codes: (\d+)", exception_message_lower)
    exception = exception or RuntimeError(exception_message)

    if not match:
        return exception

    found_code = match.group(1)

    # Now, check the code against our inverted dictionary
    # This loop implements your pseudocode logic
    for category, code_list in VEO_SAFETY_ERROR_CODES.items():
        if found_code in code_list:
            # We found a match!
            # Get the user-friendly message for this category
            user_message = USER_FRIENDLY_MESSAGES.get(
                category,
                "Video generation failed due to an unknown safety policy violation.",
            )

            # Raise our new, user-friendly exception
            print("Exception is a known VEO Safety Violation")
            return ShowableException(
                showable_message=user_message,
                original_exception=exception,
            )

    # If the code was found but not in our dictionary
    print("Exception is a not a known VEO Safety Violation")
    return exception
