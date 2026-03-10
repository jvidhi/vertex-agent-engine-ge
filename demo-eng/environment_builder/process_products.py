"""
Processes a JSON file containing product data, uploads product images to Google Cloud Storage (GCS),
and inserts the structured product data into a BigQuery table.

This script is designed to be run from the command line and provides several modes for handling
existing data, including replacing the BigQuery table, re-uploading all images, or completely
restarting the process by wiping the GCS bucket.

Key functionalities:
- Loads environment variables for GCP configuration.
- Sanitizes product IDs to create GCS-safe filenames.
- Decodes base64 image strings and uploads them to a GCS bucket.
- Defines and creates a BigQuery table with a specific schema for product data.
- Maps the fields from the input JSON to the BigQuery schema.
- Provides different data ingestion modes (--replace-table, --replace-all, --restart).
- Handles command-line arguments for specifying the input file and ingestion mode.
"""
import argparse
import base64
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google.cloud import bigquery, storage


# This code is based on the output of http://go/synthetic-commerce
# https://vertex-ai-search-for-commerce-data-generator-295158667859.us-west1.run.app/


# Load environment variables from a .env file if it exists.
load_dotenv()

# --- GCP Configuration ---
# Clients are now initialized within the main block based on command-line arguments.


import re

def _sanitize_product_id(product_id: str) -> str:
    """
    Sanitizes a product ID to make it safe for use as a GCS object name.

    This function performs the following operations:
    - Replaces spaces with hyphens.
    - Removes any characters that are not alphanumeric, underscores, periods, or hyphens.

    Args:
        product_id: The raw product ID string.

    Returns:
        A sanitized, GCS-safe version of the product ID.
    """
    # Replace spaces with hyphens for better URL compatibility.
    s = product_id.replace(' ', '-')
    # Remove all characters that are not standard for filenames.
    return re.sub(r'[^a-zA-Z0-9_.-]', '', s)

def upload_base64_to_gcs(
    storage_client: storage.Client,
    bucket_name: str,
    base64_string: str,
    product_id: str,
    overwrite: bool = False
) -> Optional[str]:
    """
    Decodes a base64 image string, uploads it to GCS, and returns the public URL.

    The image is named using the sanitized product ID. If an image with the same name
    already exists in the bucket, the upload is skipped unless `overwrite` is True.

    Args:
        base64_string: The base64-encoded image string (e.g., "data:image/png;base64,...").
        product_id: The ID of the product associated with the image.
        overwrite: If True, an existing image with the same name will be overwritten.

    Returns:
        The public HTTPS URL of the uploaded image, or None if an error occurred.
    """
    try:
        # Skip if the string is not a valid base64 image data URI.
        if not base64_string or not base64_string.startswith("data:image"):
            return base64_string

        # Split the data URI into header (for content type) and encoded data.
        header, encoded = base64_string.split(",", 1)

        # Determine the file extension from the header.
        file_ext = "png"  # Default to png
        if "jpeg" in header or "jpg" in header:
            file_ext = "jpg"
        elif "gif" in header:
            file_ext = "gif"

        # Create a GCS-safe, deterministic filename.
        sanitized_id = _sanitize_product_id(product_id)
        blob_name = f"{sanitized_id}.{file_ext}"
        
        # Get a reference to the GCS bucket and blob.
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        # If overwrite is False and the image already exists, skip the upload.
        if not overwrite and blob.exists():
            public_url = f"https://storage.cloud.google.com/{bucket_name}/{blob_name}"
            print(f"  -> Image exists, skipping upload: {public_url}")
            return public_url

        # Decode the base64 string and upload the data.
        image_data = base64.b64decode(encoded)
        # Extract content type from something like "data:image/png;base64"
        parts = header.split(":")
        content_type = parts[1].split(";")[0] if len(parts) > 1 else "image/png"
        blob.upload_from_string(image_data, content_type=content_type)

        # Return the public URL of the uploaded image.
        public_url = f"https://storage.cloud.google.com/{bucket_name}/{blob_name}"
        print(f"  -> Uploaded image to {public_url}")
        return public_url

    except Exception as e:
        print(f"  -> Error uploading image for {product_id}: {e}")
        return None


