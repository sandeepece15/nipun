import anthropic
import json
import re
from fastapi import HTTPException

BUILDER_PROMPT = """You are a data dashboard architect. Analyze the dataset schema and business requirements, then return a JSON dashboard config.

SCHEMA:
{schema}

REQUIREMENTS:
{requirements}

Return ONLY a valid JSON object (no markdown, no explanation):

{
  "pages": [
    {
      "id": "overview",
      "title": "Overview",
      "icon": "layout-dashboard",
      "kpis": [
        { "label": "Total Bookings", "agg": "count", "col": "*", "format": "number" },
        { "label": "Total Revenue", "agg": "sum", "col": "EXACT_col_name", "format": "currency" }
      ],
      "charts": [
        {
          "id": "monthly_trend",
          "title": "Monthly Trend",
          "type": "line",
          "x": "EXACT_date_col",
          "y": "EXACT_numeric_col",
          "yAgg": "sum",
          "xLabel": "Month",
          "yLabel": "Value",
          "timeGroup": "month"
        },
        {
          "id": "by_category",
          "title": "By Category",
          "type": "hbar",
          "x": "EXACT_category_col",
          "y": "EXACT_numeric_col",
          "yAgg": "sum",
          "xLabel": "Category",
          "yLabel": "Value"
        }
      ],
      "filters": ["EXACT_col1", "EXACT_col2"]
    }
  ]
}

Chart types: bar, line, pie, donut, hbar (horizontal bar)
Agg: sum, avg, count, min, max
Format: number, currency, decimal, percent

CRITICAL: Use EXACT column names from schema. Create one page per requirement section."""


def get_client():
    from config import ANTHROPIC_API_KEY
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "API key missing. config.py mein ANTHROPIC_API_KEY set karo.")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def generate_dashboard_config(engine, requirements: str, table: str) -> dict:
    schema_str = engine.get_schema_prompt(table)
    if not schema_str:
        raise HTTPException(400, "No tables loaded.")

    client = get_client()
    prompt = BUILDER_PROMPT.replace("{schema}", schema_str).replace("{requirements}", requirements[:4000])

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        config = _parse_json(raw)
        config["source_table"] = table
        config["requirements_text"] = requirements[:500] + "..." if len(requirements) > 500 else requirements
        return config

    except HTTPException:
        raise
    except anthropic.AuthenticationError:
        raise HTTPException(401, "Invalid API key.")
    except Exception as e:
        raise HTTPException(500, str(e))


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except:
        pass
    raw_clean = re.sub(r'```json|```', '', raw).strip()
    try:
        return json.loads(raw_clean)
    except:
        pass
    match = re.search(r'\{.*\}', raw_clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    raise ValueError("Could not parse dashboard config.")
