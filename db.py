import duckdb
import pandas as pd
import io
import json
from pathlib import Path

NUMERIC_TYPES = {"INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT", "SMALLINT", "TINYINT"}

class DBEngine:
    def __init__(self):
        self.con = duckdb.connect(database=":memory:")
        self._tables: dict[str, dict] = {}   # name → {filename, rows, cols}

    # ── Load ────────────────────────────────────────────────────────────────

    def load_file(self, filename: str, content: bytes) -> dict:
        ext = Path(filename).suffix.lower()
        stem = Path(filename).stem.replace(" ", "_").replace("-", "_").lower()

        try:
            if ext == ".csv":
                df = pd.read_csv(io.BytesIO(content))
            elif ext in (".xlsx", ".xls"):
                df = pd.read_excel(io.BytesIO(content))
            elif ext == ".json":
                df = pd.read_json(io.BytesIO(content))
            elif ext == ".parquet":
                df = pd.read_parquet(io.BytesIO(content))
            else:
                raise ValueError(f"Unsupported file type: {ext}")

            # Clean column names
            df.columns = [c.strip().replace(" ", "_").replace("-", "_").lower() for c in df.columns]

            # Register in DuckDB
            self.con.register(f"_df_{stem}", df)
            self.con.execute(f'CREATE OR REPLACE TABLE "{stem}" AS SELECT * FROM _df_{stem}')
            self.con.unregister(f"_df_{stem}")

            rows = len(df)
            cols = len(df.columns)
            self._tables[stem] = {"filename": filename, "rows": rows, "cols": cols}

            return {
                "table": stem,
                "filename": filename,
                "rows": rows,
                "columns": cols,
                "column_names": list(df.columns),
            }
        except Exception as e:
            raise ValueError(f"Could not load {filename}: {e}")

    def drop_table(self, name: str):
        self.con.execute(f'DROP TABLE IF EXISTS "{name}"')
        self._tables.pop(name, None)

    def list_files(self) -> list:
        out = []
        for tbl, meta in self._tables.items():
            missing = self._missing_pct(tbl)
            dupes = self._duplicate_count(tbl)
            out.append({**meta, "table": tbl, "missing_pct": missing, "duplicates": dupes})
        return out

    # ── Query helpers ───────────────────────────────────────────────────────

    def run(self, sql: str) -> list[dict]:
        rel = self.con.execute(sql)
        cols = [d[0] for d in rel.description]
        rows = rel.fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def preview(self, table: str, limit: int = 100) -> dict:
        rows = self.run(f'SELECT * FROM "{table}" LIMIT {limit}')
        schema = self.schema(table)
        return {"rows": rows, "schema": schema, "total": self._table_count(table)}

    def schema(self, table: str) -> list[dict]:
        info = self.run(f"DESCRIBE \"{table}\"")
        return [{"column": r["column_name"], "type": r["column_type"]} for r in info]

    def _table_count(self, table: str) -> int:
        return self.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]

    def _missing_pct(self, table: str) -> float:
        try:
            schema = self.schema(table)
            cols = [s["column"] for s in schema]
            null_checks = " + ".join([f'(COUNT(*) - COUNT("{c}"))' for c in cols])
            total_cells = f"(COUNT(*) * {len(cols)})"
            result = self.run(f'SELECT ROUND(100.0 * ({null_checks}) / {total_cells}, 2) as mp FROM "{table}"')
            return result[0]["mp"] or 0.0
        except:
            return 0.0

    def _duplicate_count(self, table: str) -> int:
        try:
            schema = self.schema(table)
            cols = ", ".join([f'"{s["column"]}"' for s in schema])
            result = self.run(
                f'SELECT COUNT(*) - COUNT(*) OVER() + COUNT(DISTINCT ({cols})) as dupes FROM "{table}"'
            )
            # Simpler approach
            total = self._table_count(table)
            schema_cols = ", ".join([f'"{s["column"]}"' for s in schema])
            distinct = self.run(f'SELECT COUNT(*) as n FROM (SELECT DISTINCT {schema_cols} FROM "{table}")')
            return total - distinct[0]["n"]
        except:
            return 0

    # ── Dashboard stats ─────────────────────────────────────────────────────

    def dashboard_stats(self, table: str) -> dict:
        schema = self.schema(table)
        col_names = [s["column"] for s in schema]
        col_types = {s["column"]: s["type"].upper() for s in schema}

        total_rows = self._table_count(table)
        missing_pct = self._missing_pct(table)
        duplicates = self._duplicate_count(table)

        # Detect numeric columns
        num_cols = [c for c, t in col_types.items() if any(nt in t for nt in NUMERIC_TYPES)]
        # Detect date columns
        date_cols = [c for c, t in col_types.items() if "DATE" in t or "TIMESTAMP" in t]
        # Detect categorical columns (non-numeric, non-date, low cardinality)
        cat_cols = []
        for c in col_names:
            if c not in num_cols and c not in date_cols:
                try:
                    card = self.run(f'SELECT COUNT(DISTINCT "{c}") as n FROM "{table}"')[0]["n"]
                    if card <= 50:
                        cat_cols.append(c)
                except:
                    pass

        kpis = {
            "total_rows": total_rows,
            "total_columns": len(col_names),
            "missing_pct": missing_pct,
            "duplicates": duplicates,
            "numeric_columns": num_cols,
            "date_columns": date_cols,
            "categorical_columns": cat_cols,
        }

        # Numeric summaries
        num_summaries = {}
        for col in num_cols[:4]:
            try:
                r = self.run(f'SELECT MIN("{col}") as mn, MAX("{col}") as mx, AVG("{col}") as avg, SUM("{col}") as total FROM "{table}"')[0]
                num_summaries[col] = {k: round(v, 2) if v is not None else None for k, v in r.items()}
            except:
                pass

        # Category distributions
        cat_distributions = {}
        for col in cat_cols[:3]:
            try:
                rows = self.run(f'SELECT "{col}" as label, COUNT(*) as count FROM "{table}" GROUP BY "{col}" ORDER BY count DESC LIMIT 10')
                cat_distributions[col] = rows
            except:
                pass

        # Time series (if date column exists)
        time_series = {}
        if date_cols and num_cols:
            dc = date_cols[0]
            nc = num_cols[0]
            try:
                ts = self.run(
                    f'SELECT STRFTIME(CAST("{dc}" AS DATE), \'%Y-%m\') as period, SUM("{nc}") as value '
                    f'FROM "{table}" WHERE "{dc}" IS NOT NULL '
                    f'GROUP BY period ORDER BY period LIMIT 24'
                )
                time_series = {"date_col": dc, "value_col": nc, "data": ts}
            except:
                pass

        return {
            "kpis": kpis,
            "numeric_summaries": num_summaries,
            "category_distributions": cat_distributions,
            "time_series": time_series,
            "columns": col_names,
        }

    def get_schema_prompt(self, table: str = None) -> str:
        """Build schema string for LLM context."""
        if table:
            tables = [table] if table in self._tables else list(self._tables.keys())
        else:
            tables = list(self._tables.keys())

        parts = []
        for t in tables:
            try:
                schema = self.schema(t)
                col_str = ", ".join([f'{s["column"]} ({s["type"]})' for s in schema])
                count = self._table_count(t)
                parts.append(f'Table "{t}" ({count} rows): {col_str}')
            except:
                pass
        return "\n".join(parts)


engine = DBEngine()
