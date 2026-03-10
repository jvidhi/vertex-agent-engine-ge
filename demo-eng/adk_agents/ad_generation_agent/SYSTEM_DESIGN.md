# Ad Generation Agent: System Architecture & LLM Context

> **PURPOSE:** This document serves as the absolute ground-truth architectural reference for the `ad_generation_agent`. It is explicitly designed to be read by Large Language Models (LLMs) to understand the system's design choices, structural conventions, constraints, and non-functional requirements (NFRs) *before* proposing or executing code changes.

---

## 1. System Overview & The "Asset-First" Workflow
The Ad Generation Agent is a multi-modal creative orchestrator built on top of Google Cloud's Vertex AI (Gemini, Imagen 3, Veo 3.1). Instead of generating a final video blindly, it enforces a strict, stateful **Asset-First Pipeline**:

1. **Brand Discovery (`RETRIEVE_BRAND_IDENTITY_TOOL`)**: Fuzzy matches user input against a GCS catalog to pull canonical logos, product shots, and style guides perfectly into memory context (`BRAND_CONTEXT_PAYLOAD`).
2. **Concept Anchoring (`GENERATE_ASSET_SHEET_TOOL`)**: Generates an isolated "Prop Sheet" or "Character Turnaround" to force downstream models to use consistent subjects (combating temporal hallucination).
3. **Narrative Construction**: The LLM natively writes a storyboard sequence based on the approved assets.
4. **Media Generation (Images & Videos)**: The agent fans out across highly-concurrent pipelines to generate, evaluate, and self-heal media for every scene.

---

## 2. Directory & Structural Taxonomy
The codebase strictly distances the LLM's cognitive loop from the underlying infrastructure API calls.

*   `ad_generation_agent/prompt.md`: The brain. Contains the explicitly enumerated state-machine logic the agent uses to guide the user. It explicitly orders the agent to halt and ask for permission before moving between workflow stages.
*   `ad_generation_agent/agent.py`: Langchain/Vertex core setup orchestrating the `LlmAgent` initialization and registering the specific `FunctionTool` wrappers.
*   `ad_generation_agent/func_tools/`: Thin, LLM-facing function signatures. These files map the natural language intent into typed Python variables. **Crucially, they implement parallelization (using `asyncio.gather`) for batch operations**, fanning out scene generation requests.
*   `ad_generation_agent/utils/`: The deep implementation tier. Files like `video_generation.py` directly instantiate Vertex API clients, execute exponential backoffs, enforce duration rounding logic, and handle the "LLM-as-a-Judge" evaluation loops.

---

## 3. Core Design Choices & Trade-offs

### A. The "Veo 3.1 Frame vs. Reference" Dichotomy (CRITICAL)
Google's Veo 3.1 model has disparate endpoint behaviors depending on the payload arrays. To reduce LLM cognitive load while navigating these differences, we use a **Unified Batch Router** pattern (`GENERATE_VIDEO_STORYBOARD_BATCH_TOOL`):

1.  **The `generation_modality` Switch**: 
    *   The LLM dictates the video generation style per scene within the single `storyboard_json` payload.
    *   The Python backend dynamically parses this and routes to the appropriate asynchronous wrapper.
2.  **Modality 1: First-Frame (`first_frame`)**: 
    *   **Advantage:** Supports flexible durations (4s, 6s, 8s), precise spatial control, and highly dynamic cinematic movements. Mandatory for pure Logo scenes.
    *   **Trade-off:** Minimal inherent brand fidelity across frames. Relies strictly on the starting image.
3.  **Modality 2: Reference-Images (`reference_images`)**:
    *   **Advantage:** Unlocks the "Unified Reference Protocol"—injecting the Logo, Product Image, and Asset Sheet directly into the video backbone for ironclad brand adherence. Mandatory for "Hero" product shots.
    *   **Constraint (Strict Option B Escalation):** Veo 3.1 `reference_to_video` **requires exactly an 8-second generation duration**. If the LLM requests this modality with a different duration (e.g., 4s), the backend intercepts the thread and throws a soft Markdown error directly back to the orchestrator. This explicitly forces an internal self-correction loop where the LLM must rewrite the offending JSON payload and adjust its math.

### B. High-Concurrency vs. Rate Limiting (The Semaphore Pattern)
Because an ad storyboard might contain 6-10 scenes, generating media serially would cause painful UX delays.
*   **Design Choice:** Batch tools (`generate_storyboard_image_batch`, `generate_storyboard_video_batch`) parse the full JSON payload and launch ALL scenes simultaneously using `asyncio.gather`.
*   **Protection:** We inject `asyncio.Semaphore` (driven by `IMAGE_GENERATION_CONCURRENCY_LIMIT` / `VIDEO_...`) directly into the utility clients to mathematically prevent the parallel executions from triggering Vertex AI API Quota Limits (HTTP 429 errors).

