#!/usr/bin/env python3
"""
AI Interview Preparation Agent
Automatically identifies upcoming PM interviews and generates comprehensive prep reports
"""

import os
import json
import re
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
from typing import List, Dict
import anthropic
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Google Calendar API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle

# ─────────────────────────────────────────────────────────────────────────────
# CANDIDATE PROFILE
# Edit this section to reflect your background. The more specific, the better
# the prep reports will be tailored to you.
# ─────────────────────────────────────────────────────────────────────────────

CANDIDATE_PROFILE = """
CANDIDATE BACKGROUND:
- Total experience: ~6.5 years (~4 years in Product Management, ~2.5 years in Analytics)
- Companies: Meesho, Orange Health Labs
- Built and scaled consumer-facing, data-driven products

KEY STRENGTHS:
- Strong analytical thinking and problem-solving, especially in ML/AI-driven products
- Led 0→1 and scale initiatives in personalisation, recommendations, and conversational AI
- Strong focus on improving user experience and business metrics through experimentation
- Good at structuring ambiguous problems and translating user behaviour into product decisions
- Experience with A/B testing, data-driven decision making, and cross-functional collaboration

TARGET ROLES:
- Product Manager / Senior Product Manager
- Preferred: consumer tech or AI-first companies
- Open to: India and MENA region
- Interested in: high-impact user problems, AI-led products

GAPS TO ADDRESS PROACTIVELY:
- Limited B2B SaaS or enterprise workflow experience — if the role has B2B elements, address this by highlighting transferable skills (data thinking, cross-functional work, stakeholder management)
- Would like to go deeper into AI/ML infrastructure and system design over time — frame as a growth area, not a weakness
"""

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
CLAUDE_MODEL = "claude-sonnet-4-6"


