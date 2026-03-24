# Interview Prep Agent

An AI agent that automatically detects PM interviews on your Google Calendar and emails you a personalised preparation report every morning at 10:30 AM.

Built because researching a company, finding real interview questions, and mapping your experience to the role takes 1–2 hours of manual work before every interview. This does it automatically.

---

## What it does

1. Reads your Google Calendar every morning
2. Detects if there's a PM interview scheduled for today (keyword matching + external attendee check)
3. If an interview is found:
   - Identifies the company and interviewer from the calendar event
   - Searches Glassdoor, AmbitionBox, and Exponent for real PM interview questions at that company
   - Generates a personalised prep report grounded in your background
   - Emails you the report as a PDF
4. If no interview is found — exits silently, no email sent

---

## What's in the report

1. **Company Overview** — what they do, recent news, culture
2. **Competitive Landscape** — key competitors, market position
3. **Real Interview Questions** — verbatim questions from Glassdoor/AmbitionBox with suggested answer approaches
4. **Interview Preparation** — likely questions based on interview type, framed using your specific experience
5. **Questions to Ask** — tailored to each interviewer's role
6. **Key Talking Points** — how your background maps to this company's needs, how to address gaps
7. **Preparation Checklist** — 5–7 specific things to do before the interview

---

## Tech stack

- **Python 3.9+**
- **Anthropic Claude** (claude-sonnet-4-6) — company extraction, web search, report generation
- **Google Calendar API** — reads today's events
- **fpdf2** — generates formatted PDF report
- **Gmail SMTP** — sends the report to your inbox
- **GitHub Actions** — runs the agent at 10:30 AM IST daily (no server needed)

---

## How it detects interviews

Two conditions must both be true:
1. The event title or description contains a keyword (`interview`, `screening`, `product`, `call`, `exploratory`, etc.)
2. At least one attendee is from an external domain (not your own company email), OR the event has no attendees listed

This prevents internal team syncs from being flagged as interviews.

---

## Setup

### 1. Google Calendar API
- Go to [Google Cloud Console](https://console.cloud.google.com/)
- Create a project → enable **Google Calendar API**
- Create OAuth 2.0 credentials → download as `credentials.json`
- Run the script once locally to authenticate — this generates `token.pickle`

### 2. Gmail App Password
- Go to your Google Account → Security → 2-Step Verification → App Passwords
- Generate a password for "Mail"

### 3. Environment variables
```bash
export ANTHROPIC_API_KEY=your_key
export GMAIL_ADDRESS=your@gmail.com
export GMAIL_APP_PASSWORD=your_app_password
```

### 4. Run locally
```bash
pip install anthropic google-auth google-auth-oauthlib google-api-python-client fpdf2
python3 interview_prep_agent.py
```

---

## GitHub Actions (automated daily runs)

See `.github/workflows/daily_prep.yml`. The workflow runs at 10:30 AM IST (05:00 UTC) every day.

Secrets required in your GitHub repo settings:
- `ANTHROPIC_API_KEY`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `GOOGLE_TOKEN_B64` — base64-encoded contents of `token.pickle`
- `GOOGLE_CREDENTIALS_B64` — base64-encoded contents of `credentials.json`

---

## Customising for your own use

- **Candidate profile** — edit `CANDIDATE_PROFILE` in `interview_prep_agent.py` with your background, strengths, and gaps
- **Interview keywords** — edit the `interview_keywords` list in `identify_pm_interviews` to match how interviews are named in your calendar
- **Report sections** — edit the prompt in `generate_prep_report` to add, remove, or reorder sections
