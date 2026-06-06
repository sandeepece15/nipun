import pandas as pd
from fastapi import HTTPException

NUMERIC_TYPES = {"INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT", "SMALLINT", "TINYINT"}

def get_cleaning_report(engine, table: str) -> dict:
    try:
        schema = engine.schema(table)
        total = engine.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]
        col_types = {s["column"]: s["type"].upper() for s in schema}

        missing = []
        for col in [s["column"] for s in schema]:
            null_count = engine.run(
                f'SELECT COUNT(*) - COUNT("{col}") as n FROM "{table}"'
            )[0]["n"]
            if null_count > 0:
                pct = round(100.0 * null_count / total, 2) if total > 0 else 0
                ctype = col_types.get(col, "VARCHAR")
                is_numeric = any(nt in ctype for nt in NUMERIC_TYPES)
                missing.append({
                    "column": col,
                    "null_count": null_count,
                    "null_pct": pct,
                    "type": ctype,
                    "is_numeric": is_numeric,
                    "suggested_strategy": (
                        "mean" if is_numeric else
                        ("ffill" if "DATE" in ctype or "TIMESTAMP" in ctype else "mode")
                    ),
                })

        # Duplicates
        all_cols = ", ".join([f'"{s["column"]}"' for s in schema])
        distinct = engine.run(f'SELECT COUNT(*) as n FROM (SELECT DISTINCT {all_cols} FROM "{table}")')[0]["n"]
        duplicates = total - distinct

        # Outliers (numeric columns only)
        outliers = []
        for col, ctype in col_types.items():
            if any(nt in ctype for nt in NUMERIC_TYPES):
                try:
                    stats = engine.run(
                        f'SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY "{col}") as q1, '
                        f'PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY "{col}") as q3 '
                        f'FROM "{table}" WHERE "{col}" IS NOT NULL'
                    )[0]
                    q1, q3 = stats["q1"], stats["q3"]
                    if q1 is not None and q3 is not None:
                        iqr = q3 - q1
                        lower = q1 - 1.5 * iqr
                        upper = q3 + 1.5 * iqr
                        count = engine.run(
                            f'SELECT COUNT(*) as n FROM "{table}" '
                            f'WHERE "{col}" < {lower} OR "{col}" > {upper}'
                        )[0]["n"]
                        if count > 0:
                            outliers.append({
                                "column": col,
                                "outlier_count": count,
                                "lower_bound": round(lower, 2),
                                "upper_bound": round(upper, 2),
                            })
                except:
                    pass

        return {
            "table": table,
            "total_rows": total,
            "missing_columns": missing,
            "duplicates": duplicates,
            "outliers": outliers,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def apply_cleaning(engine, table: str, strategies: dict, drop_duplicates: bool) -> dict:
    try:
        schema = engine.schema(table)
        col_types = {s["column"]: s["type"].upper() for s in schema}
        changes = []

        for col, strategy in strategies.items():
            if col not in col_types:
                continue
            ctype = col_types[col]
            is_numeric = any(nt in ctype for nt in NUMERIC_TYPES)

            try:
                if strategy == "drop":
                    before = engine.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]
                    engine.run(f'DELETE FROM "{table}" WHERE "{col}" IS NULL')
                    after = engine.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]
                    changes.append({"column": col, "strategy": "drop_rows", "rows_removed": before - after})

                elif strategy == "mean" and is_numeric:
                    avg = engine.run(f'SELECT AVG("{col}") as v FROM "{table}"')[0]["v"]
                    if avg is not None:
                        engine.run(f'UPDATE "{table}" SET "{col}" = {round(avg, 4)} WHERE "{col}" IS NULL')
                        changes.append({"column": col, "strategy": "mean", "fill_value": round(avg, 4)})

                elif strategy == "median" and is_numeric:
                    med = engine.run(
                        f'SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "{col}") as v FROM "{table}" WHERE "{col}" IS NOT NULL'
                    )[0]["v"]
                    if med is not None:
                        engine.run(f'UPDATE "{table}" SET "{col}" = {round(med, 4)} WHERE "{col}" IS NULL')
                        changes.append({"column": col, "strategy": "median", "fill_value": round(med, 4)})

                elif strategy == "zero" and is_numeric:
                    engine.run(f'UPDATE "{table}" SET "{col}" = 0 WHERE "{col}" IS NULL')
                    changes.append({"column": col, "strategy": "zero_fill"})

                elif strategy == "mode":
                    mode_val = engine.run(
                        f'SELECT "{col}" as v, COUNT(*) as n FROM "{table}" WHERE "{col}" IS NOT NULL '
                        f'GROUP BY "{col}" ORDER BY n DESC LIMIT 1'
                    )
                    if mode_val:
                        v = mode_val[0]["v"]
                        safe_v = f"'{v}'" if not is_numeric else str(v)
                        engine.run(f'UPDATE "{table}" SET "{col}" = {safe_v} WHERE "{col}" IS NULL')
                        changes.append({"column": col, "strategy": "mode", "fill_value": v})

                elif strategy == "unknown":
                    engine.run(f"UPDATE \"{table}\" SET \"{col}\" = 'Unknown' WHERE \"{col}\" IS NULL")
                    changes.append({"column": col, "strategy": "unknown_tag"})

                elif strategy == "ffill":
                    # Forward fill using window function workaround in DuckDB
                    all_cols = [s["column"] for s in schema]
                    select_cols = ", ".join([
                        f'LAST_VALUE("{c}" IGNORE NULLS) OVER (ORDER BY rowid ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS "{c}"'
                        if c == col else f'"{c}"'
                        for c in all_cols
                    ])
                    engine.run(
                        f'CREATE OR REPLACE TABLE "{table}" AS '
                        f'SELECT {select_cols} FROM (SELECT rowid, * FROM "{table}")'
                    )
                    changes.append({"column": col, "strategy": "forward_fill"})

            except Exception as col_err:
                changes.append({"column": col, "strategy": strategy, "error": str(col_err)})

        if drop_duplicates:
            all_cols_str = ", ".join([f'"{s["column"]}"' for s in schema])
            before = engine.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]
            engine.run(
                f'CREATE OR REPLACE TABLE "{table}" AS '
                f'SELECT DISTINCT {all_cols_str} FROM "{table}"'
            )
            after = engine.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]
            changes.append({"strategy": "drop_duplicates", "rows_removed": before - after})

        final_rows = engine.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]
        return {"table": table, "changes": changes, "final_rows": final_rows, "status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
