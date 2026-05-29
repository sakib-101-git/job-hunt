import requests
from src.utils import env


def send(message: str):
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
    resp.raise_for_status()


def notify_new_jobs(jobs: list[dict], min_score: int = 70):
    high_score = [j for j in jobs if (j.get("score") or 0) >= min_score]
    if not high_score:
        return
    lines = [f"*{len(high_score)} new jobs matched:*"]
    for j in high_score[:10]:
        lines.append(f"• [{j['title']} @ {j['company']}]({j['url']}) — score: {j['score']}")
    send("\n".join(lines))


def notify_cv_ready(job: dict):
    send(f"CV ready for *{job['title']}* at *{job['company']}*")
