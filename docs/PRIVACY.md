# Privacy

What the JobHunt bot stores, why, and who else sees it.

## What is stored

- **Telegram identity:** user id, chat id, first name and username, to send
  messages and greet you.
- **Settings:** tracks, experience level, where you can work, preferred cities,
  job types, minimum score, delivery mode and quiet hours, to choose your jobs.
- **Skills and education:** typed in the bot or read from a CV you chose to
  send. Only the structured result is kept; the CV text is deleted after
  parsing. Telegram's file id for the CV is kept so it can be parsed again.
- **Matches and feedback:** the jobs matched for you, their scores and
  explanations, and what you marked applied, saved, hidden, 👍 or 👎.

Nothing else: no phone numbers, no message history.

## Who else processes data

- **Job postings** (public) are read by Google Gemini and Groq to extract their
  facts. No user data is sent with them.
- **CVs** go only to Groq, and only after you agree in the bot. Groq does not
  keep inference inputs by default and does not train on them
  ([Groq: your data](https://console.groq.com/docs/your-data)). CVs are never
  sent to Gemini's free tier, whose inputs may be used to improve Google's
  products ([Gemini API terms](https://ai.google.dev/terms)).
- **Infrastructure:** Telegram delivers messages, Cloudflare hosts the bot and
  the database, and GitHub Actions runs the pipeline.

Data is never sold or shared for any other purpose.

## Your controls

- `/export` sends you everything stored about you as a JSON file.
- `/delete` removes your user record and, through the database's foreign keys,
  your settings, profile, matches, notifications and feedback.
- `/pause` stops alerts; `/resume` starts them again.

## Retention

Job descriptions are cleared 60 days after a job was last seen; extracted job
facts are kept. User data is kept until you delete it.
