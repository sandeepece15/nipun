from itertools import combinations

def detect_relations(engine) -> dict:
    tables = list(engine._tables.keys())
    if len(tables) < 2:
        return {"relations": [], "tables": tables}

    relations = []

    # Build schema map
    schema_map = {}
    for t in tables:
        try:
            schema_map[t] = {s["column"]: s["type"].upper() for s in engine.schema(t)}
        except:
            schema_map[t] = {}

    for t1, t2 in combinations(tables, 2):
        cols1 = schema_map.get(t1, {})
        cols2 = schema_map.get(t2, {})

        for c1 in cols1:
            for c2 in cols2:
                # Name-based matching: exact, or one is substring of other
                if not _names_match(c1, c2):
                    continue
                # Type compatibility
                if not _types_compatible(cols1[c1], cols2[c2]):
                    continue
                # Value overlap check (sampled)
                overlap = _check_overlap(engine, t1, c1, t2, c2)
                if overlap > 0.3:
                    # Determine which is PK (higher uniqueness ratio)
                    u1 = _uniqueness(engine, t1, c1)
                    u2 = _uniqueness(engine, t2, c2)
                    if u1 >= u2:
                        pk_table, pk_col, fk_table, fk_col = t1, c1, t2, c2
                    else:
                        pk_table, pk_col, fk_table, fk_col = t2, c2, t1, c1

                    join_type = "INNER JOIN" if overlap > 0.7 else "LEFT JOIN"
                    relations.append({
                        "pk_table": pk_table,
                        "pk_col": pk_col,
                        "fk_table": fk_table,
                        "fk_col": fk_col,
                        "overlap_pct": round(overlap * 100, 1),
                        "join_type": join_type,
                        "join_sql": f'"{fk_table}" {join_type} "{pk_table}" ON "{fk_table}"."{fk_col}" = "{pk_table}"."{pk_col}"',
                    })

    return {"relations": relations, "tables": tables, "schema_map": {t: list(v.keys()) for t, v in schema_map.items()}}


def _names_match(c1: str, c2: str) -> bool:
    c1l, c2l = c1.lower(), c2.lower()
    if c1l == c2l:
        return True
    # Common FK patterns: customer_id ↔ cust_id ↔ id
    def base(name):
        for suffix in ["_id", "_key", "_code", "_no", "_num"]:
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return name
    b1, b2 = base(c1l), base(c2l)
    if b1 == b2:
        return True
    # One contains the other
    if b1 in b2 or b2 in b1:
        return True
    return False


def _types_compatible(t1: str, t2: str) -> bool:
    NUMERIC = {"INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT", "SMALLINT"}
    TEXT = {"VARCHAR", "TEXT", "STRING", "CHAR"}
    DATE = {"DATE", "TIMESTAMP"}

    def category(t):
        if any(n in t for n in NUMERIC):
            return "numeric"
        if any(n in t for n in DATE):
            return "date"
        return "text"

    return category(t1) == category(t2)


def _check_overlap(engine, t1, c1, t2, c2) -> float:
    try:
        result = engine.run(
            f'SELECT COUNT(DISTINCT a."{c1}") as common '
            f'FROM (SELECT DISTINCT "{c1}" FROM "{t1}" WHERE "{c1}" IS NOT NULL LIMIT 500) a '
            f'INNER JOIN (SELECT DISTINCT "{c2}" FROM "{t2}" WHERE "{c2}" IS NOT NULL) b '
            f'ON a."{c1}" = b."{c2}"'
        )
        common = result[0]["common"]
        total = engine.run(f'SELECT COUNT(DISTINCT "{c1}") as n FROM "{t1}" WHERE "{c1}" IS NOT NULL')[0]["n"]
        return common / total if total > 0 else 0
    except:
        return 0


def _uniqueness(engine, table, col) -> float:
    try:
        total = engine.run(f'SELECT COUNT(*) as n FROM "{table}"')[0]["n"]
        distinct = engine.run(f'SELECT COUNT(DISTINCT "{col}") as n FROM "{table}"')[0]["n"]
        return distinct / total if total > 0 else 0
    except:
        return 0
