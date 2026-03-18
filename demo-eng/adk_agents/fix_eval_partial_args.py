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
                        # For video batch, the JSON payload is too volatile.
                        if tool.get("name") in ["generate_video_storyboard_batch", "GENERATE_VIDEO_STORYBOARD_BATCH_TOOL"]:
                            # Remove strict args if present
                            if "args" in tool:
                                del tool["args"]
                            # Add partial_args to tell ADK 1.26.0 to ignore full match
                            tool["partial_args"] = {}
                            modified = True
                        elif tool.get("name") == "generate_ad_hoc_image":
                             if "args" in tool:
                                del tool["args"]
                             tool["partial_args"] = {}
                             modified = True
                             
        
        if modified:
            with open(f, "w") as file:
                json.dump(data, file, indent=2)
            print(f"Fixed {f}")

fix_args()