def ensure_bq_table_exists(
    bq_client: bigquery.Client,
    dataset_id: str,
    table_id: str,
    replace: bool = False
):
    """
    Ensures the target BigQuery table exists, creating it if necessary.

    If the table already exists, the script's behavior depends on the `replace` flag:
    - If `replace` is True, the existing table is dropped and a new one is created.
    - If `replace` is False, the script prints an error and exits.

    This function also creates the BigQuery dataset if it does not already exist.

    Args:
        replace: If True, an existing table will be replaced.
    """
    dataset_ref = bq_client.dataset(dataset_id)
    table_ref = dataset_ref.table(table_id)

    # Define the schema for the BigQuery table.
    schema = [
        bigquery.SchemaField("product_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("product_title", "STRING"),
        bigquery.SchemaField("product_description", "STRING"),
        bigquery.SchemaField("price", "FLOAT"),
        bigquery.SchemaField("currency_code", "STRING"),
        bigquery.SchemaField("availableQuantity", "INTEGER"),
        bigquery.SchemaField("product_image_url", "STRING"),
        bigquery.SchemaField("categories", "STRING", mode="REPEATED"),
        bigquery.SchemaField("last_updated", "TIMESTAMP"),
    ]

    try:
        # Check if the table already exists.
        bq_client.get_table(table_ref)
        if replace:
            # If replace is True, drop the existing table.
            print(f"Table {dataset_id}.{table_id} exists. Dropping it...")
            bq_client.delete_table(table_ref)
        else:
            # If replace is False, exit with an error.
            print(f"Error: Table {dataset_id}.{table_id} already exists.")
            print("Use --replace-table, --replace-all, or --restart to overwrite it.")
            exit(1)
    except Exception:
        # If the table doesn't exist, an exception is raised, which we can ignore.
        pass

    print(f"Creating table {dataset_id}.{table_id}...")
    try:
        # Ensure the dataset exists before trying to create the table.
        try:
            bq_client.get_dataset(dataset_ref)
        except Exception:
            print(f"Dataset {dataset_id} not found. Creating it...")
            bq_client.create_dataset(dataset_ref, exists_ok=True)

        # Create the new table with the defined schema.
        table = bigquery.Table(table_ref, schema=schema)
        bq_client.create_table(table)
        print(f"Table {dataset_id}.{table_id} created.")
    except Exception as e:
        print(f"Error creating table: {e}")
        exit(1)


def map_product_to_schema(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforms a product dictionary from the input JSON to match the BigQuery schema.

    This function handles:
    - Extracting and renaming fields (e.g., 'title' to 'product_title').
    - Processing nested objects (e.g., 'priceInfo').
    - Ensuring 'categories' is a list and removing duplicates.
    - Generating a 'last_updated' timestamp.

    Args:
        product: A dictionary representing a single product from the JSON file.

    Returns:
        A new dictionary formatted for insertion into the BigQuery table.
    """
    # Extract the public URL of the first image (already processed).
    image_url = None
    if "images" in product and isinstance(product["images"], list) and product["images"]:
        first_image = product["images"][0]
        if isinstance(first_image, dict):
            image_url = first_image.get("uri")

    # Ensure categories are a list of unique strings.
    categories = product.get("categories", [])
    if isinstance(categories, str):
        categories = [categories]
    categories = list(set(categories))

    # Safely extract nested price information.
    price_info = product.get("priceInfo", {})

    # Map the JSON fields to the BigQuery schema fields.
    return {
        "product_id": product.get("id", str(uuid.uuid4())),
        "product_title": product.get("title"),
        "product_description": product.get("description"),
        "price": price_info.get("price"),
        "currency_code": price_info.get("currencyCode"),
        "availableQuantity": product.get("availableQuantity"),
        "product_image_url": image_url,
        "categories": categories,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def process_and_insert(
    storage_client: storage.Client,
    bq_client: bigquery.Client,
    project_id: str,
    bucket_name: str,
    dataset_id: str,
    table_id: str,
    json_path: str,
    mode: str
):
    """
    Main function to orchestrate the processing of the product JSON file.

    This function reads the JSON file, processes each product, handles image uploads,
    and inserts the data into BigQuery based on the specified ingestion mode.

    Args:
        json_path: The local file path to the input JSON file.
        mode: The ingestion mode, which can be 'default', 'replace-table',
              'replace-all', or 'restart'.
    """
    # 1. Validate file existence.
    if not os.path.exists(json_path):
        print(f"Error: File not found at {json_path}")
        return

    # 2. Load and parse the JSON data.
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to decode JSON. {e}")
        return

    # The input can be a single product object or a list of them. Normalize to a list.
    products = raw_data if isinstance(raw_data, list) else [raw_data]
    rows_to_insert = []

    print(f"Processing {len(products)} records from {json_path} in mode: {mode}...")

    # --- Mode-Specific Operations ---
    # In 'restart' mode, wipe the entire GCS bucket after user confirmation.
    if mode == "restart":
        print(f"WARNING: You are about to DELETE ALL FILES in bucket '{bucket_name}'.")
        confirm = input("Are you sure you want to delete all files in this bucket? (y/n): ")
        if confirm.lower() != 'y':
            print("Operation cancelled.")
            exit(0)
        
        print(f"Deleting all blobs in {bucket_name}...")
        try:
            bucket = storage_client.bucket(bucket_name)
            blobs = list(bucket.list_blobs())
            if blobs:
                bucket.delete_blobs(blobs)
                print(f"Deleted {len(blobs)} blobs.")
            else:
                print("Bucket is already empty.")
        except Exception as e:
            print(f"Error wiping bucket: {e}")
            exit(1)

    # Determine if the BigQuery table should be replaced based on the mode.
    should_replace_table = mode in ["replace-table", "replace-all", "restart"]
    ensure_bq_table_exists(
        bq_client=bq_client,
        dataset_id=dataset_id,
        table_id=table_id,
        replace=should_replace_table
    )

    # Determine if GCS images should be overwritten.
    overwrite_images = mode in ["replace-all", "restart"]

    # 3. Process each product.
    for product in products:
        prod_id = product.get("id", "unknown")

        # Upload the first image from the 'images' list.
        if "images" in product and isinstance(product["images"], list) and product["images"]:
            img_obj = product["images"][0]
            if "uri" in img_obj:
                gcs_uri = upload_base64_to_gcs(
                    storage_client=storage_client,
                    bucket_name=bucket_name,
                    base64_string=img_obj["uri"],
                    product_id=prod_id,
                    overwrite=overwrite_images
                )
                # Update the URI in the product dict to the new GCS URL for mapping.
                if gcs_uri:
                    img_obj["uri"] = gcs_uri

        # Map the processed product data to the BigQuery schema.
        bq_row = map_product_to_schema(product)
        rows_to_insert.append(bq_row)

    # 4. Insert all rows into BigQuery in a single batch.
    if rows_to_insert:
        table_ref = f"{project_id}.{dataset_id}.{table_id}"
        errors = bq_client.insert_rows_json(table_ref, rows_to_insert)

        if not errors:
            print(
                f"Success: {len(rows_to_insert)} rows inserted into BigQuery table"
                f" {table_id}."
            )
        else:
            print("Encountered errors while inserting rows:")
            print(errors)


if __name__ == "__main__":
    # --- Argument Parsing ---
    # Sets up the command-line interface to accept the input file and ingestion mode.
    parser = argparse.ArgumentParser(
        description="Upload Base64 images to GCS and push JSON to BigQuery."
    )
    # Positional argument for the JSON file.
    parser.add_argument(
        "input_file", help="Relative or absolute path to the input JSON file"
    )

    # Required arguments for GCP configuration.
    parser.add_argument(
        "--project-id", "-p", required=True, help="Google Cloud Project ID"
    )
    parser.add_argument(
        "--bucket-name", "-b", required=True, help="Google Cloud Storage Bucket name for catalog images"
    )
    parser.add_argument(
        "--dataset-id", "-d", required=True, help="BigQuery Dataset ID"
    )
    parser.add_argument(
        "--table-id", "-t", required=True, help="BigQuery Table ID"
    )
    
    # Mutually exclusive group for the ingestion modes.
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--replace-table", action="store_true", help="Recreate BQ table but keep existing GCS images"
    )
    group.add_argument(
        "--replace-all", action="store_true", help="Recreate BQ table AND overwrite GCS images"
    )
    group.add_argument(
        "--restart", action="store_true", help="Wipe GCS bucket, recreate BQ table, and upload everything"
    )

    args = parser.parse_args()

    # --- Mode Determination ---
    mode = "default"
    if args.replace_table:
        mode = "replace-table"
    elif args.replace_all:
        mode = "replace-all"
    elif args.restart:
        mode = "restart"

    # --- Client Initialization ---
    try:
        storage_client = storage.Client(project=args.project_id)
        bq_client = bigquery.Client(project=args.project_id)
    except Exception as e:
        print(f"Error initializing clients: {e}")
        print("Ensure GOOGLE_APPLICATION_CREDENTIALS is set or you are authenticated via gcloud.")
        exit(1)

    # --- Start Processing ---
    process_and_insert(
        storage_client=storage_client,
        bq_client=bq_client,
        project_id=args.project_id,
        bucket_name=args.bucket_name,
        dataset_id=args.dataset_id,
        table_id=args.table_id,
        json_path=args.input_file,
        mode=mode
    )