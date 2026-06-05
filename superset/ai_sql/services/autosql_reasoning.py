# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import json
import re
from typing import Any

from flask import current_app

DEFAULT_SQL_LIMIT = 100
MAX_SQL_LIMIT = 1000
DANGEROUS_SQL_PATTERN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|"
    r"merge|call|exec|execute|copy|vacuum|analyze|attach|detach"
    r")\b",
    re.IGNORECASE,
)
READONLY_SQL_PATTERN = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
LIMIT_PATTERN = re.compile(r"(?is)\blimit\s+(\d+)\b")
SELECT_STAR_PATTERN = re.compile(r"(?is)\bselect\s+\*")
AGGREGATE_PATTERN = re.compile(
    r"(?is)\b(count|avg|sum|min|max)\s*\(|\bgroup\s+by\b|\bhaving\b"
)
CATALOG_SQL_PATTERN = re.compile(
    r"(?is)\b(information_schema|pg_catalog|sys\.)\b"
)


class AutoSqlReasoningError(Exception):
    """Raised when generated SQL violates Superset-side AI SQL rules."""


def _ai_sql_config() -> dict[str, Any]:
    config = current_app.config.get("AI_SQL_ASSISTANT", {})
    return config if isinstance(config, dict) else {}


def _configured_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(_ai_sql_config().get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _compact_schema_context(schema_context: dict[str, Any]) -> dict[str, Any]:
    tables = []
    for table in schema_context.get("tables", []):
        columns = []
        for column in table.get("columns", []):
            columns.append(
                {
                    "name": column.get("name"),
                    "type": column.get("type") or column.get("long_type"),
                    "comment": column.get("comment"),
                    "primary_key": bool(column.get("primary_key")),
                }
            )
        tables.append(
            {
                "name": table.get("name"),
                "schema": table.get("schema"),
                "catalog": table.get("catalog"),
                "comment": table.get("comment"),
                "columns": columns,
                "primary_key": table.get("primary_key"),
                "foreign_keys": table.get("foreign_keys") or [],
            }
        )

    return {
        "database_id": schema_context.get("database_id"),
        "database_name": schema_context.get("database_name"),
        "dialect": schema_context.get("dialect"),
        "catalog": schema_context.get("catalog"),
        "schema": schema_context.get("schema"),
        "tables": tables,
    }


def build_autosql_messages(
    *,
    question: str,
    schema_context: dict[str, Any],
    current_sql: str | None,
) -> list[dict[str, str]]:
    """Build a Superset-native Text-to-SQL reasoning contract.

    This keeps AutoSQL's useful planner shape inside Superset while making
    Superset the source of truth for schema, permissions, and safety rules.
    """
    compact_schema_context = _compact_schema_context(schema_context)
    max_limit = _configured_int("max_sql_limit", MAX_SQL_LIMIT, 1, MAX_SQL_LIMIT)
    default_limit = _configured_int("default_sql_limit", DEFAULT_SQL_LIMIT, 1, max_limit)

    system_prompt = "\n".join(
        [
            "You are Superset SQL Lab's Text-to-SQL planner.",
            "Generate one SQL query for the user's question.",
            "Use only the provided schema_context; do not invent tables or columns.",
            "Do not inspect information_schema, pg_catalog, system tables, or data rows.",
            "Return STRICT JSON only: {\"sql\":\"...\", \"explanation\":\"...\", \"tables\":[\"...\"], \"warnings\":[\"...\"]}.",
            "The SQL must be a single readonly SELECT statement; WITH is allowed only for readonly SELECT CTEs.",
            "Never generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, MERGE, CALL, EXEC, COPY, VACUUM, or GRANT/REVOKE.",
            "Avoid SELECT *; choose explicit columns from schema_context.",
            f"For non-aggregate detail queries, include LIMIT <= {max_limit}. Use LIMIT {default_limit} when no better limit is implied.",
            "If the question is ambiguous, make conservative assumptions and include them in warnings.",
            "Never include database connection strings, credentials, tokens, or hidden system details.",
        ]
    )

    user_parts = [
        "Question:",
        question,
        "",
        "schema_context:",
        json.dumps(compact_schema_context, ensure_ascii=False, default=str),
    ]
    if current_sql:
        user_parts.extend(["", "Current SQL editor content:", current_sql])

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def extract_sql_from_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    candidates = [raw]
    fenced_json = re.search(r"```json\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    if fenced_json:
        candidates.insert(0, fenced_json.group(1).strip())
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if 0 <= brace_start < brace_end:
        candidates.append(raw[brace_start : brace_end + 1].strip())

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            sql = str(payload.get("sql") or "").strip()
            if sql:
                return sql

    fenced_sql = re.search(r"```sql\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    if fenced_sql:
        return fenced_sql.group(1).strip()

    select_match = re.search(r"(?is)\b(with|select)\b[\s\S]*?(?:;|$)", raw)
    return select_match.group(0).strip() if select_match else ""


def _strip_outer_semicolon(sql: str) -> tuple[str, bool]:
    stripped = (sql or "").strip()
    has_semicolon = stripped.endswith(";")
    return stripped.rstrip(";").strip(), has_semicolon


def _has_multiple_statements(sql: str) -> bool:
    body, _ = _strip_outer_semicolon(sql)
    return ";" in body


def _is_aggregate_sql(sql: str) -> bool:
    return bool(AGGREGATE_PATTERN.search(sql or ""))


def _enforce_limit(sql: str, warnings: list[str]) -> str:
    max_limit = _configured_int("max_sql_limit", MAX_SQL_LIMIT, 1, MAX_SQL_LIMIT)
    default_limit = _configured_int("default_sql_limit", DEFAULT_SQL_LIMIT, 1, max_limit)
    body, had_semicolon = _strip_outer_semicolon(sql)
    limit_match = LIMIT_PATTERN.search(body)

    if limit_match:
        current_limit = int(limit_match.group(1))
        if current_limit > max_limit:
            body = (
                body[: limit_match.start(1)]
                + str(max_limit)
                + body[limit_match.end(1) :]
            )
            warnings.append(f"Reduced LIMIT to {max_limit}.")
    elif not _is_aggregate_sql(body):
        body = f"{body}\nLIMIT {default_limit}"
        warnings.append(f"Added LIMIT {default_limit} to the generated detail query.")

    return body + (";" if had_semicolon else "")


def finalize_generated_sql(
    *,
    sql: str,
    question: str,
    dialect: str | None,
) -> dict[str, Any]:
    generated_sql = extract_sql_from_text(sql)
    if not generated_sql:
        raise AutoSqlReasoningError("AI service response did not include SQL.")

    if _has_multiple_statements(generated_sql):
        raise AutoSqlReasoningError("AI generated multiple SQL statements.")

    if not READONLY_SQL_PATTERN.search(generated_sql):
        raise AutoSqlReasoningError("AI generated non-readonly SQL.")

    if DANGEROUS_SQL_PATTERN.search(generated_sql):
        raise AutoSqlReasoningError("AI generated SQL containing dangerous keywords.")

    if CATALOG_SQL_PATTERN.search(generated_sql):
        raise AutoSqlReasoningError(
            "AI generated SQL against system catalog tables instead of schema_context."
        )

    warnings = []
    if SELECT_STAR_PATTERN.search(generated_sql):
        warnings.append(
            "Generated SQL contains SELECT *; prefer explicit columns before running."
        )

    normalized_dialect = (dialect or "").lower()
    if normalized_dialect not in {"mssql", "sqlserver", "oracle"}:
        generated_sql = _enforce_limit(generated_sql, warnings)

    return {
        "sql": generated_sql,
        "warnings": warnings,
        "readonly": True,
    }
