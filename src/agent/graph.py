import json
from typing import Optional
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END

from src.config import Config
from src.agent.state import AgentState
from src.data.retriever import get_code_retriever

class Agent:
    def __init__(self, config: Config):
        """
        Initializes the Agent with Groq LLM and builds the graph.
        """
        self.config = config
        self.model = ChatGroq(
            api_key=config.groq_api_key,
            model_name=config.groq_model,
            temperature=0.2
        )
        
        # We will initialize the retriever lazily or per-repo if needed, 
        # but for the single-repo context implied, we can store it or fetch it in the method.
        # To strictly follow "Initialized once", we'll store a reference or cache.
        self.retriever = None 

    def root_cause_analysis_agent(self, state: AgentState):
        """
        Agent Node: Analyzes the root cause and intent of the PR changes.
        """
        print("--- Root Cause Analysis ---")
        pr_diff = state.get("pr_diff", "")
        # Retrieve context if available
        retrieved_content = state.get("retrieved_content", "")
        
        system_prompt = """You are a Senior Software Architect performing a Root Cause Analysis.
        Analyze the provided PR Diff and Retrieved Code Context.
        
        Determine:
        1. The intent of the changes (Refactor, Feature, specific Bug Fix).
        2. Potential architectural risks or mismatches.
        3. Why existing code was modified (Root Cause).
        
        Provide a concise analysis."""
        
        if not retrieved_content:
            context_str = "No existing code context retrieved."
        else:
            context_str = f"Related Codebase Context:\n{retrieved_content}"
            
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"PR Diff:\n{pr_diff}\n\n{context_str}")
        ]
        
        response = self.model.invoke(messages)
        
        return {
            "root_cause_analysis": response.content,
            "messages": [response] # Append to history
        }

    def code_review_agent(self, state: AgentState):
        """
        Agent Node: Performs the code review using RCA and diff.
        """
        print("--- Code Review ---")
        pr_diff = state.get("pr_diff", "")
        rca = state.get("root_cause_analysis", "")
        retrieved_content = state.get("retrieved_content", "")
        
        system_prompt = """You are an expert Code Reviewer.
        Using the Root Cause Analysis and Code Context, review the PR Diff.
        
        Responsibilities:
        - Enforce Python best practices, Maintainability, Performance, Security.
        
        Output Format:
        You must return a VALID JSON object (clean, no markdown formatting around it if possible) with:
        {
            "summary": "...",
            "severity_score": int (1-10),
            "issues": [
                {"type": "bug|security|style", "file": "...", "line": int, "description": "...", "suggestion": "..."}
            ],
            "suggestions": ["..."],
            "security_concerns": ["..."],
            "positive_feedback": ["..."]
        }
        """
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Deep Analysis (RCA):\n{rca}\n\nContext:\n{retrieved_content}\n\nPR Diff:\n{pr_diff}")
        ]
        
        response = self.model.invoke(messages)
        
        return {
            "code_review": response.content,
            "messages": [response]
        }
        
    def review_pr(self, pr_diff: str, repo_path: str, model_name: Optional[str] = None) -> str:
        """
        Public entry point to review a PR.
        
        Args:
            pr_diff: The diff string of the PR.
            repo_path: Path to local repository for retrieval.
            model_name: Optional model name to use for this review.
            
        Returns:
            The raw code review string (JSON content).
        """
        # Update model if a specific one is requested
        if model_name:
            self.model = ChatGroq(
                api_key=self.config.groq_api_key,
                model_name=model_name,
                temperature=0.2
            )

        # 1. Initialize Retriever (if enabled and not already initialized)
        if self.config.enable_rag and not self.retriever:
            self.retriever = get_code_retriever(repo_path)
            
        # 2. Retrieve Context (only if RAG is enabled)
        retrieved_content = ""
        if self.config.enable_rag and self.retriever:
            try:
                # Query with the first chunk of the diff to find relevant files
                docs = self.retriever.invoke(pr_diff[:2000])
                retrieved_content = "\n\n".join([d.page_content for d in docs])
            except Exception as e:
                print(f"Retrieval failed: {e}")
                
        # 3. Build Workflow
        workflow = StateGraph(AgentState)
        
        workflow.add_node("root_cause_analysis_agent", self.root_cause_analysis_agent)
        workflow.add_node("code_review_agent", self.code_review_agent)
        
        workflow.set_entry_point("root_cause_analysis_agent")
        workflow.add_edge("root_cause_analysis_agent", "code_review_agent")
        workflow.add_edge("code_review_agent", END)
        
        app = workflow.compile()
        
        # 4. Execute
        initial_state = {
            "pr_diff": pr_diff,
            "retrieved_content": retrieved_content,
            "messages": [],
            "root_cause_analysis": "",
            "code_review": "",
            "jira_ticket_description": None
        }
        
        result = app.invoke(initial_state)
        
        # Return the final code review
        return result["code_review"]
