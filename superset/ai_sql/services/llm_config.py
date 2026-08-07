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

import os
from typing import Any

from flask import current_app

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MODEL = "ctosql-agent"


def _raw_ai_sql_config() -> dict[str, Any]:
    config = current_app.config.get("AI_SQL_ASSISTANT", {})
    return config if isinstance(config, dict) else {}


def _nested_config(config: dict[str, Any], key: str) -> dict[str, Any]:
    nested = config.get(key, {})
    return nested if isinstance(nested, dict) else {}


def _env_value(name: Any) -> str:
    return os.getenv(str(name), "").strip() if name else ""


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _join_endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _resolve_endpoint(config: dict[str, Any], llm: dict[str, Any], provider: str) -> str | None:
    endpoint = config.get("endpoint") or llm.get("endpoint")
    if endpoint:
        return str(endpoint)

    base_url = llm.get("base_url") or config.get("base_url")
    if not base_url and provider == "deepseek":
        base_url = "https://api.deepseek.com"

    if not base_url:
        return None

    wire_api = llm.get("wire_api") or config.get("wire_api") or "chat/completions"
    return _join_endpoint(str(base_url), str(wire_api))


def normalize_ai_sql_config() -> dict[str, Any]:
    """Normalize Superset-side AI SQL model configuration.

    Existing deployments can keep using the old flat `endpoint`/`model` shape.
    New deployments can use:

        AI_SQL_ASSISTANT = {
            "enabled": True,
            "llm": {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "sk-xxxx",
                "model": "deepseek-v4-flash",
            },
        }
    """
    config = _raw_ai_sql_config()
    llm = _nested_config(config, "llm")
    provider = str(
        llm.get("provider")
        or config.get("provider")
        or config.get("llm_provider")
        or ""
    ).lower()
    endpoint = _resolve_endpoint(config, llm, provider)

    headers = {}
    for source in (config.get("headers"), llm.get("headers")):
        if isinstance(source, dict):
            headers.update(
                {
                    str(header): str(value)
                    for header, value in source.items()
                    if header and value
                }
            )

    bearer_token = (
        config.get("bearer_token")
        or llm.get("bearer_token")
        or llm.get("api_key")
        or _env_value(llm.get("bearer_token_env"))
        or _env_value(llm.get("api_key_env"))
    )
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    if provider == "anthropic":
        api_key = llm.get("api_key") or _env_value(llm.get("api_key_env"))
        auth_token = llm.get("auth_token") or _env_value(llm.get("auth_token_env"))
        if api_key:
            headers["x-api-key"] = str(api_key)
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        headers.setdefault(
            "anthropic-version",
            str(llm.get("api_version") or "2023-06-01"),
        )

    request_format = str(
        config.get("request_format")
        or llm.get("request_format")
        or ""
    ).lower()
    if not request_format:
        if provider == "anthropic":
            request_format = "anthropic_messages"
        elif provider in {"deepseek", "openai", "openai_compatible"}:
            request_format = "chat_completions"
        elif endpoint and "/v1/chat/completions" in endpoint:
            request_format = "chat_completions"
        else:
            request_format = "generate"

    return {
        **config,
        "enabled": _coerce_bool(config.get("enabled"), False),
        "provider": provider,
        "endpoint": endpoint,
        "headers": headers,
        "model": llm.get("model") or config.get("model") or DEFAULT_MODEL,
        "timeout_seconds": _coerce_int(
            llm.get("timeout_seconds")
            or config.get("timeout_seconds")
            or config.get("timeout"),
            DEFAULT_TIMEOUT_SECONDS,
        ),
        "request_format": request_format,
        "stream": _coerce_bool(llm.get("stream", config.get("stream")), True),
        "temperature": llm.get("temperature", config.get("temperature", 0.1)),
        "max_tokens": _coerce_int(
            llm.get("max_tokens") or config.get("max_tokens"),
            1200,
        ),
        "reasoning_effort": llm.get("reasoning_effort")
        or config.get("reasoning_effort"),
        "output_effort": llm.get("output_effort") or config.get("output_effort"),
        "thinking_type": llm.get("thinking_type") or config.get("thinking_type"),
    }


def is_ai_sql_enabled() -> bool:
    config = normalize_ai_sql_config()
    return bool(config.get("enabled") and config.get("endpoint"))
