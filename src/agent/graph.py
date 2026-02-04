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
        
        task_description = state.get("task_description", "")
        
        system_prompt = """You are a Senior Software Architect performing a Root Cause Analysis.
        Analyze the provided PR Diff and Retrieved Code Context.
        
        Determine:
        1. The intent of the changes (Refactor, Feature, specific Bug Fix).
        2. Potential architectural risks or mismatches.
        3. Why existing code was modified (Root Cause).
        
        CRITICAL TASK VERIFICATION:
        The user has provided a specific task/requirement for this PR.
        You MUST verify if the code changes align with this task.
        Task: {task_description}
        
        If the code contradicts or misses the task, explicitly state this in your analysis.
        
        Provide a concise analysis including the task verification."""
        
        if not retrieved_content:
            context_str = "No existing code context retrieved."
        else:
            context_str = f"Related Codebase Context:\n{retrieved_content}"
            
        final_prompt = system_prompt.format(task_description=task_description if task_description else "No specific task provided.")

        messages = [
            SystemMessage(content=final_prompt),
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
    
    def test_generation_agent(self, state: AgentState):
        """
        Agent Node: Generates unit tests based on the PR diff and analysis.
        """
        print("--- Test Generation ---")
        pr_diff = state.get("pr_diff", "")
        rca = state.get("root_cause_analysis", "")
        retrieved_content = state.get("retrieved_content", "")
        task_description = state.get("task_description", "")
        
        system_prompt = """You are an Expert QA Automation Engineer.
        Your goal is to generate robust Unit Tests (using pytest) for the code changes.
        
        Input Context:
        1. PR Diff (The changes)
        2. Root Cause Analysis (The intent)
        3. Task Description (The requirements)
        
        Instructions:
        - Write valid Python code.
        - Use `pytest` syntax.
        - Mock external dependencies where appropriate.
        - Focus on testing the CHANGED logic and edge cases.
        - Do NOT include long explanations, just the code block.
        
        If no code changes require tests (e.g. documentation update, configuration), return "NO_TESTS_NEEDED".
        """
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Task: {task_description}\n\nRCA: {rca}\n\nCode Context:\n{retrieved_content}\n\nPR Diff:\n{pr_diff}")
        ]
        
        response = self.model.invoke(messages)
        
        return {
            "generated_tests": response.content,
            "messages": [response]
        }
        
    def review_pr(self, pr_diff: str, repo_path: str, model_name: Optional[str] = None, task_description: Optional[str] = None) -> str:
        """
        Public entry point to review a PR.
        
        Args:
            pr_diff: The diff string of the PR.
            repo_path: Path to local repository for retrieval.
            model_name: Optional model name to use for this review.
            task_description: Optional description of the task/requirements to verify against.
            
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
        workflow.add_node("test_generation_agent", self.test_generation_agent)
        
        workflow.set_entry_point("root_cause_analysis_agent")
        workflow.add_edge("root_cause_analysis_agent", "code_review_agent")
        workflow.add_edge("root_cause_analysis_agent", "test_generation_agent")
        workflow.add_edge("code_review_agent", END)
        workflow.add_edge("test_generation_agent", END)
        
        app = workflow.compile()
        
        # 4. Execute
        initial_state = {
            "pr_diff": pr_diff,
            "retrieved_content": retrieved_content,
            "messages": [],
            "root_cause_analysis": "",
            "code_review": "",
            "jira_ticket_description": None,
            "task_description": task_description,
            "generated_tests": ""
        }
        
        result = app.invoke(initial_state)
        
        # Return the final code review and generated tests (we package into a dict for the service layer)
        # Note: The original signature returns str (code_review), but we want to pass more data.
        # We will package it as a JSON string to maintain backward interface compatibility if needed, 
        # OR we modify the return type. For minimal invasion, let's return a dict structure as string.
        
        return json.dumps({
            "code_review": result.get("code_review", ""),
            "generated_tests": result.get("generated_tests", "")
        })
