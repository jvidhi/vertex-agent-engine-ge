import pytest
import argparse
import asyncio
import os
import sys
import datetime
from google.adk.evaluation.agent_evaluator import AgentEvaluator
from adk_common.utils.env_loader import load_env_cascade
from adk_common.utils.constants import get_required_env_var
from google.cloud import storage

# Load environment variables
load_env_cascade(__file__)

GOOGLE_CLOUD_PROJECT = get_required_env_var("GOOGLE_CLOUD_PROJECT")
BUCKET_NAME = get_required_env_var("GOOGLE_CLOUD_BUCKET_ARTIFACTS")

class GCSUploader:
    def __init__(self, project_id, bucket_name):
        self.client = storage.Client(project=project_id)
        self.bucket = self.client.bucket(bucket_name)

    def upload_file(self, local_path, destination_blob_name):
        blob = self.bucket.blob(destination_blob_name)
        blob.upload_from_filename(local_path)
        print(f"✅ Log uploaded to: gs://{BUCKET_NAME}/{destination_blob_name}")

@pytest.mark.asyncio
async def test_all_evals():
    """Run all ADK evals in the directory. (Used automatically by PyTest CI/CD)"""
    evals_dir = os.path.join(os.path.dirname(__file__), "evals")
    
    # Setup logging for pytest run
    os.makedirs("eval_results", exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_name = f"pytest_run_{timestamp}.log"
    local_log_path = f"eval_results/{log_file_name}"
    
    print(f"Starting pytest evaluation... Writing detailed results to: {local_log_path}")
    
    with open(local_log_path, "w") as f:
        original_stdout = sys.stdout
        sys.stdout = f
        try:
            await AgentEvaluator.evaluate(
                agent_module="ad_generation_agent",
                eval_dataset_file_path_or_dir=evals_dir,
                print_detailed_results=True,
            )
        finally:
            sys.stdout = original_stdout
            
    # Upload to GCS
    try:
        uploader = GCSUploader(GOOGLE_CLOUD_PROJECT, BUCKET_NAME)
        gcs_destination = f"sessions/eval_session/{log_file_name}"
        uploader.upload_file(local_log_path, gcs_destination)
    except Exception as e:
        print(f"❌ Failed to upload log to GCS: {e}")

async def _run_cli(eval_path: str):
    """Run a specific eval and dump logs. (Used manually via CLI)"""
    os.makedirs("eval_results", exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_name = f"eval_run_{timestamp}.log"
    local_log_path = f"eval_results/{log_file_name}"
    
    print(f"Starting evaluation of '{eval_path}'... Writing detailed results to: {local_log_path}")
    
    with open(local_log_path, "w") as f:
        original_stdout = sys.stdout
        sys.stdout = f
        try:
            await AgentEvaluator.evaluate(
                agent_module="ad_generation_agent",
                eval_dataset_file_path_or_dir=eval_path, 
                print_detailed_results=True,
            )
        except Exception as e:
            print(f"Error during evaluation: {e}")
        finally:
            sys.stdout = original_stdout
    
    print("Evaluation completed.")
    
    # Upload to GCS
    try:
        uploader = GCSUploader(GOOGLE_CLOUD_PROJECT, BUCKET_NAME)
        gcs_destination = f"sessions/eval_session/{log_file_name}"
        uploader.upload_file(local_log_path, gcs_destination)
    except Exception as e:
        print(f"❌ Failed to upload log to GCS: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ADK Agent Evaluations manually.")
    parser.add_argument(
        "eval_path",
        nargs="?",
        default="evals",
        help="Path to a specific .test.json file or a directory. Defaults to 'evals'.",
    )
    args = parser.parse_args()
    asyncio.run(_run_cli(args.eval_path))
