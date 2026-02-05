from typing import TypedDict, List, Annotated, Optional
import operator
from langchain_core.messages import AnyMessage

class AgentState(TypedDict):
    """
    Shared state for the review agent workflow.
    """
    messages: Annotated[List[AnyMessage], operator.add]
    pr_diff: str
    retrieved_content: str
    root_cause_analysis: str
    code_review: str
    jira_ticket_description: Optional[str]
    task_description: Optional[str]
    generated_tests: Optional[str]
    primary_language: Optional[str]
