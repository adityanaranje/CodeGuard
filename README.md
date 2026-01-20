# GitHub PR Review System

An automated PR review system that uses LLM (Groq) to review code changes and enforce company rules before merging.

## Features

- 🔍 **Automated Code Review** - LLM-powered analysis of code changes
- 📋 **Rule Enforcement** - Check against company coding standards
- 💬 **PR Comments** - Automatic feedback posted to GitHub PRs
- ✅ **Merge Control** - Pass/fail status based on review results

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required variables:
- `GITHUB_TOKEN` - GitHub Personal Access Token with repo permissions
- `GITHUB_WEBHOOK_SECRET` - Secret for webhook verification
- `GROQ_API_KEY` - Your Groq API key

### 📊 Dashboard
The bot includes a built-in dashboard to visualize review statistics and logs.
- **URL**: `/dashboard` (e.g., `https://your-app-url.onrender.com/dashboard`)
- **Features**:
  - Total reviews count
  - Average severity score
  - Verdict distribution chart
  - Recent review logs table

### 3. Configure Company Rules

Edit `config/rules.yaml` to define your coding standards.

## Usage

### CLI Mode (Testing)

Review a specific PR:

```bash
python src/main.py --repo owner/repo --pr 123
```

### Webhook Mode (Production)

Start the webhook server:

```bash
python src/main.py --webhook --port 5000
```

Configure your GitHub repository webhook to point to:
`https://your-server.com/webhook`

## Project Structure

```
├── config/
│   └── rules.yaml          # Company rules configuration
├── src/
│   ├── main.py             # Entry point
│   ├── github_client.py    # GitHub API interactions
│   ├── diff_parser.py      # Parse PR diffs
│   ├── rule_checker.py     # Validate against rules
│   ├── llm_reviewer.py     # Groq LLM integration
│   ├── decision_engine.py  # Pass/fail logic
│   └── config_loader.py    # Configuration management
└── templates/
    └── review_comment.md   # PR comment template
```

## License

MIT
