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
import os
from google.cloud import bigquery
from google.api_core import exceptions as api_exceptions
from google.cloud import bigquery
from adk_common.utils.constants import get_required_env_var, get_optional_env_var
from adk_common.utils.utils_logging import Severity, log_message, log_function_call

# @log_function_call
def retrieve_product_uri_from_bq(product_name: str) -> str | None:
    """
    Searches for a product in BigQuery by its name within the search_tags array.

    Args:
        product_name (str): The name of the product to search for.

    Returns:
        A dictionary representing the matched product row, or None if no match is found.
    """
    # TODO: Replace with your project ID, dataset ID, and table ID.
    project_id = get_required_env_var("GOOGLE_CLOUD_PROJECT")
    dataset_id = get_optional_env_var("BQ_DATASET", "UNKNOWN")
    table_id = get_optional_env_var("BQ_TABLE", "UNKNOWN")

    if dataset_id == "UNKNOWN" or table_id == "UNKNOWN":
        log_message("ERROR: BQ_DATASET or BQ_TABLE environment variable is not set.", Severity.ERROR)
        return None

    client = bigquery.Client()
    table_ref = client.dataset(dataset_id).table(table_id)

    # Normalize the product name for consistent searching.
    normalized_product_name = product_name.lower().strip()

    # Construct the query to search for the product name in the search_tags array.
    query = f"""
        SELECT *
        FROM `{project_id}.{dataset_id}.{table_id}`
        WHERE EXISTS (SELECT 1 FROM UNNEST(categories) AS category WHERE LOWER(category) = '{normalized_product_name}')
           OR CONTAINS_SUBSTR(LOWER(product_title), '{normalized_product_name}')
           OR CONTAINS_SUBSTR(LOWER(product_description), '{normalized_product_name}')
           OR LOWER(product_id) = '{normalized_product_name}'
    """

    log_message(f"Query: {query}", Severity.INFO)
    log_message(f"Project ID: {project_id}", Severity.INFO)
    log_message(f"Dataset ID: {dataset_id}", Severity.INFO)
    log_message(f"Table ID: {table_id}", Severity.INFO)

    try:
        query_job = client.query(query)
        results = query_job.result()

        for row in results:
            dict_result = dict(row)
            result:str = dict_result["product_image_url"]
            log_message(f"Found product: {result}", Severity.INFO)
            return result

    except Exception as e:
        log_message(f"ERROR: An error occurred in select_product_from_bq: {e}", Severity.ERROR)
        return None

    return None
