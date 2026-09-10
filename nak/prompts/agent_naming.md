Suggest a reusable agent name for a new AI agent described as: {description}

Rules:
- Prefer generic reusable names: PlanningAgent for plans/roadmaps/designs,
  CodingAgent for code/APIs/apps, ResearchAgent for research, ExcelAgent only
  for Excel files, PdfReaderAgent only for PDFs, EmailAgent for emails.
- Never propose task-specific names like FastAPIPlanningAgent — use PlanningAgent instead.
- CamelCase, must end with "Agent".

Return ONLY JSON, no markdown, like:
{"agent_name": "PlanningAgent", "reason": "..."}
