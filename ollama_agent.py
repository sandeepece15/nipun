import anthropic
from fastapi import HTTPException

def get_client():
    from config import ANTHROPIC_API_KEY
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="API key missing. config.py mein ANTHROPIC_API_KEY set karo.")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are a SQL expert for DuckDB. Convert user questions (English/Hindi/Hinglish) to valid DuckDB SQL.

Rules:
1. Return ONLY JSON: {"sql": "...", "explanation": "..."}
2. Use double quotes for identifiers: "column", "table"
3. No backticks, no backslashes in identifiers
4. String values use single quotes: WHERE "col" = 'value'
5. LIMIT 500 unless user asks for all
6. For aggregations always add ORDER BY
7. Column/table names EXACTLY as in schema

Schema:
{schema}"""

def nl_to_result(engine, question: str, table: str = None) -> dict:
    import json, re
    schema_str = engine.get_schema_prompt(table)
    if not schema_str:
        raise HTTPException(status_code=400, detail="No tables loaded. Please upload a file first.")

    client = get_client()

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=SYSTEM_PROMPT.replace("{schema}", schema_str),
            messages=[{"role": "user", "content": f"Question: {question}\n\nReturn JSON only."}]
        )
        raw = msg.content[0].text.strip()

        # Parse JSON
        parsed = _parse(raw)
        sql = _clean_sql(parsed.get("sql", ""))
        explanation = parsed.get("explanation", "Query executed.")

        if not sql:
            raise HTTPException(status_code=422, detail="Could not extract SQL from response.")

        rows = engine.run(sql)
        columns = list(rows[0].keys()) if rows else []
        return {"question": question, "sql": sql, "explanation": explanation,
                "columns": columns, "rows": rows, "row_count": len(rows)}

    except HTTPException:
        raise
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid API key. config.py mein sahi key daalo.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _clean_sql(sql: str) -> str:
    import re
    sql = re.sub(r'```sql|```', '', sql).strip()
    sql = sql.replace('\\"', '"').replace('\\', '')
    sql = re.sub(r'`([^`]+)`', r'"\1"', sql)
    return sql.strip()

def _parse(raw: str) -> dict:
    import json, re
    try:
        return json.loads(raw)
    except:
        pass
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    return {"sql": "", "explanation": ""}
