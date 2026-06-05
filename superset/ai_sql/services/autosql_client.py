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
import logging
from typing import Any

import requests

from superset.ai_sql.services.llm_config import (
    is_ai_sql_enabled,
    normalize_ai_sql_config,
)
from superset.ai_sql.services.autosql_reasoning import (
    AutoSqlReasoningError,
    build_autosql_messages,
    extract_sql_from_text,
    finalize_generated_sql,
)

logger = logging.getLogger(__name__)


class AutoSqlClientError(Exception):
    """Raised when the configured AutoSQL service cannot generate a response."""


def _autosql_config() -> dict[str, Any]:
    return normalize_ai_sql_config()


def is_autosql_enabled() -> bool:
    return is_ai_sql_enabled()


def _normalize_autosql_response(
    payload: dict[str, Any],
    *,
    question: str,
    schema_context: dict[str, Any],
) -> dict[str, Any]:
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise AutoSqlClientError("AutoSQL service returned an invalid result.")

    sql = extract_sql_from_text(str(result.get("sql") or ""))
    if not sql:
        raise AutoSqlClientError("AutoSQL service response did not include SQL.")

    try:
        finalized = finalize_generated_sql(
            sql=sql,
            question=question,
            dialect=schema_context.get("dialect"),
        )
    except AutoSqlReasoningError as ex:
        raise AutoSqlClientError(str(ex)) from ex

    tables = result.get("tables", [])
    warnings = result.get("warnings", [])
    if not isinstance(tables, list):
        tables = []
    if not isinstance(warnings, list):
        warnings = [str(warnings)]

    return {
        "sql": finalized["sql"],
        "tables": [str(table) for table in tables if table],
        "explanation": str(result.get("explanation") or ""),
        "warnings": [
            str(warning)
            for warning in [
                *warnings,
                *finalized["warnings"],
            ]
            if warning
        ],
        "readonly": bool(result.get("readonly", finalized["readonly"])),
        "raw_result": result,
    }


def _chat_completions_payload(
    *,
    question: str,
    schema_context: dict[str, Any],
    current_sql: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "model": config.get("model") or "ctosql-agent",
        "messages": build_autosql_messages(
            question=question,
            schema_context=schema_context,
            current_sql=current_sql,
        ),
        "scenario_id": config.get("scenario_id") or "superset_sql_lab",
        "business_domain": config.get("business_domain") or "general",
        "user_id": config.get("user_id"),
        "trace_id": config.get("trace_id"),
        "session_id": config.get("session_id"),
        "return_sql_only": True,
        "stream": bool(config.get("stream", True)),
    }
    if config.get("reasoning_effort"):
        payload["reasoning_effort"] = config["reasoning_effort"]
    if config.get("output_effort"):
        payload["output_config"] = {"effort": config["output_effort"]}
    if config.get("thinking_type") in {"enabled", "disabled"}:
        payload["thinking"] = {"type": config["thinking_type"]}
    if config.get("temperature") is not None:
        payload["temperature"] = config["temperature"]
    return payload


