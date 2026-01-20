# GitHub PR Review Bot - Review Summary

## 🔍 Automated Code Review

**Repository:** {{ repo_name }}
**Pull Request:** #{{ pr_number }} - {{ pr_title }}
**Author:** @{{ pr_author }}

---

## 📋 Rule Check Results

{% if rule_violations %}
### ⚠️ Violations Found ({{ rule_violations|length }})

| Severity | Rule | File | Line | Message |
|----------|------|------|------|---------|
{% for v in rule_violations %}
| {{ v.severity_emoji }} {{ v.severity }} | {{ v.rule_name }} | `{{ v.file }}` | {{ v.line }} | {{ v.message }} |
{% endfor %}
{% else %}
### ✅ No Rule Violations

All company coding standards have been met!
{% endif %}

---

## 🤖 LLM Code Review

{{ llm_review }}

---

## 📊 Final Verdict

| Metric | Value |
|--------|-------|
| Rule Violations | {{ rule_violations|length }} |
| LLM Severity Score | {{ llm_severity }}/10 |
| **Status** | {{ final_status_emoji }} **{{ final_status }}** |

{% if final_status == "FAIL" %}
> ⛔ **This PR requires changes before it can be merged.**
{% elif final_status == "NEEDS_REVIEW" %}
> ⚠️ **This PR requires manual review before merging.**
{% else %}
> ✅ **This PR is approved for merging.**
{% endif %}

---

<sub>🤖 Automated review by GitHub PR Review Bot | Powered by Groq LLM</sub>
