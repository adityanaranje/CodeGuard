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
        
        Input Format:
        The PR Diff now includes line numbers at the start of each line (e.g., "12: + code"). 
        Use these EXACT line numbers when reporting issues.
        
        Output Format:
        You MUST return your review wrapped in a ```json markdown block:
        ```json
        {
            "summary": "...",
            "severity_score": int (1-10),
            "issues": [
                {
                    "type": "bug|security|style", 
                    "file": "...", 
                    "line": int, 
                    "description": "...", 
                    "suggestion": "...",
                    "fixed_code": "actual corrected code snippet"
                }
            ],
            "suggestions": ["..."],
            "security_concerns": ["..."],
            "positive_feedback": ["..."]
        }
        ```

        
        IMPORTANT:
        - For each issue, provide the 'fixed_code' field with the actual corrected code that can replace the problematic code.
        - DO NOT suggest changes that are ALREADY PRESENT in the PR Diff. 
          Example: If the diff shows a variable rename from 'contents' to 'content', do NOT suggest renaming it again.
          Only report issues if the NEW code in the diff is still incorrect.
        
        PRODUCTION-GRADE SUGGESTIONS:
        1. Give the BEST fix FIRST: Do not provide a "patch" that will need a follow-up fix. 
           Example: If there is a potential DivisionByZero, suggest a robust check (e.g., `if c != 0: ...`) rather than just setting `c = 1`.
        2. Defensive Programming: Prefer input validation, type checks, and error handling over simple value adjustments.
        3. Future-Proofing: Consider how the code might fail in other edge cases, not just the one obvious bug.
        
        NOISE REDUCTION RULES:
        1. If you find a Logic Bug (e.g., empty loop `range(-1)`), do NOT also flag "Unused Variable" or "Unclear Purpose" for code inside that dead block. Report only the primary Logic Bug.
        2. Consolidate related feedback. Don't leave 3 comments on 3 consecutive lines if they stem from the same issue.
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
        primary_language = state.get("primary_language", "python")
        
        # Map languages to test frameworks
        test_frameworks = {
            "python": {"name": "pytest", "example": "pytest"},
            "javascript": {"name": "Jest", "example": "jest"},
            "typescript": {"name": "Jest", "example": "jest"},
            "java": {"name": "JUnit", "example": "JUnit 5"},
            "go": {"name": "testing package", "example": "go test"},
            "rust": {"name": "cargo test", "example": "cargo test"},
            "ruby": {"name": "RSpec", "example": "RSpec"},
            "php": {"name": "PHPUnit", "example": "PHPUnit"},
            "csharp": {"name": "xUnit", "example": "xUnit"},
            "kotlin": {"name": "JUnit", "example": "JUnit 5"},
            "swift": {"name": "XCTest", "example": "XCTest"},
            "scala": {"name": "ScalaTest", "example": "ScalaTest"},
        }
        
        framework = test_frameworks.get(primary_language, {"name": "pytest", "example": "pytest"})
        
        system_prompt = f"""You are an Expert QA Automation Engineer.
        Your goal is to generate robust Unit Tests for the code changes.
        
        Input Context:
        1. PR Diff (The changes)
        2. Root Cause Analysis (The intent)
        3. Task Description (The requirements)
        4. Language: {primary_language}
        
        Instructions:
        - Write valid {primary_language} code.
        - Use `{framework['name']}` syntax for testing.
        - Mock external dependencies where appropriate.
        - Focus on testing the CHANGED logic and edge cases.
        - Do NOT include long explanations, just the code block.
        - Return ONLY the test code without markdown formatting.
        
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
        
    def review_pr(self, pr_diff: str, repo_path: str, model_name: Optional[str] = None, task_description: Optional[str] = None, primary_language: Optional[str] = None, skip_test_generation: bool = False) -> str:
        """
        Public entry point to review a PR.
        
        Args:
            pr_diff: The diff string of the PR.
            repo_path: Path to local repository for retrieval.
            model_name: Optional model name to use for this review.
            task_description: Optional description of the task/requirements to verify against.
            primary_language: Primary programming language detected from the PR.
            skip_test_generation: If True, skip test generation even if enabled.
            
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
        
        # Phase 2: Conditional test generation
        # Only add test generation node if enabled in config AND not skipped for this PR
        should_generate_tests = self.config.enable_test_generation and not skip_test_generation
        if should_generate_tests:
            workflow.add_node("test_generation_agent", self.test_generation_agent)
        
        workflow.set_entry_point("root_cause_analysis_agent")
        workflow.add_edge("root_cause_analysis_agent", "code_review_agent")
        
        # Only enable test generation path if configured
        if should_generate_tests:
            workflow.add_edge("root_cause_analysis_agent", "test_generation_agent")
            workflow.add_edge("test_generation_agent", END)
        
        workflow.add_edge("code_review_agent", END)
        
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
            "generated_tests": "",
            "primary_language": primary_language or "python"
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