class InterviewPrepAgent:
    """Main agent class for interview preparation automation"""

    def __init__(self, anthropic_api_key: str):
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.calendar_service = None

    def authenticate_google_calendar(self, credentials_file: str = 'credentials.json'):
        """Authenticate with Google Calendar API"""
        creds = None

        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(credentials_file):
                    raise FileNotFoundError(
                        f"Please make sure '{credentials_file}' is in the same folder as this script"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)

            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)

        self.calendar_service = build('calendar', 'v3', credentials=creds)
        print("✅ Successfully authenticated with Google Calendar\n")

    def get_calendar_events(self) -> List[Dict]:
        """Fetch calendar events for today only"""
        if not self.calendar_service:
            raise RuntimeError("Calendar service not authenticated")

        today = datetime.now().date()
        time_min = datetime(today.year, today.month, today.day, 0, 0, 0).astimezone().isoformat()
        time_max = datetime(today.year, today.month, today.day, 23, 59, 59).astimezone().isoformat()

        print(f"📅 Fetching calendar events for today ({today})...")

        events_result = self.calendar_service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        print(f"✅ Found {len(events)} total events\n")
        return events

    def identify_pm_interviews(self, events: List[Dict]) -> List[Dict]:
        """Identify Product Manager interview events from calendar"""
        interview_keywords = [
            'interview', 'screening', 'chat', 'discussion',
            'product manager', 'pm role', 'pm position', 'product lead',
            'hiring', 'recruiter', 'talent', 'round',
            'call', 'connect', 'catch up', 'sync',
            'product', 'exploratory',
        ]

        pm_interviews = []

        # Get the user's own email domain to detect external attendees
        own_email = self.calendar_service.calendarList().get(calendarId='primary').execute().get('id', '')
        own_domain = own_email.split('@')[-1] if '@' in own_email else ''

        for event in events:
            title = event.get('summary', '').lower()
            description = event.get('description', '').lower()

            has_keyword = any(
                keyword in title or keyword in description
                for keyword in interview_keywords
            )

            # Check if any attendee is from an external domain
            attendees = event.get('attendees', [])
            has_external = any(
                own_domain and own_domain not in att.get('email', '')
                for att in attendees
            ) if attendees else False

            is_interview = has_keyword and (has_external or not attendees)

            if is_interview:
                start = event['start'].get('dateTime', event['start'].get('date'))
                pm_interviews.append({
                    'title': event.get('summary'),
                    'description': event.get('description', ''),
                    'start': start,
                    'end': event['end'].get('dateTime', event['end'].get('date')),
                    'attendees': event.get('attendees', []),
                    'location': event.get('location', ''),
                })

        print(f"🎯 Identified {len(pm_interviews)} potential PM interview(s)\n")
        return pm_interviews

    def _create_message(self, **kwargs):
        """Call Claude with automatic retry on rate limit (429)."""
        for attempt in range(1, 4):
            try:
                return self.anthropic_client.messages.create(**kwargs)
            except Exception as e:
                if "rate_limit" in str(e).lower() or "429" in str(e):
                    wait = 60 * attempt
                    print(f"⏳ Rate limit hit. Waiting {wait}s before retry {attempt}/3...")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("Rate limit exceeded after 3 retries — try again later.")

    def search_and_extract(self, interview: Dict) -> Dict:
        """
        Single Claude call that:
        1. Figures out company name, interviewer info, and interview type from the calendar event
        2. Searches the web for real PM interview questions at that company

        Returns a dict with company_info + real_questions combined.
        """
        attendees_info = "\n".join([
            f"- {att.get('email', 'N/A')} ({att.get('displayName', 'Unknown')})"
            for att in interview['attendees']
        ]) if interview['attendees'] else "No attendees listed"

        print(f"🔍 Extracting company info and searching for real questions: {interview['title']}")

        prompt = f"""You have two tasks. First, analyse this calendar event to extract company and interview details. Then, search the web for real PM interview questions at that company.

CALENDAR EVENT:
Title: {interview['title']}
Description: {interview['description']}
Attendees:
{attendees_info}
Location/Link: {interview['location']}

TASK 1 — Extract from the calendar event:
- Company name
- Interview type (e.g. Phone Screen, Behavioral, Case Study, Final Round)
- Interviewer names and likely roles

TASK 2 — Search Glassdoor, AmbitionBox, and Exponent for real verbatim PM interview questions asked at this company. Find 5-10 actual questions from real interview experience posts — not generic advice.

Return ONLY a valid JSON object in this exact format:
{{
  "company_name": "...",
  "interview_type": "...",
  "interviewers": [
    {{"name": "...", "email": "...", "likely_role": "..."}}
  ],
  "real_questions": [
    {{
      "question": "...",
      "source": "...",
      "recency": "...",
      "round": "..."
    }}
  ]
}}

Return ONLY valid JSON. No other text."""

        try:
            message = self._create_message(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": prompt}]
            )

            # Get the final text block (comes after tool use blocks)
            response_text = ""
            for block in message.content:
                if hasattr(block, 'text') and block.text:
                    response_text = block.text.strip()

            match = re.search(r'\{[\s\S]*\}', response_text)
            if match:
                result = json.loads(match.group())
                print(f"✅ Company: {result.get('company_name', 'Unknown')}, "
                      f"Questions found: {len(result.get('real_questions', []))}\n")
                return result

        except Exception as e:
            print(f"⚠️  Search and extract failed: {e}\n")

        return {
            "company_name": "Unknown Company",
            "interview_type": "Unknown",
            "interviewers": [],
            "real_questions": []
        }

    def generate_prep_report(self, interview: Dict, extracted: Dict) -> str:
        """Generate comprehensive, personalised interview prep report"""
        company_name   = extracted.get('company_name', 'Unknown Company')
        interview_type = extracted.get('interview_type', 'Unknown')
        real_questions = extracted.get('real_questions', [])

        print(f"📝 Generating preparation report for {company_name}...")

        # Format interviewers
        interviewer_context = ""
        if extracted.get('interviewers'):
            interviewer_context = "Interviewers:\n"
            for iv in extracted['interviewers']:
                interviewer_context += f"- {iv.get('name', 'Unknown')}: {iv.get('likely_role', 'Unknown role')}\n"

        # Format real questions
        if real_questions:
            real_questions_section = "\nREAL INTERVIEW QUESTIONS FOUND ONLINE:\n"
            for q in real_questions:
                real_questions_section += (
                    f"- \"{q.get('question', '')}\" "
                    f"({q.get('source', '')}, {q.get('recency', '')}, {q.get('round', '')})\n"
                )
        else:
            real_questions_section = "\nREAL INTERVIEW QUESTIONS: None found online — use predicted questions instead.\n"

        prompt = f"""You are an expert career coach preparing a specific candidate for a Product Manager interview. Use their background to make every section of this report personalised and actionable — not generic.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

INTERVIEW DETAILS:
Company: {company_name}
Interview Type: {interview_type}
{interviewer_context}
Scheduled: {interview['start']}
{real_questions_section}

Generate a comprehensive prep report with these sections:

1. COMPANY OVERVIEW
   - What the company does, key products/services
   - Recent news and developments (last 6 months)
   - Culture, values, and ways of working

2. COMPETITIVE LANDSCAPE
   - Main competitors and how this company differentiates
   - Market position, key challenges, and strategic bets

3. REAL INTERVIEW QUESTIONS
   - List all real questions found verbatim with source attribution
   - For each, write a 2-3 sentence answer approach tailored to the candidate's background
   - If none found, provide 5-7 predicted questions based on interview type and company

4. INTERVIEW PREPARATION
   - Additional likely questions based on interview type and interviewer seniority
   - For each question, suggest how the candidate should frame their answer using their specific experience (Meesho, Orange Health Labs, ML/AI work)
   - If the role has B2B elements, suggest how to bridge from consumer experience

5. QUESTIONS TO ASK
   - 3-5 thoughtful questions tailored to each interviewer's role
   - Questions about product strategy, team structure, and what success looks like

6. KEY TALKING POINTS
   - Specific experiences from the candidate's background that map to this company's needs
   - How to proactively address gaps (B2B experience, technical depth) without being defensive
   - Relevant trends to reference (AI/ML, consumer behaviour, relevant market dynamics)

7. PREPARATION CHECKLIST
   - 5-7 specific things to do before this interview (research, stories to prep, frameworks to review)

Be specific, concrete, and use the candidate's actual background throughout. Avoid generic career advice."""

        try:
            message = self._create_message(
                model=CLAUDE_MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )

            report = message.content[0].text
            print("✅ Report generated!\n")
            return report

        except Exception as e:
            print(f"⚠️  Error generating report: {e}\n")
            return f"Error generating report: {str(e)}"

    def save_report(self, company_name: str, report: str, interview_date: str) -> str:
        """Save the report as a formatted PDF"""
        safe_company = "".join(c for c in company_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_date = interview_date.split('T')[0]
        filename = f"Interview_Prep_{safe_company}_{safe_date}.pdf"

        pdf = FPDF()
        pdf.set_margins(20, 20, 20)
        pdf.add_page()

        # ── Cover header ──────────────────────────────────────────────────
        # Dark blue bar across the top
        pdf.set_fill_color(10, 40, 80)
        pdf.rect(0, 0, 210, 38, 'F')

        pdf.set_y(8)
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, "Interview Preparation Report", align="C",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(180, 210, 255)
        pdf.cell(0, 8, company_name, align="C",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Meta line
        pdf.set_y(44)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(120, 120, 120)
        interview_date_fmt = interview_date.split('T')[0]
        generated = datetime.now().strftime('%d %b %Y, %H:%M')
        pdf.cell(0, 6, f"Interview Date: {interview_date_fmt}   |   Generated: {generated}",
                 align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Divider
        pdf.set_draw_color(10, 40, 80)
        pdf.set_line_width(0.5)
        pdf.line(20, pdf.get_y() + 3, 190, pdf.get_y() + 3)
        pdf.ln(8)

        # ── Body: parse report lines ──────────────────────────────────────
        pdf.set_text_color(30, 30, 30)

        for line in report.split('\n'):
            # Detect section headers (e.g. "1. COMPANY OVERVIEW" or "## SECTION")
            stripped = line.strip()

            # Skip lines with only special/unicode characters that Helvetica can't render
            renderable = ''.join(c for c in stripped if ord(c) < 256)
            if not renderable and stripped:
                continue

            is_main_header = (
                re.match(r'^\d+\.\s+[A-Z]', stripped) or
                re.match(r'^#{1,2}\s+', stripped) or
                (stripped.isupper() and len(stripped) > 4 and len(stripped) < 60)
            )
            is_sub_header = (
                re.match(r'^#{3,}\s+', stripped) or
                re.match(r'^[-•]\s+\*\*', stripped) or
                (stripped.startswith('**') and stripped.endswith('**'))
            )

            if is_main_header:
                pdf.ln(4)
                pdf.set_x(pdf.l_margin)
                pdf.set_fill_color(10, 40, 80)
                pdf.set_text_color(255, 255, 255)
                pdf.set_font("Helvetica", "B", 11)
                clean = re.sub(r'^#+\s*', '', stripped)
                clean = ''.join(c for c in clean if ord(c) < 256)
                pdf.cell(0, 8, f"  {clean}", fill=True,
                         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(30, 30, 30)
                pdf.ln(2)

            elif is_sub_header:
                pdf.set_x(pdf.l_margin)
                pdf.set_font("Helvetica", "B", 10)
                pdf.set_text_color(10, 40, 80)
                clean = re.sub(r'^\*\*|\*\*$|^#+\s*|^[-•]\s+\*\*', '', stripped).rstrip('*')
                clean = ''.join(c for c in clean if ord(c) < 256)
                pdf.multi_cell(0, 6, clean)
                pdf.set_text_color(30, 30, 30)

            elif stripped == '':
                pdf.ln(2)

            elif stripped.startswith('- ') or stripped.startswith('• '):
                pdf.set_font("Helvetica", "", 10)
                text = stripped[2:]
                text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
                text = ''.join(c for c in text if ord(c) < 256)
                pdf.set_x(24)
                pdf.cell(5, 6, chr(149))  # bullet — cursor stays on same line
                pdf.multi_cell(0, 6, text)
                pdf.set_x(pdf.l_margin)   # reset x after bullet+text block

            else:
                pdf.set_x(pdf.l_margin)
                pdf.set_font("Helvetica", "", 10)
                clean = re.sub(r'\*\*(.*?)\*\*', r'\1', stripped)
                clean = ''.join(c for c in clean if ord(c) < 256)
                pdf.multi_cell(0, 6, clean)

        pdf.output(filename)
        print(f"💾 Report saved: {filename}\n")
        return filename

    def send_report_email(self, filename: str, company_name: str, interview_date: str):
        """Email the prep report as an attachment"""
        gmail_user = os.environ.get("GMAIL_ADDRESS")
        gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")

        if not gmail_user or not gmail_app_password:
            print("⚠️  Skipping email: GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set\n")
            return

        print(f"📧 Sending report to {gmail_user}...")

        msg = MIMEMultipart()
        msg['From'] = gmail_user
        msg['To'] = gmail_user
        msg['Subject'] = f"Interview Prep: {company_name} ({interview_date.split('T')[0]})"

        body = f"""Hi,

Your interview prep report for {company_name} is attached.

Interview Date: {interview_date}
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Good luck!
— Interview Prep Agent
"""
        msg.attach(MIMEText(body, 'plain'))

        with open(filename, 'rb') as f:
            part = MIMEBase('application', 'pdf')
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
        msg.attach(part)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_app_password)
            server.sendmail(gmail_user, gmail_user, msg.as_string())

        print(f"✅ Report emailed to {gmail_user}\n")

    def run(self):
        """Main execution flow"""
        print("=" * 80)
        print("🚀 INTERVIEW PREP AGENT STARTED")
        print("=" * 80 + "\n")

        # Step 1: Authenticate
        self.authenticate_google_calendar()

        # Step 2: Get calendar events
        events = self.get_calendar_events()

        if not events:
            print("📭 No events found today. Nothing to do.")
            return

        # Step 3: Identify PM interviews
        interviews = self.identify_pm_interviews(events)

        if not interviews:
            print("✨ No PM interviews found. You're all clear!")
            return

        # Step 4: Process each interview
        for i, interview in enumerate(interviews, 1):
            print(f"\n{'='*80}")
            print(f"INTERVIEW {i} of {len(interviews)}: {interview['title']}")
            print(f"{'='*80}\n")

            # Single call: extract company info + search real questions
            extracted = self.search_and_extract(interview)

            # Generate personalised report
            report = self.generate_prep_report(interview, extracted)

            # Save report
            filename = self.save_report(
                extracted['company_name'],
                report,
                interview['start']
            )

            # Email report
            self.send_report_email(filename, extracted['company_name'], interview['start'])

            print(f"✅ Done for {extracted['company_name']}!")
            print(f"📄 Report saved: {filename}\n")

        print("=" * 80)
        print("🎉 ALL INTERVIEWS PROCESSED!")
        print("=" * 80)


def main():
    """Main entry point"""
    API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    if not API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY='your-key'")

    agent = InterviewPrepAgent(API_KEY)
    agent.run()


if __name__ == "__main__":
    main()
