#merangkai nodes menjadi satu StateGraph
from langgraph.graph import StateGraph, END
from app.agents.state import AgentState
from app.agents.nodes import (
    fetch_data_node,
    temporal_filter_node,
    dietary_and_capacity_matching_node
)

def build_negotiation_graph():
    workflow = StateGraph(AgentState)

    # 1. Tambah Node
    workflow.add_node("fetch_data", fetch_data_node)
    workflow.add_node("temporal_check", temporal_filter_node)
    workflow.add_node("matching_engine", dietary_and_capacity_matching_node)

    # 2. Atur Alur (Edges)
    workflow.set_entry_point("fetch_data")
    workflow.add_edge("fetch_data", "temporal_check")
    workflow.add_edge("temporal_check", "matching_engine")
    workflow.add_edge("matching_engine", END)

    # 3. Compile Graph
    return workflow.compile()

negotiation_agent = build_negotiation_graph()