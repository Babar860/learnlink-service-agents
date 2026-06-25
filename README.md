# LearnLink Agent Service

FastAPI service for LearnLink autonomous AI agents, including moderation, feed ranking, channel eligibility, recommendations, quiz conversion, key points, and grading.

## Run

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/uvicorn app.main:app --reload --port 5005
```

Default port: `5005`.

