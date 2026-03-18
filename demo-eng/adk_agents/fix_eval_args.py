import json
import glob

def fix_args():
    files = glob.glob("ad_generation_agent/evals/*.test.json")
    for f in files:
        with open(f, "r") as file:
            data = json.load(file)
            
        modified = False
        for case in data.get("eval_cases", []):
            for turn in case.get("conversation", []):
                
                # Check intermediate data structure
                if "intermediate_data" in turn:
                    tool_uses = turn["intermediate_data"].get("tool_uses", [])
                    for tool in tool_uses:
                        if tool.get("name") == "retrieve_brand_identity":
                            tool["args"] = {"company_name": "Vantus"}
                            modified = True
                        elif tool.get("name") == "generate_asset_sheet":
                            tool["args"] = {"company_name": "Vantus"}
                            modified = True
                        elif tool.get("name") == "generate_video_storyboard_batch":
                             # We can't perfectly predict the generated JSON string here, 
                             # so we'll have to rely on ADK 1.26.0's exact-match fallbacks or partial matching if supported.
                             # If exact match fails due to LLM variance, we might need a custom evaluator rather than trajectory matching.
                             # For now, let's just make sure "args" isn't strictly empty for all of them if they expect it.
                             pass
                             
        
        if modified:
            with open(f, "w") as file:
                json.dump(data, file, indent=2)
            print(f"Fixed {f}")

fix_args()
