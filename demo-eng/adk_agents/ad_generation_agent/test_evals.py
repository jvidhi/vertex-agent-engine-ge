import pytest
import argparse
import asyncio
import os
import sys
import datetime
from google.adk.evaluation.agent_evaluator import AgentEvaluator

@pytest.mark.asyncio
async def test_all_evals():
    """Run all ADK evals in the directory. (Used automatically by PyTest CI/CD)"""
    evals_dir = os.path.join(os.path.dirname(__file__), "evals")
    await AgentEvaluator.evaluate(
        agent_module="ad_generation_agent",
        eval_dataset_file_path_or_dir=evals_dir,
        print_detailed_results=True,
    )

async def _run_cli(eval_path: str):
    """Run a specific eval and dump logs. (Used manually via CLI)"""
    os.makedirs("eval_results", exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = f"eval_results/eval_run_{timestamp}.log"
    print(f"Starting evaluation of '{eval_path}'... Writing detailed results to: {log_file_path}")
    
    with open(log_file_path, "w") as f:
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
