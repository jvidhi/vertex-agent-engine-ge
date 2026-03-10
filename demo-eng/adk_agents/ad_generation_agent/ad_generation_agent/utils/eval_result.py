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

from typing import Literal, List

from pydantic import BaseModel, model_validator


class Defect(BaseModel):
    timestamp: str
    category: str
    description: str


class CategoryScores(BaseModel):
    subject_and_brand: Literal["Pass", "Fail", "N/A"]
    physics_and_logic: Literal["Pass", "Fail", "N/A"]
    visual_fidelity: Literal["Pass", "Fail", "N/A"]
    temporal_flow: Literal["Pass", "Fail", "N/A"]
    consistency: Literal["Pass", "Fail", "N/A"]



class SceneFeedback(BaseModel):
    timestamp: str
    decision: Literal["Pass", "Fail", "Fix"]
    description: str
    improvement_suggestion: str
    impact_analysis: str = ""  # Optional continuity notes


class EvalResult(BaseModel):
    """Represents the structured result of a media evaluation using the Unified Schema."""

    decision: Literal["Pass", "Fail"]
    score: int
    summary_reason: str
    improvement_prompt: str
    defects: List[Defect]
    scene_feedback: List[SceneFeedback] = []  # Detailed scene analysis
    category_scores: CategoryScores
    
    # Computed fields
    calculated_evaluation_score: int = 0
    averaged_evaluation_score: int = 0

    @property
    def llm_evaluation_score(self) -> int:
        return self.score

    @model_validator(mode="after")
    def calculate_scores(self) -> "EvalResult":
        # 1. Validate LLM Score
        self.score = max(0, min(100, self.score))

        # 2. Calculate Category Score
        # We assign equal weight (20 pts) to each of the 5 categories.
        # If a category is "N/A", it is excluded from the total possible score.
        category_weights = {
            "subject_and_brand": 20,
            "physics_and_logic": 20,
            "visual_fidelity": 20,
            "temporal_flow": 20,
            "consistency": 20
        }
        
        earned_points = 0
        total_possible_points = 0
        
        for field, weight in category_weights.items():
            status = getattr(self.category_scores, field)
            if status != "N/A":
                total_possible_points += weight
                if status == "Pass":
                    earned_points += weight
        
        if total_possible_points > 0:
            self.calculated_evaluation_score = int((earned_points / total_possible_points) * 100)
        else:
            self.calculated_evaluation_score = 0
            
        # 3. Calculate Averaged Score
        self.averaged_evaluation_score = int((self.calculated_evaluation_score + self.score) / 2)
        
        return self