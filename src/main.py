"""
Main entry point for the GitHub PR Review System.
Supports both CLI mode and webhook server mode.
"""

import argparse
import hashlib
import hmac
import json
import sys
import logging
from typing import Optional

from flask import Flask, request, jsonify, render_template

from flask import Flask, request, jsonify, render_template

from src.data import db
from src.config import get_config, Config
from src.services.github import GitHubClient, PRFile
from src.review.parser import DiffParser, FileDiff
from src.review.rules import RuleChecker
from src.review.service import LLMReviewer
from src.review.decision import DecisionEngine, Verdict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
with app.app_context():
    db.init_db()


@app.route("/", methods=["GET", "POST"])
def index():
    """Root endpoint for simple uptime check."""
    if request.method == "POST":
         return "❌ Error: You sent a POST request to '/'. Did you mean to send it to '/webhook'? Check your GitHub App settings!", 404
    return "GitHub PR Review Bot is running! 🚀\n\nConfigure your Webhook URL to: <url>/webhook", 200


class PRReviewBot:
    """Main PR review bot orchestrator."""
    
    def __init__(self, config: Optional[Config] = None, installation_id: Optional[int] = None):
        """
        Initialize the review bot.
        
        Args:
            config: Optional config object.
            installation_id: GitHub App Installation ID (required for App mode).
        """
        self.config = config or get_config()
        self.github_client = GitHubClient.from_config(self.config, installation_id)
        self.diff_parser = DiffParser()
        self.rule_checker = RuleChecker(self.config)
        self.llm_reviewer = LLMReviewer(self.config)
        self.decision_engine = DecisionEngine(self.config)
    
    def review_pr(
        self,
        repo_name: str,
        pr_number: int,
        post_comment: bool = True
    ) -> dict:
        """
        Review a pull request.
        
        Args:
            repo_name: Repository in format "owner/repo".
            pr_number: PR number.
            post_comment: Whether to post the review comment to GitHub.
            
        Returns:
            Dict with review results.
        """
        logger.info(f"🔍 Reviewing PR #{pr_number} in {repo_name}...")
        
        # 1. Fetch PR details
        logger.info("  📥 Fetching PR details...")
        pr_details = self.github_client.get_pr_details(repo_name, pr_number)
        
        logger.info(f"  📄 Found {len(pr_details.files)} changed files "
              f"(+{pr_details.additions}/-{pr_details.deletions})")
        
        # 2. Parse diffs
        logger.info("  🔧 Parsing diffs...")
        diffs = []
        for file in pr_details.files:
            diff = self.diff_parser.parse_patch(
                patch=file.patch or "",
                filename=file.filename,
                status=file.status
            )
            diffs.append(diff)
        
        # 3. Check rules
        logger.info("  📋 Checking company rules...")
        violations = self.rule_checker.check_all_rules(diffs)
        logger.info(f"  ⚠️ Found {len(violations)} rule violation(s)")
        
        # 4. LLM review
        logger.info("  🤖 Running LLM code review...")
        llm_review = self.llm_reviewer.review(
            diffs=diffs,
            pr_title=pr_details.title,
            pr_body=pr_details.body
        )
        logger.info(f"  📊 LLM severity score: {llm_review.severity_score}/10")
        
        # 5. Make decision
        logger.info("  ⚖️ Making decision...")
        decision = self.decision_engine.decide(
            violations=violations,
            llm_review=llm_review,
            rule_checker=self.rule_checker
        )
        logger.info(f"  {decision.verdict.emoji} Verdict: {decision.verdict.value}")
        
        # 6. Format comment
        comment = self.decision_engine.format_comment(
            decision=decision,
            repo_name=repo_name,
            pr_number=pr_number,
            pr_title=pr_details.title,
            pr_author=pr_details.author
        )
        
        # 7. Post to GitHub if requested
        if post_comment:
            logger.info("  💬 Posting review comment...")
            try:
                # Post as PR review with appropriate event
                if decision.verdict == Verdict.PASS:
                    self.github_client.approve_pr(repo_name, pr_number, comment)
                elif decision.verdict == Verdict.FAIL:
                    self.github_client.request_changes(repo_name, pr_number, comment)
                else:
                    self.github_client.post_issue_comment(repo_name, pr_number, comment)
                
                # Set commit status
                head_sha = self.github_client.get_pr_head_sha(repo_name, pr_number)
                self.github_client.set_commit_status(
                    repo_name=repo_name,
                    sha=head_sha,
                    state=decision.verdict.github_status,
                    description=decision.reasons[0] if decision.reasons else "Review complete",
                    context="PR Review Bot"
                )
                logger.info("  ✅ Review posted successfully!")
            except Exception as e:
                logger.error(f"  ❌ Failed to post review: {e}")
        
        return {
            "verdict": decision.verdict.value,
            "violations_count": len(violations),
            "llm_severity": llm_review.severity_score,
            "reasons": decision.reasons,
            "comment": comment
        }


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    if not signature or not secret:
        return False
    
    expected_sig = "sha256=" + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_sig, signature)


