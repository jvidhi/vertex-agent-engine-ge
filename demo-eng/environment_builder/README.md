# Product Data Ingestion Script (`process_products.py`)

This script automates the process of ingesting product data from a JSON file into Google Cloud Platform (GCP). It handles uploading product images to Google Cloud Storage (GCS) and inserting structured product metadata into a BigQuery table.

## 📊 Data Source & Generation

For this script to be used, you must first produce a product catalog JSON file using the **Vertex AI Search for Commerce Data Generator**:
- **Tool URL**: [http://go/synthetic-commerce](http://go/synthetic-commerce) (External: [Vertex AI Search Data Generator](https://vertex-ai-search-for-commerce-data-generator-295158667859.us-west1.run.app/))

This tool allows you to generate synthetic retail datasets (with base64-encoded images) which this script then parses and uploads to GCP.

## Features

- **Image Upload**: Decodes base64 images from the input JSON and uploads them to GCS.
- **BigQuery Insertion**: Maps product data to a structured schema and inserts it into BigQuery.
- **Idempotency**: Sanitizes product IDs for consistent GCS filenames and supports overwriting existing data.
- **Multiple Modes**: Supports different ingestion modes to handle existing data (replace table, replace all, restart).

## Prerequisites

- Python 3.7+
- Google Cloud SDK (`gcloud`) installed and authenticated.
- Required Python packages: `google-cloud-storage`, `google-cloud-bigquery`, `python-dotenv`.

## Configuration

The script can use environment variables for configuration. You can set these in your shell or a `.env` file in the same directory.
Alternatively, you can hard code these in your code.
Except for the `GOOGLE_CLOUD_PROJECT`, all other assets will be automatically created (depending on the arguments provided to the script).

| Variable | Description | Default (if any) |
| :--- | :--- | :--- |
| `GOOGLE_CLOUD_PROJECT` | Your GCP Project ID | None (Required) |
| `GOOGLE_CLOUD_BUCKET_CATALOG` | GCS bucket name for images | None (Required) |
| `BQ_DATASET` | BigQuery dataset ID | `nrf_marketing_catalog` |
| `BQ_TABLE` | BigQuery table ID | `nrf_marketing_catalog_table` |


## Usage

Run the script from the command line, providing the path to your input JSON file.

### Basic Usage

```bash
python process_products.py path/to/products.json
```

### Ingestion Modes

- **Default**: Inserts data into BigQuery. Fails if the table already exists. Skips image upload if the file already exists in GCS.
- **Replace Table**: Recreates the BigQuery table but keeps existing GCS images.
  ```bash
  python process_products.py path/to/products.json --replace-table
  ```
- **Replace All**: Recreates the BigQuery table and overwrites existing GCS images.
  ```bash
  python process_products.py path/to/products.json --replace-all
  ```
- **Restart**: Wipes the GCS bucket, recreates the BigQuery table, and uploads everything fresh. **Use with caution.**
  ```bash
  python process_products.py path/to/products.json --restart
  ```

## Input Data Format

The script expects a JSON file containing an array of product objects. Each object should follow this structure:
This should be taken from http://go/synthetic-commerce (https://vertex-ai-search-for-commerce-data-generator-295158667859.us-west1.run.app/)

```json
[
  {
    "id": "product-123",
    "title": "Example Product",
    "description": "A description of the product.",
    "priceInfo": {
      "price": 29.99,
      "currencyCode": "USD"
    },
    "availableQuantity": 100,
    "categories": ["Category A", "Category B"],
    "images": [
      {
        "uri": "data:image/png;base64,..."
      }
    ]
  }
]
```

## GCP Setup (Brief)
1.  **Authentication**:
    ```bash
    gcloud auth application-default login
    ```
