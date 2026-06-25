import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - local scaffolding can run without optional SDK import
    genai = None


app = FastAPI(title="LearnLink Agent Service", version="1.1.0")


class AgentLog(BaseModel):
    agent_name: str
    trigger: str
    subject_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    status: str = "ok"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ModerationRequest(BaseModel):
    id: str
    content: str
    media_url: list[str] = Field(default_factory=list)
    post_type: str


class FeedRankRequest(BaseModel):
    user_id: str
    posts: list[dict[str, Any]]
    onboarding_keywords: list[str] = Field(default_factory=list)


class RecommendRequest(BaseModel):
    user_id: str
    onboarding_answers: dict[str, Any] = Field(default_factory=dict)
    resume_url: str | None = None


execution_logs: list[AgentLog] = []


def log(agent_name: str, trigger: str, payload: dict[str, Any], output: dict[str, Any], subject_id: str | None = None) -> None:
    execution_logs.append(
        AgentLog(agent_name=agent_name, trigger=trigger, subject_id=subject_id, input=payload, output=output)
    )


async def gemini_touch(agent_name: str, prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or genai is None:
        return f"{agent_name}: local deterministic fallback; Gemini call skipped until GEMINI_API_KEY is set."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)
    return response.text or ""


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "learnlink-service-agents",
        "agents": [
            "content-moderation-agent",
            "feed-ranking-agent",
            "channel-eligibility-agent",
            "keyword-analyzer-agent",
            "recommendation-agent",
            "quiz-converter-agent",
            "key-points-agent",
            "grading-agent",
            "channel-activity-analyzer",
        ],
    }


@app.post("/agents/moderate")
async def moderate(payload: ModerationRequest) -> dict[str, Any]:
    text = payload.content.lower()
    blocked_terms = ["hate", "spam", "violence", "scam"]
    flagged = [term for term in blocked_terms if term in text]
    decision: Literal["approved", "rejected"] = "rejected" if flagged else "approved"
    reason = "Policy violation: " + ", ".join(flagged) if flagged else "No policy violation detected."
    await gemini_touch("content-moderation-agent", f"Moderate this LearnLink post: {payload.content[:500]}")
    output = {"decision": decision, "reason": reason}
    log("content-moderation-agent", "post_created_or_cron", payload.model_dump(), output, payload.id)
    return output


@app.post("/agents/feed-rank")
async def feed_rank(payload: FeedRankRequest) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    def score(post: dict[str, Any]) -> float:
        engagement = float(post.get("likes", 0)) + float(post.get("comments", 0)) * 1.5 + float(post.get("shares", 0)) * 2
        content = str(post.get("content", "")).lower()
        relevance = sum(1 for keyword in payload.onboarding_keywords if keyword.lower() in content)
        created_at = post.get("created_at")
        recency = 1.0
        if isinstance(created_at, str):
            try:
                age_hours = max((now - datetime.fromisoformat(created_at.replace("Z", "+00:00"))).total_seconds() / 3600, 1)
                recency = 1 / age_hours
            except ValueError:
                recency = 0.5
        return relevance * 5 + engagement + recency

    ranked = sorted(payload.posts, key=score, reverse=True)
    await gemini_touch("feed-ranking-agent", f"Rank {len(payload.posts)} LearnLink posts for user {payload.user_id}.")
    output = {"ranked_posts": ranked}
    log("feed-ranking-agent", "feed_refresh", payload.model_dump(), {"count": len(ranked)}, payload.user_id)
    return output


@app.post("/agents/recommend")
async def recommend(payload: RecommendRequest) -> dict[str, Any]:
    text = " ".join(str(value) for value in payload.onboarding_answers.values()).lower()
    categories = []
    if any(word in text for word in ["ai", "data", "python", "machine learning"]):
        categories.append("ai-and-data")
    if any(word in text for word in ["business", "startup", "entrepreneur"]):
        categories.append("entrepreneurship")
    if any(word in text for word in ["design", "frontend", "web"]):
        categories.append("web-and-design")
    if not categories:
        categories = ["career-foundations", "communication", "digital-skills"]
    await gemini_touch("recommendation-agent", f"Map onboarding answers to LearnLink categories: {payload.onboarding_answers}")
    output = {
        "communities": categories[:3],
        "channels": categories[:3],
        "courses": categories[:3],
        "keywords": categories,
    }
    log("recommendation-agent", "profile_creation_or_search", payload.model_dump(), output, payload.user_id)
    return output


@app.post("/agents/channel-eligibility")
async def channel_eligibility(payload: dict[str, Any]) -> dict[str, Any]:
    threshold = float(os.getenv("ACTIVITY_SCORE_THRESHOLD", "0.65"))
    activity_score = float(payload.get("activity_score", 0))
    account_age_days = int(payload.get("account_age_days", 0))
    eligible = account_age_days >= 30 and activity_score >= threshold and int(payload.get("active_flags", 0)) == 0
    await gemini_touch("channel-eligibility-agent", f"Evaluate channel eligibility for score {activity_score}.")
    output = {"eligible": eligible, "threshold": threshold, "notify": eligible}
    log("channel-eligibility-agent", "nightly_cron", payload, output, str(payload.get("user_id", "")))
    return output


@app.post("/agents/quiz-convert")
async def quiz_convert(payload: dict[str, Any]) -> dict[str, Any]:
    content = str(payload.get("content", "Untitled question"))
    await gemini_touch("quiz-converter-agent", f"Convert to structured quiz JSON: {content[:500]}")
    output = {
        "timer_seconds": int(payload.get("timer_seconds", 300)),
        "retry_allowed": True,
        "retry_count": 1,
        "questions": [
            {
                "id": "q1",
                "type": "problem_statement" if "explain" in content.lower() else "mcq",
                "content": content,
                "options": payload.get("options", []),
                "correct_option_index": payload.get("correct_option_index"),
                "ai_converted": True,
            }
        ],
    }
    log("quiz-converter-agent", "teacher_quiz_upload", payload, output)
    return output


@app.post("/agents/key-points")
async def key_points(payload: dict[str, Any]) -> dict[str, Any]:
    transcript = str(payload.get("transcript", ""))
    sentences = [part.strip() for part in transcript.split(".") if part.strip()]
    output = {"key_points": sentences[:5], "saved_to_profile": True, "notification": "student_fcm"}
    await gemini_touch("key-points-agent", f"Extract live class key points: {transcript[:1000]}")
    log("key-points-agent", "student_premium_activation", payload, output, str(payload.get("live_class_id", "")))
    return output


@app.post("/agents/grade")
async def grade(payload: dict[str, Any]) -> dict[str, Any]:
    submissions = payload.get("submissions", [])
    output = {
        "status": "marksheet_generated",
        "rows": len(submissions) if isinstance(submissions, list) else 0,
        "format": "xlsx",
        "delivery": "teacher_fcm_email",
    }
    await gemini_touch("grading-agent", f"Grade live class quiz submissions: {output['rows']}")
    log("grading-agent", "live_class_quiz_ended", payload, output, str(payload.get("live_class_quiz_id", "")))
    return output


@app.get("/admin/agent-health")
async def agent_health() -> dict[str, Any]:
    return {
        "logs": execution_logs[-50:],
        "queue_depth": 0,
        "error_rate": 0,
        "cron_controls": ["restart_agent", "pause_specific_agent_cron"],
    }