@app.route("/webhook", methods=["GET", "POST"])
def webhook_handler():
    """Handle GitHub webhook events."""
    if request.method == "GET":
        return "❌ Error: This endpoint expects a POST request from GitHub. You cannot visit it in a browser.", 405

    logger.info("📩 Webhook received")
    config = get_config()
    
    # Verify signature if secret is configured
    if config.github_webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_webhook_signature(
            request.get_data(),
            signature,
            config.github_webhook_secret
        ):
            logger.warning("❌ Invalid webhook signature")
            return jsonify({"error": "Invalid signature"}), 401
    
    event_type = request.headers.get("X-GitHub-Event", "")
    logger.info(f"Event type: {event_type}")
    
    payload = request.get_json()
    
    if not payload:
        return jsonify({"error": "Invalid JSON payload"}), 400

    # Only handle pull_request events
    if event_type != "pull_request":
        logger.info(f"Ignoring event: {event_type}")
        return jsonify({"message": f"Ignoring event: {event_type}"}), 200
    
    action = payload.get("action", "")
    logger.info(f"Action: {action}")
    
    # Only review on opened or synchronize (new commits)
    if action not in ("opened", "synchronize", "reopened"):
        logger.info(f"Ignoring action: {action}")
        return jsonify({"message": f"Ignoring action: {action}"}), 200
    
    # Extract PR info
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})
    
    repo_name = repo.get("full_name")
    pr_number = pr.get("number")
    installation_id = installation.get("id")
    
    logger.info(f"Processing PR #{pr_number} for {repo_name} (Installation: {installation_id})")
    
    if not repo_name or not pr_number:
        logger.error("Missing repo or PR info in payload")
        return jsonify({"error": "Missing repo or PR info"}), 400
    
    # Run review
    try:
        # Pass installation_id if available (for App mode)
        bot = PRReviewBot(config, installation_id=installation_id)
        result = bot.review_pr(repo_name, pr_number, post_comment=True)
        
        # Log to DB
        db.log_review(
            repo_name=repo_name,
            pr_number=pr_number,
            author=pr.get("user", {}).get("login", "unknown"),
            verdict=result["verdict"],
            violations_count=result["violations_count"],
            llm_severity=result["llm_severity"],
            comment=result["comment"],
            total_changes=pr.get("additions", 0) + pr.get("deletions", 0),
            files_changed=pr.get("changed_files", 0)
        )
        
        logger.info("✅ Review completed successfully")
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy"}), 200


@app.route("/dashboard")
def dashboard():
    """Render dashboard page."""
    return render_template("dashboard.html")


@app.route("/api/stats")
def api_stats():
    """Get stats for dashboard."""
    repo = request.args.get('repo')
    author = request.args.get('author')
    
    stats = db.get_stats(repo, author)
    reviews = db.get_recent_reviews(repo=repo, author=author)
    return jsonify({"stats": stats, "reviews": reviews})


@app.route("/api/filters")
def api_filters():
    """Get filter options."""
    return jsonify(db.get_filter_options())


def run_cli(repo: str, pr: int, no_post: bool = False):
    """Run review in CLI mode."""
    try:
        bot = PRReviewBot()
        result = bot.review_pr(repo, pr, post_comment=not no_post)
        
        if no_post:
            print("\n" + "=" * 60)
            print("REVIEW COMMENT (not posted):")
            print("=" * 60)
            print(result["comment"])
        
        return 0 if result["verdict"] in ("PASS", "NEEDS_REVIEW") else 1
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="GitHub PR Review Bot - Automated code review with LLM"
    )
    
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="Run as webhook server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port for webhook server (default: 5000)"
    )
    parser.add_argument(
        "--repo",
        type=str,
        help="Repository in format 'owner/repo' for CLI mode"
    )
    parser.add_argument(
        "--pr",
        type=int,
        help="PR number to review for CLI mode"
    )
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="Don't post comment to GitHub (CLI mode only)"
    )
    
    args = parser.parse_args()
    
    if args.webhook:
        # Run as webhook server
        print(f"🚀 Starting PR Review Bot webhook server on port {args.port}...")
        app.run(host="0.0.0.0", port=args.port, debug=False)
    elif args.repo and args.pr:
        # CLI mode
        sys.exit(run_cli(args.repo, args.pr, args.no_post))
    else:
        parser.print_help()
        print("\nExamples:")
        print("  # Review a specific PR")
        print("  python -m src.main --repo owner/repo --pr 123")
        print("")
        print("  # Review without posting to GitHub")
        print("  python -m src.main --repo owner/repo --pr 123 --no-post")
        print("")
        print("  # Run as webhook server")
        print("  python -m src.main --webhook --port 5000")
        sys.exit(1)


if __name__ == "__main__":
    main()
