"""
GitHub API client for PR operations.
Handles fetching PR details, posting comments, and setting statuses.
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from github import Github, GithubException, GithubIntegration, Auth
from github.PullRequest import PullRequest
from github.Repository import Repository


@dataclass
class PRFile:
    """Represents a file changed in a PR."""
    filename: str
    status: str  # added, modified, removed, renamed
    additions: int
    deletions: int
    changes: int
    patch: Optional[str]  # The diff patch
    raw_url: Optional[str]


@dataclass
class PRDetails:
    """Represents PR metadata."""
    number: int
    title: str
    body: Optional[str]
    author: str
    base_branch: str
    head_branch: str
    state: str
    files: List[PRFile]
    commits_count: int
    additions: int
    deletions: int




class GitHubClient:
    """Client for interacting with GitHub API."""
    
    def __init__(self, token: Optional[str] = None, app_id: Optional[int] = None, private_key: Optional[str] = None, installation_id: Optional[int] = None):
        """
        Initialize GitHub client.
        
        Args:
           token: Personal Access Token (legacy mode).
           app_id: GitHub App ID.
           private_key: GitHub App Private Key.
           installation_id: Installation ID for App mode.
        """
        if token:
            # PAT Mode
            auth = Auth.Token(token)
            self.github = Github(auth=auth)
        elif app_id and private_key and installation_id:
            # App Mode - Get installation token
            integration = GithubIntegration(app_id, private_key)
            access_token = integration.get_access_token(installation_id).token
            auth = Auth.Token(access_token)
            self.github = Github(auth=auth)
        else:
            raise ValueError("Must provide either 'token' or 'app_id', 'private_key', and 'installation_id'")
            
        self._repo: Optional[Repository] = None

    @classmethod
    def from_config(cls, config, installation_id: Optional[int] = None):
        """Factory method to create client from config."""
        if config.github_app_id and config.github_private_key:
             if not installation_id:
                 raise ValueError("Installation ID required for GitHub App mode")
             return cls(
                 app_id=config.github_app_id, 
                 private_key=config.github_private_key, 
                 installation_id=installation_id
             )
        else:
             return cls(token=config.github_token)
    
    def get_repo(self, repo_name: str) -> Repository:
        """
        Get a repository by name.
        
        Args:
            repo_name: Repository in format "owner/repo".
            
        Returns:
            Repository object.
        """
        self._repo = self.github.get_repo(repo_name)
        return self._repo
    
    def get_pr(self, repo_name: str, pr_number: int) -> PullRequest:
        """
        Get a pull request.
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            
        Returns:
            PullRequest object.
        """
        repo = self.get_repo(repo_name)
        return repo.get_pull(pr_number)
    
    def get_pr_details(self, repo_name: str, pr_number: int) -> PRDetails:
        """
        Get detailed PR information including files.
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            
        Returns:
            PRDetails object with all PR information.
        """
        pr = self.get_pr(repo_name, pr_number)
        
        # Get all changed files
        files = []
        for file in pr.get_files():
            files.append(PRFile(
                filename=file.filename,
                status=file.status,
                additions=file.additions,
                deletions=file.deletions,
                changes=file.changes,
                patch=file.patch,
                raw_url=file.raw_url
            ))
        
        return PRDetails(
            number=pr.number,
            title=pr.title,
            body=pr.body,
            author=pr.user.login,
            base_branch=pr.base.ref,
            head_branch=pr.head.ref,
            state=pr.state,
            files=files,
            commits_count=pr.commits,
            additions=pr.additions,
            deletions=pr.deletions
        )
    
    def get_file_content(self, repo_name: str, file_path: str, ref: str) -> str:
        """
        Get content of a file at a specific ref.
        
        Args:
            repo_name: Repository in format "owner/repo".
            file_path: Path to file in repo.
            ref: Branch or commit SHA.
            
        Returns:
            File content as string.
        """
        repo = self.get_repo(repo_name)
        content = repo.get_contents(file_path, ref=ref)
        return content.decoded_content.decode("utf-8")
    
    def post_review_comment(
        self,
        repo_name: str,
        pr_number: int,
        body: str,
        event: str = "COMMENT"
    ) -> None:
        """
        Post a review comment on a PR.
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            body: Comment body in markdown.
            event: Review event type (COMMENT, APPROVE, REQUEST_CHANGES).
        """
        pr = self.get_pr(repo_name, pr_number)
        pr.create_review(body=body, event=event)
    
    def post_issue_comment(
        self,
        repo_name: str,
        pr_number: int,
        body: str
    ) -> None:
        """
        Post a regular comment on a PR (not a review).
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            body: Comment body in markdown.
        """
        pr = self.get_pr(repo_name, pr_number)
        pr.create_issue_comment(body=body)
    
    def set_commit_status(
        self,
        repo_name: str,
        sha: str,
        state: str,
        description: str,
        context: str = "PR Review Bot"
    ) -> None:
        """
        Set commit status (check).
        
        Args:
            repo_name: Repository in format "owner/repo".
            sha: Commit SHA.
            state: Status state (pending, success, error, failure).
            description: Status description.
            context: Status context/name.
        """
        repo = self.get_repo(repo_name)
        commit = repo.get_commit(sha)
        commit.create_status(
            state=state,
            description=description[:140],  # Max 140 chars
            context=context
        )
    
    def get_pr_head_sha(self, repo_name: str, pr_number: int) -> str:
        """
        Get the HEAD commit SHA of a PR.
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            
        Returns:
            HEAD commit SHA.
        """
        pr = self.get_pr(repo_name, pr_number)
        return pr.head.sha
    
    def approve_pr(self, repo_name: str, pr_number: int, body: str) -> None:
        """
        Approve a PR.
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            body: Approval message.
        """
        self.post_review_comment(repo_name, pr_number, body, event="APPROVE")
    
    def request_changes(self, repo_name: str, pr_number: int, body: str) -> None:
        """
        Request changes on a PR.
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            body: Review message explaining required changes.
        """
        self.post_review_comment(repo_name, pr_number, body, event="REQUEST_CHANGES")
