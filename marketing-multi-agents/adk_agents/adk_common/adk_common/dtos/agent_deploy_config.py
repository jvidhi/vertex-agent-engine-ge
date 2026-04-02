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

from pydantic import BaseModel

DEPLOYMENT_BUCKET_PREFIX = "deploy"


def sanitize_gcs_bucket_name(name: str) -> str:
    """
    Converts an arbitrary string into a valid GCS bucket name.

    GCS bucket name rules:
    - Must be between 3 and 63 characters.
    - Must contain only lowercase letters, numbers, dashes (-),
      underscores (_), and dots (.).
    - Must start and end with a number or letter.
    - Cannot begin with the 'goog' prefix.
    - Cannot contain 'google' or close misspellings.
    - Cannot be formatted as an IP address.

    This function enforces a stricter, safer subset:
    - Only letters, numbers, and dashes.
    - All other characters (including dots and underscores) are
      replaced with dashes.
    """

    # 1. Convert to lowercase
    s = name.lower()

    # 2. Replace all invalid characters (anything not a-z, 0-9) with a dash
    # This handles spaces, underscores, dots, and special chars.
    s = re.sub(r"[^a-z0-9]", "-", s)

    # 3. Collapse consecutive dashes into a single dash
    s = re.sub(r"-+", "-", s)

    # 4. Remove leading or trailing dashes
    s = s.strip("-")

    # 5. Handle 'goog' prefix
    if s.startswith("goog"):
        s = "g-" + s

    # 6. Enforce minimum length (3) by padding if necessary
    if len(s) < 3:
        # Pad with a generic suffix to meet length
        s = s + "-000"

    # 7. Enforce maximum length (63) by truncating
    s = s[:63]

    # 8. Re-strip trailing dashes, in case truncation created one
    s = s.strip("-")

    return s


class AgentDeployConfig(BaseModel):
    agent: str
    agent_module: str | None = None
    agent_variable: str | None = None
    whl_file_path: str
    agent_description: str
    agent_display_name: str
    deployment_environment: str
    gcs_bucket_deployment_location: str
    agent_engine_id_to_update: str
    google_cloud_reasoning_engine_location: str
    env_vars: dict[str, str]

    @property
    def gcs_bucket_deployment_name(cls) -> str:
        version: str | None = cls.env_vars.get("AGENT_VERSION")
        project_id: str | None = cls.env_vars.get("GOOGLE_CLOUD_PROJECT")

        if not version:
            raise ValueError(
                f"The env_vars in the deployment json is missing AGENT_VERSION. It is required."
            )
        
        if not project_id:
            raise ValueError(
                f"The env_vars in the deployment json is missing GOOGLE_CLOUD_PROJECT. It is required."
            )
        sanitized_bucket_name = sanitize_gcs_bucket_name(
            f"{DEPLOYMENT_BUCKET_PREFIX}-{cls.agent}-{project_id}-{version}-{cls.deployment_environment}"
        )
        
        return sanitized_bucket_name
