from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn

from db import engine
from cleaner import get_cleaning_report, apply_cleaning
from relations import detect_relations
from ollama_agent import nl_to_result
from requirement_reader import extract_requirements
from dashboard_builder import generate_dashboard_config

app = FastAPI(title="DataFlow AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    try:
        from config import ANTHROPIC_API_KEY
        api_configured = bool(ANTHROPIC_API_KEY and ANTHROPIC_API_KEY != "your-api-key-here")
    except:
        api_configured = False
    return {"status": "ok", "api_configured": api_configured}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    return engine.load_file(file.filename, content)

@app.get("/files")
def list_files():
    return engine.list_files()

@app.delete("/files/{name}")
def delete_file(name: str):
    engine.drop_table(name)
    return {"deleted": name}

@app.get("/preview/{table}")
def preview(table: str, limit: int = 2000):
    return engine.preview(table, limit)

@app.get("/schema/{table}")
def schema(table: str):
    return engine.schema(table)

@app.get("/dashboard/{table}")
def dashboard(table: str):
    return engine.dashboard_stats(table)

@app.get("/clean/report/{table}")
def clean_report(table: str):
    return get_cleaning_report(engine, table)

class CleanRequest(BaseModel):
    table: str
    strategies: dict
    drop_duplicates: bool = False

@app.post("/clean/apply")
def clean_apply(req: CleanRequest):
    return apply_cleaning(engine, req.table, req.strategies, req.drop_duplicates)

@app.get("/relations")
def relations():
    return detect_relations(engine)

class QueryRequest(BaseModel):
    question: str
    table: Optional[str] = None

@app.post("/query")
def query(req: QueryRequest):
    return nl_to_result(engine, req.question, req.table)

@app.post("/requirements/upload")
async def upload_requirements(file: UploadFile = File(...)):
    content = await file.read()
    try:
        text = extract_requirements(file.filename, content)
        return {"text": text, "filename": file.filename, "length": len(text)}
    except ValueError as e:
        raise HTTPException(400, str(e))

class BuildRequest(BaseModel):
    requirements: str
    table: str

@app.post("/requirements/build")
def build_dashboard(req: BuildRequest):
    return generate_dashboard_config(engine, req.requirements, req.table)

@app.post("/data/query")
def data_query(req: QueryRequest):
    if not req.question:
        raise HTTPException(400, "SQL required")
    try:
        rows = engine.run(req.question)
        cols = list(rows[0].keys()) if rows else []
        return {"rows": rows, "columns": cols, "count": len(rows)}
    except Exception as e:
        raise HTTPException(400, str(e))

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
