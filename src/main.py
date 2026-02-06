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
        
        # 3.5. Generate PR description if empty
        generated_description = None
        if not pr_details.body or pr_details.body.strip() == "":
            logger.info("  📝 PR body is empty. Generating description...")
            try:
                generated_description = self.llm_reviewer.generate_pr_description(diffs, pr_details.title)
                logger.info("  ✅ Description generated successfully")
                # Note: We can't update the PR via GitHub API in this flow without additional permissions
                # So we'll include it in the review comment instead
            except Exception as e:
                logger.warning(f"  ⚠️ Failed to generate description: {e}")
        
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
            pr_author=pr_details.author,
            generated_description=generated_description
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
                
                # Post inline comments if there are any issues with file/line info
                inline_comments = self.llm_reviewer.get_inline_comments(llm_review)
                if inline_comments:
                    logger.info(f"  💬 Posting {len(inline_comments)} inline comment(s)...")
                    try:
                        self.github_client.post_inline_comments(
                            repo_name=repo_name,
                            pr_number=pr_number,
                            comments=inline_comments,
                            body="Code suggestions with fixes",
                            event="COMMENT"
                        )
                        logger.info("  ✅ Inline comments posted successfully!")
                    except Exception as e:
                        logger.warning(f"  ⚠️ Failed to post inline comments: {e}")
                
                # Auto-commit fixes if enabled
                if self.config.enable_auto_fix_commits and llm_review.issues:
                    logger.info("  🔧 Auto-commit fixes enabled. Applying fixes to PR branch...")
                    fixes_to_apply = []
                    total_fix_lines = 0
                    
                    for issue in llm_review.issues:
                        if issue.get('fixed_code') and issue.get('file'):
                            # Count lines in the fix
                            fix_line_count = len(issue['fixed_code'].split('\n'))
                            total_fix_lines += fix_line_count
                            
                            # Get current file content
                            try:
                                current_content = self.github_client.get_file_content(
                                    repo_name=repo_name,
                                    file_path=issue['file'],
                                    ref=pr_details.head_branch
                                )
                                
                                # Apply the fix (simple replacement for now)
                                # TODO: More sophisticated patching logic
                                fixes_to_apply.append({
                                    "file": issue['file'],
                                    "content": issue['fixed_code'],
                                    "description": issue.get('description', 'Auto-fix')
                                })
                            except Exception as e:
                                logger.warning(f"  ⚠️ Could not prepare fix for {issue['file']}: {e}")
                    
                    # Safety check: Don't auto-commit if changes are too large
                    if total_fix_lines > 100:
                        logger.warning(f"  ⚠️ Auto-commit skipped: Changes too large ({total_fix_lines} lines > 100 line limit)")
                        logger.info("  💡 Fixes are available in inline comments for manual review")
                    elif fixes_to_apply:
                        try:
                            committed_files = self.github_client.apply_fixes_to_pr(
                                repo_name=repo_name,
                                pr_number=pr_number,
                                fixes=fixes_to_apply
                            )
                            logger.info(f"  ✅ Auto-committed fixes to {len(committed_files)} file(s) ({total_fix_lines} lines)")
                            
                            # Post comment about auto-fixes
                            fix_comment = f"🤖 **Auto-fixes applied!**\n\nI've automatically committed fixes to the following files:\n"
                            for file in committed_files:
                                fix_comment += f"- `{file}`\n"
                            fix_comment += f"\n**Total changes:** {total_fix_lines} lines\n"
                            fix_comment += "\nPlease review the changes and let me know if you need any adjustments!"
                            
                            self.github_client.post_issue_comment(repo_name, pr_number, fix_comment)
                        except Exception as e:
                            logger.error(f"  ❌ Failed to auto-commit fixes: {e}")
                
                
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

    # Handle issue_comment events for slash commands
    if event_type == "issue_comment":
        action = payload.get("action", "")
        if action == "created":
            comment_body = payload.get("comment", {}).get("body", "").strip().lower()
            issue = payload.get("issue", {})
            
            # Check if it's a PR (issues and PRs share the same API)
            if "pull_request" in issue:
                pr_number = issue.get("number")
                repo = payload.get("repository", {})
                repo_name = repo.get("full_name")
                installation = payload.get("installation", {})
                installation_id = installation.get("id")
                commenter = payload.get("comment", {}).get("user", {}).get("login", "")
                
                logger.info(f"Comment from {commenter} on PR #{pr_number}: {comment_body}")
                
                # Process slash commands
                if comment_body.startswith("/approve"):
                    logger.info(f"Approving PR #{pr_number}...")
                    try:
                        bot = PRReviewBot(config, installation_id=installation_id)
                        bot.github_client.approve_pr(repo_name, pr_number, "✅ Approved by maintainer command")
                        return jsonify({"message": "PR approved"}), 200
                    except Exception as e:
                        logger.error(f"Failed to approve: {e}")
                        return jsonify({"error": str(e)}), 500
                
                elif comment_body.startswith("/reject"):
                    logger.info(f"Rejecting PR #{pr_number}...")
                    try:
                        bot = PRReviewBot(config, installation_id=installation_id)
                        bot.github_client.request_changes(repo_name, pr_number, "❌ Changes requested by maintainer command")
                        return jsonify({"message": "PR rejected"}), 200
                    except Exception as e:
                        logger.error(f"Failed to reject: {e}")
                        return jsonify({"error": str(e)}), 500
                
                elif comment_body.startswith("/request-changes"):
                    logger.info(f"Requesting changes on PR #{pr_number}...")
                    try:
                        bot = PRReviewBot(config, installation_id=installation_id)
                        bot.github_client.request_changes(repo_name, pr_number, "🔄 Changes requested by maintainer command")
                        return jsonify({"message": "Changes requested"}), 200
                    except Exception as e:
                        logger.error(f"Failed to request changes: {e}")
                        return jsonify({"error": str(e)}), 500
        
        return jsonify({"message": "Comment processed"}), 200

    # Only handle pull_request events for reviews
    if event_type != "pull_request":
        logger.info(f"Ignoring event: {event_type}")
        return jsonify({"message": f"Ignoring event: {event_type}"}), 200
    
    action = payload.get("action", "")
    logger.info(f"Action: {action}")
    
    # Only review on opened or synchronize (new commits)
    # Only review on opened or synchronize (new commits)
    # Also handle 'closed' for merge tracking
    if action not in ("opened", "synchronize", "reopened", "closed"):
        logger.info(f"Ignoring action: {action}")
        return jsonify({"message": f"Ignoring action: {action}"}), 200
    
    # Extract PR info
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})
    
    repo_name = repo.get("full_name")
    pr_number = pr.get("number")
    installation_id = installation.get("id")
    
    # Handle merge event (Action: closed + merged: true)
    if action == "closed" and pr.get("merged"):
        merged_by = pr.get("merged_by", {}).get("login", "unknown")
        logger.info(f"PR #{pr_number} merged by {merged_by}. Checking for overrides...")
        
        try:
            was_overridden = db.track_merge(repo_name, pr_number, merged_by)
            if was_overridden:
                logger.warning(f"🚨 ALERT: PR #{pr_number} was merged despite bot failure!")
            return jsonify({"message": "Merge tracked", "overridden": was_overridden}), 200
        except Exception as e:
            logger.error(f"Failed to track merge: {e}")
            return jsonify({"error": str(e)}), 500

    # For reviews, skip if action is 'closed'
    if action == "closed":
         return jsonify({"message": "PR closed (not merged or already handled)"}), 200

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
    
    stats = db.get_stats(
        repo=repo if repo != "all" else None,
        author=author if author != "all" else None
    )
    
    daily_stats = db.get_daily_stats(
        repo=repo if repo != "all" else None,
        author=author if author != "all" else None
    )
    
    override_stats = db.get_override_stats(
        repo=repo if repo != "all" else None
    )
    
    # Get recent reviews for activity feed
    reviews = db.get_recent_reviews(
        repo=repo if repo != "all" else None,
        author=author if author != "all" else None
    )
    
    return jsonify({
        "stats": stats,
        "trend_data": daily_stats,  # Frontend expects "trend_data"
        "override_stats": override_stats,
        "reviews": reviews          # Frontend expects "reviews"
    })


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
