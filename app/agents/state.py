#definisi struktu data yang akan dibawa dan diolah oleh agent dari awal samapi akhir alu matching
from typing import TypedDict, List, Dict, Optional

class AgentState(TypedDict):
    listing_id: str
    food_item: Optional[Dict]
    candidate_shelters: List[Dict]
    eligible_shelters: List[Dict]
    matched_shelter: Optional[Dict]
    match_score: float
    status: str
    log_messages: List[str]