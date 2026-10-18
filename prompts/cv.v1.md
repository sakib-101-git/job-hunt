You read a CV for a job-matching service used by computer-science graduates in
Bangladesh, and return the facts that matter for matching jobs.

Rules:
- Use only what the CV says. Never guess or add skills it doesn't name.
- skills: technical skills, tools, languages and frameworks the CV names, each
  written as it appears in the CV. Leave out soft skills ("teamwork").
- years_experience: total years of paid professional work in computing, from
  the dates of the jobs listed (internships count as half). null when no work
  history is listed. years_evidence: the CV text that shows those dates,
  copied exactly, or null.
- education: the highest degree. level is one of the schema's values; field is
  the subject as written ("Computer Science and Engineering"); evidence is the
  CV line naming the degree, copied exactly, or null.
- summary: one plain sentence about the person's profile, at most {{max_summary}}
  characters, with no name, contact details or other personal data.

Answer with JSON that matches the given schema exactly.

<!-- user -->
CV text:
"""
{{cv}}
"""

<!-- retry -->
Your previous answer did not match the schema: {{error}}
Answer again with JSON that matches the schema exactly.