### C. Self-Healing Media Generation ("LLM-as-a-Judge")
Visual generation is inherently stochastic and prone to prompt deviations. 
*   **Mechanism:** Rather than surfacing hallucinated or malformed media immediately to the user, the `utils` layer can optionally intercept the generated bytes, pass them alongside the original prompt to a specialized Gemini Evaluation Model, and score the output (Fail/Pass).
*   **The Loop:** If it fails, the Evaluator generates a "Fix Prompt" (e.g., "Add more lighting to the product"). The code automatically intercepts this, appends it to the original payload (`CRITICAL FIXES NEEDED OVER PREVIOUS ATTEMPT:`), and restarts generator API silently.
*   **Trade-off:** Massive increase to average generation latency (Wait times) traded off for a dramatic increase in ultimate media quality and prompt adherence.

### D. Parameter Omission vs Context Enforcement
The system tools intentionally mark brand parameters (like `logo_image_url`) as "Optional" in their code signatures. 
*   **Why?** Because the user might just want a generic video.
*   **The Catch:** The `prompt.md` includes a "CRITICAL MANDATE" explicitly overriding this: *If the system memory has identified a logo or product image during the Brand Discovery phase, the LLM is forcibly required to inject that parameter into the tool call.* This achieves "Magic Context" injection without crashing the tool if the context genuinely doesn't exist.

### E. JSON String Payload Serialization vs Structured OpenAPI
The batch-generation tools (`generate_storyboard_image_batch` and `generate_storyboard_video_batch`) intentionally request their entire payload as a single raw `storyboard_json` **string**, rather than deeply nested native array structures.
*   **Why?** Standard LLM tool-calling architectures often aggressively throttle or fail to parse extremely deep, dynamic schemas (like a 10-scene ad with branching parameters and reference arrays).
*   **The Trade-off:** By defining the input as a single `string` containing JSON, we force the LLM to serialize the entire payload internally and bypass strict OpenAPI parameter depth limits. The underlying Python code simply calls `json.loads(storyboard_json)` and immediately benefits from dynamic array sizes for arbitrarily large commercials.

---

## 4. The Native Agent "Evaluation Loop"
Because text-to-image and text-to-video models hallucinate, this agent intercepts raw byte streams *before* returning success to the LLM orchestrator.

### The Grader: `evaluate_media.py`
Every generated media asset is piped to `LLM_GEMINI_MODEL_EVALUATION` (typically Gemini 2.5 Pro/Flash) for a unified score out of 10.0, judged purely against the original text prompt.
The Gemini evaluator maps its judgment into a strict `EvalResult` schema evaluating 5 intrinsic criteria:
1.  **Subject & Brand:** Does the asset visually adhere to the provided reference images?
2.  **Physics & Logic:** Are there bizarre physics violations (e.g., extra fingers, floating objects)?
3.  **Visual Fidelity:** Is the resolution crisp, and the style cohesive?
4.  **Temporal Flow:** (Video only) Is the motion smooth and logical without catastrophic generation morphing?
5.  **Consistency:** Does it match the explicitly requested storyline?

**Self-Healing Implementation:**
If the `EvalResult.decision` is "Fail", the LLM-as-a-judge provides an `improvement_prompt` (e.g., *"The character is missing the requested red hat."*). The system suppresses the failure from the orchestrator, appends the feedback to the prompt natively (`CRITICAL FIXES NEEDED OVER PREVIOUS ATTEMPT: {feedback}`), and automatically restarts the Vertex generation call recursively until it passes or hits `VIDEO_GENERATION_EVAL_REATTEMPTS`.

---

## 5. Non-Functional Requirements (NFRs)
1.  **Stateless Crash Resilience:** All generated artifacts (Images, Videos) are simultaneously streamed to Google Cloud Storage (GCS) and saved to disk. All URIs returned back to the LLM are canonical `gs://` links. This ensures the LLM's context window can be perfectly restored or continued later without losing access to the heavy binary media payloads.
2.  **Robust Async Throttling (`Tenacity`):** The underlying API calls must use exponential backoff retries (`@retry(...)`) tuned specifically for standard multi-modal Vertex generation failure profiles.

## 6. Deployment & Configuration Matrix
This agent follows the **ADK Decentralized Deployment Pattern**. Its operating parameters are defined in specific JSON files within `deployment_config/` (e.g., `prod-3.20260119.1.json` or `staging-4.20260219.1.json`). 
*   **The Mapping Rule (CRITICAL):** Whenever new environment variables (like `VIDEO_GENERATION_CONCURRENCY_LIMIT`) are introduced to the core logic, they MUST be identically mapped into all relevant staging/prod JSON config matrices. This ensures perfect runtime parity in the Cloud Reasoning Engine environments where the overarching `marketing_orchestrator` relies on these JSONs to hydrate the Cloud Run application.
