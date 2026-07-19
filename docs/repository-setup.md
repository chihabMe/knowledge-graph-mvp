# Repository Setup

## Local Repository

This project should live at:

```text
/home/user/Documents/projects/knowledge-graph-mvp
```

Recommended branch naming:

```text
main
codex/<feature-name>
```

## GitHub Repository

Recommended settings:

- Private repository for now.
- Default branch: `main`.
- Require pull requests before changes reach `main`.
- Require the `Backend validation` GitHub Actions check.
- Require branches to be current before merging.
- Block force pushes and branch deletion on `main`.
- Do not commit `.env`.
- Do not commit Google service account credentials.
- Do not commit customer Drive exports or real customer files.

## Initial Git Commands

```bash
git init
git branch -M main
git add .
git commit -m "Initial project architecture scaffold"
gh repo create knowledge-graph-mvp --private --source=. --remote=origin --push
```

## Secrets

Store secrets in `.env` locally and in the deployment environment later.

Never commit:

- Google service account JSON
- OpenRouter API keys
- Django secret key
- SpiceDB preshared key
- Database passwords
- Customer documents
