# Marketing Plan Agent

The **Marketing Plan Agent** is a creative AI assistant specializing in crafting comprehensive and effective marketing strategies. It is responsible for strategic planning, determining audience segments, and crafting campaign strategies based on user input and available data.

## 🌟 Capabilities

The agent acts as a world-class AI Marketing Strategist capable of:
*   **Comprehensive Marketing Plans:** Generating full, data-driven marketing strategies from scratch.
*   **Campaign Ideation:** Crafting campaign themes, taglines, and core messaging.
*   **Channel Strategy:** Selecting the optimal marketing channels (e.g., Instagram, LinkedIn, Google Ads) based on the target audience and goals.
*   **Content Creation:** Drafting social media posts, determining visual cues, and writing ad copy.
*   **Target Audience Analysis:** Defining demographics, psychographics, and buyer personas.

## 📥 Input Schema

To get the best results from the Marketing Plan Agent, provide as much structured context as possible. 

### **Recommended Inputs** (Essential for a strong strategy):
*   `Brand_Name`: The official name of the business, product, or project.
*   `Product_Details`: A clear description of the product/service, key features, benefits, and Unique Selling Proposition (USP).
*   `Primary_Goal`: The primary, specific, and measurable objective (e.g., "Generate 150 B2B leads in Q4").
*   `Target_Audience`: Detailed description of the ideal customer, covering **Demographics** (age, gender, location, income) and **Psychographics** (lifestyle, values, pain points).

### **Optional Inputs** (Enhances customization):
*   `Budget`: Allocated budget (e.g., Low, Medium, High).
*   `Brand_Voice`: Desired tone and personality (e.g., Witty, Authoritative).
*   `Competitors`: 1-3 main competitors and their strengths/weaknesses.
*   `KPIs`: Key performance indicators to measure success.
*   `Timeline`: Desired timeframe for implementation.
*   `Existing_Brand_Assets`: Links to logos, style guides, etc.
*   `Audience_Habits`, `Past_Performance`, `Channel_Preferences`, `Geographic_Focus`.

*(Note: If essential information is missing, the agent will gracefully make educated assumptions after confirming with the user, or it will explicitly ask for the required fields.)*

## 📤 Output Format

When generating a full marketing strategy, the agent structures its output into organized, readable modules using tables and bullet points:

1.  **Core Strategy Summary:** High-level overview including Target Persona, Core Message, Recommended Channels, and Key KPIs.
2.  **Campaign Ideation:** Creative concepts including a Campaign Theme, Tagline, and Rationale.
3.  **Channel & Content Plan:** A structured table breaking down Channels, Target Segments, Example Content, and the Strategic Rationale for each.
4.  **Social Media Content Examples:** Specific post drafts including the Platform, Post Copy, suggested Hashtags, and Visual Cues.

## 📝 Example Prompts

Here are examples of how to structure a prompt for the Marketing Plan Agent to get a full plan or a specific component.

**Example 1: Comprehensive Strategy Request**
```text
Please create a comprehensive marketing strategy using the following details:
- **Brand_Name**: Innovatech Solutions
- **Product_Details**: A SaaS platform that streamlines project management and team collaboration with AI-driven task prioritization. Our USP is reducing meeting time by 30%.
- **Primary_Goal**: Generate 150 qualified B2B leads in Q4.
- **Target_Audience**:
  - *Demographics*: Tech-savvy project managers and team leads, ages 28-45, located in North America and Europe.
  - *Psychographics*: They value efficiency and are frustrated by disjointed tools and endless status meetings.
- **Budget**: $5,000/month.
- **Brand_Voice**: Professional, authoritative, yet approachable and modern.
```

**Example 2: Specific Component Request**
```text
Based on the Innovatech Solutions profile, can you write 3 LinkedIn post examples targeting Decision Makers that highlight our AI-driven task prioritization? 
```

## ⚙️ Configuration (Environment Variables)

| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `LLM_GEMINI_MODEL_MARKETINGPLAN` | The Gemini model used for strategy generation. | `gemini-2.5-flash` |
| `GOOGLE_CLOUD_PROJECT` | GCP Project ID. | |
| `GOOGLE_CLOUD_LOCATION` | Region for Vertex AI calls. | `us-central1` |

## 🏃 Usage

### Run as Module
```bash
uv run python -m marketing_plan_agent.agent
```

### Updating the Agent Version

When releasing a new version of the agent, you must update the version across codebase configurations:

1. **Update package version**: Modify the `version` field in the agent's `pyproject.toml`.
2. **Update environment**: Change the `AGENT_VERSION` variable in the agent's `.env` file (if used locally).
3. **Create new deployment config**:
   * Duplicate the latest JSON configuration in `deployment_config/`.
   * Rename the new file to reflect the new version (e.g., `<version>-prod.json`).
   * Inside the new JSON file, update any internal references to the version (e.g., `env_vars.AGENT_VERSION`, `agent_display_name`, and `whl_file_path`).
4. **Deploy**: Run the deployment script pointing to the new configuration file.
5. **Update Engine ID (New Deployments Only)**: If you deployed a completely new agent (i.e. not using the `--update_agent` flag, and the JSON lacked a value for `agent_engine_id_to_update`), you must update your new deployment JSON post-deployment. You can find the newly generated reasoning engine ID in the terminal logs, which has the structure `projects/[project]/locations/[location]/reasoningEngines/[agent_engine_id]` (e.g., `projects/39704124777/locations/us-central1/reasoningEngines/8551151518353457152`). Extract **only** the `[agent_engine_id]` (the numeric part at the end) and set this value in the `"agent_engine_id_to_update"` field of your JSON config.