def _anthropic_messages_payload(
    *,
    question: str,
    schema_context: dict[str, Any],
    current_sql: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    messages = build_autosql_messages(
        question=question,
        schema_context=schema_context,
        current_sql=current_sql,
    )
    system_message = next(
        (message["content"] for message in messages if message["role"] == "system"),
        "",
    )
    user_message = next(
        (message["content"] for message in messages if message["role"] == "user"),
        question,
    )

    return {
        "model": config.get("model") or "ctosql-agent",
        "system": system_message,
        "messages": [{"role": "user", "content": user_message}],
        "max_tokens": int(config.get("max_tokens") or 1200),
        "temperature": float(config.get("temperature") or 0.1),
    }


def _extract_chat_completions_result(
    response_text: str,
    *,
    question: str,
    schema_context: dict[str, Any],
) -> dict[str, Any]:
    last_sql = ""
    explanation_parts = []
    warnings = []

    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue

        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue

        sql = extract_sql_from_text(str(payload.get("sql") or ""))
        if sql:
            last_sql = sql

        result_preview = payload.get("result_preview")
        if isinstance(result_preview, str) and result_preview.strip():
            explanation_parts.append(result_preview.strip())

        assumptions = payload.get("assumptions")
        if isinstance(assumptions, list):
            warnings.extend(str(item) for item in assumptions if item)

        if payload.get("clarification_needed"):
            warnings.append("AutoSQL requested clarification.")

    if not last_sql:
        raise AutoSqlClientError("AutoSQL chat completions response did not include SQL.")

    try:
        finalized = finalize_generated_sql(
            sql=last_sql,
            question=question,
            dialect=schema_context.get("dialect"),
        )
    except AutoSqlReasoningError as ex:
        raise AutoSqlClientError(str(ex)) from ex

    return {
        "sql": finalized["sql"],
        "tables": [],
        "explanation": "\n".join(explanation_parts),
        "warnings": [*warnings, *finalized["warnings"]],
        "readonly": finalized["readonly"],
        "raw_result": {"response_text": response_text},
    }


def _extract_chat_completions_json_result(
    payload: dict[str, Any],
    *,
    question: str,
    schema_context: dict[str, Any],
) -> dict[str, Any]:
    texts = []
    for choice in payload.get("choices", []) or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
        elif isinstance(content, list):
            texts.extend(
                str(item.get("text")).strip()
                for item in content
                if isinstance(item, dict) and item.get("text")
            )

    sql_text = "\n".join(texts) or str(payload.get("output_text") or "")
    try:
        finalized = finalize_generated_sql(
            sql=sql_text,
            question=question,
            dialect=schema_context.get("dialect"),
        )
    except AutoSqlReasoningError as ex:
        raise AutoSqlClientError(str(ex)) from ex

    return {
        "sql": finalized["sql"],
        "tables": [],
        "explanation": "",
        "warnings": finalized["warnings"],
        "readonly": finalized["readonly"],
        "raw_result": payload,
    }


def _extract_anthropic_messages_result(
    payload: dict[str, Any],
    *,
    question: str,
    schema_context: dict[str, Any],
) -> dict[str, Any]:
    texts = []
    for item in payload.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

    sql_text = "\n".join(texts) or str(payload.get("output_text") or "")
    try:
        finalized = finalize_generated_sql(
            sql=sql_text,
            question=question,
            dialect=schema_context.get("dialect"),
        )
    except AutoSqlReasoningError as ex:
        raise AutoSqlClientError(str(ex)) from ex

    return {
        "sql": finalized["sql"],
        "tables": [],
        "explanation": "",
        "warnings": finalized["warnings"],
        "readonly": finalized["readonly"],
        "raw_result": payload,
    }


def _request_format(config: dict[str, Any], endpoint: str) -> str:
    configured_format = str(config.get("request_format") or "").lower()
    if configured_format:
        return configured_format
    if "/v1/chat/completions" in endpoint:
        return "chat_completions"
    return "generate"


def generate_sql_with_autosql(
    *,
    question: str,
    schema_context: dict[str, Any],
    current_sql: str | None = None,
) -> dict[str, Any]:
    config = _autosql_config()
    endpoint = config.get("endpoint")
    if not endpoint:
        raise AutoSqlClientError("AI_SQL_ASSISTANT.endpoint is not configured.")

    timeout_seconds = int(config.get("timeout_seconds") or 30)
    headers = {"Content-Type": "application/json"}
    configured_headers = config.get("headers")
    if isinstance(configured_headers, dict):
        headers.update(
            {
                str(header): str(value)
                for header, value in configured_headers.items()
                if header and value
            }
        )
    request_format = _request_format(config, endpoint)
    if request_format in {"chat_completions", "openai_chat"}:
        request_payload = _chat_completions_payload(
            question=question,
            schema_context=schema_context,
            current_sql=current_sql,
            config=config,
        )
    elif request_format in {"anthropic_messages", "anthropic"}:
        request_payload = _anthropic_messages_payload(
            question=question,
            schema_context=schema_context,
            current_sql=current_sql,
            config=config,
        )
    else:
        request_payload = {
            "question": question,
            "dialect": schema_context.get("dialect"),
            "database": schema_context.get("database_name"),
            "catalog": schema_context.get("catalog"),
            "schema": schema_context.get("schema"),
            "schema_context": schema_context,
            "reasoning_context": {
                "messages": build_autosql_messages(
                    question=question,
                    schema_context=schema_context,
                    current_sql=current_sql,
                ),
            },
            "current_sql": current_sql,
        }

    try:
        response = requests.post(
            endpoint,
            json=request_payload,
            headers=headers,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        if request_format in {"chat_completions", "openai_chat"}:
            if bool(config.get("stream", True)):
                return _extract_chat_completions_result(
                    response.text,
                    question=question,
                    schema_context=schema_context,
                )

            return _extract_chat_completions_json_result(
                response.json(),
                question=question,
                schema_context=schema_context,
            )

        if request_format in {"anthropic_messages", "anthropic"}:
            return _extract_anthropic_messages_result(
                response.json(),
                question=question,
                schema_context=schema_context,
            )

        response_payload = response.json()
    except requests.exceptions.RequestException as ex:
        logger.exception("AutoSQL service request failed.")
        raise AutoSqlClientError(str(ex)) from ex
    except ValueError as ex:
        logger.exception("AutoSQL service returned invalid JSON.")
        raise AutoSqlClientError("AutoSQL service returned invalid JSON.") from ex

    if not isinstance(response_payload, dict):
        raise AutoSqlClientError("AutoSQL service returned an invalid payload.")

    return _normalize_autosql_response(
        response_payload,
        question=question,
        schema_context=schema_context,
    )
