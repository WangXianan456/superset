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
"""Client for the supersqlApp independent backend service."""

from __future__ import annotations

import logging
from typing import Any

import requests
from flask import current_app, g

from superset.ai_sql.services.llm_config import (
    _coerce_bool,
    _coerce_int,
    _join_endpoint,
    _nested_config,
    _raw_ai_sql_config,
)

logger = logging.getLogger(__name__)

DEFAULT_SUPERSQL_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 120


class SupersqlClientError(Exception):
    """Raised when the supersqlApp service cannot fulfill a request."""


def _supersql_config() -> dict[str, Any]:
    raw = _raw_ai_sql_config()
    nested = _nested_config(raw, "supersql")
    url = str(nested.get("url") or raw.get("supersql_url") or "")
    return {
        "enabled": _coerce_bool(nested.get("enabled"), True) and bool(url),
        "url": url or DEFAULT_SUPERSQL_URL,
        "service_key": str(
            nested.get("service_key") or raw.get("supersql_service_key") or ""
        ),
        "timeout_seconds": _coerce_int(
            nested.get("timeout_seconds") or raw.get("supersql_timeout_seconds"),
            DEFAULT_TIMEOUT_SECONDS,
        ),
    }


def is_supersql_enabled() -> bool:
    return bool(_supersql_config()["enabled"])


def _headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config["service_key"]:
        headers["X-Autosql-Service-Key"] = config["service_key"]
    return headers


def _post(
    config: dict[str, Any],
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    endpoint = _join_endpoint(config["url"], path)
    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers=_headers(config),
            timeout=config["timeout_seconds"],
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as exc:
        message = _friendly_error(response, exc)
        logger.exception("supersqlApp request to %s failed.", path)
        raise SupersqlClientError(message) from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("supersqlApp request to %s failed.", path)
        raise SupersqlClientError(f"supersqlApp request to {path} failed: {exc}") from exc
    except ValueError as exc:
        logger.exception("supersqlApp returned invalid JSON.")
        raise SupersqlClientError("supersqlApp returned an invalid JSON response.") from exc

    if not isinstance(data, dict):
        raise SupersqlClientError("supersqlApp returned an invalid payload.")
    error = data.get("error")
    if isinstance(error, dict):
        raise SupersqlClientError(
            f"supersqlApp error {error.get('code')}: {error.get('message')}"
        )
    return data


def _friendly_error(response: requests.Response, exc: requests.HTTPError) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"supersqlApp request to {response.url} failed: {exc}"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if response.status_code == 409 and code in {"CATALOG_NOT_READY", "CATALOG_EMPTY"}:
            return "catalog 未同步：请先在 SQL Lab 刷新元数据后重试"
        return f"supersqlApp error {code}: {error.get('message')}"
    return f"supersqlApp request to {response.url} failed: {exc}"


def _current_user() -> dict[str, str | None]:
    user = getattr(g, "user", None)
    if user is None:
        return {"id": None, "username": None}
    return {
        "id": str(getattr(user, "id", "") or ""),
        "username": str(getattr(user, "username", "") or ""),
    }


def sync_metadata(
    *,
    database_id: int,
    database_name: str,
    dialect: str,
    catalog: str | None,
    schema: str | None,
    tables: list[dict[str, Any]],
    metadata_version: str,
    incremental: bool = False,
) -> dict[str, Any]:
    config = _supersql_config()
    if not config["enabled"]:
        raise SupersqlClientError("supersqlApp is not enabled.")
    payload = {
        "database": {
            "superset_database_id": database_id,
            "name": database_name,
            "dialect": dialect,
            "engine": dialect,
        },
        "catalog": catalog,
        "schema": schema or "",
        "metadata_version": metadata_version,
        "tables": tables,
        "incremental": incremental,
    }
    return _post(config, "/api/v1/metadata/sync", payload)


def diff_metadata(
    *,
    database_id: int,
    catalog: str | None,
    schema: str | None,
    signatures: dict[str, str],
) -> dict[str, Any]:
    config = _supersql_config()
    if not config["enabled"]:
        raise SupersqlClientError("supersqlApp is not enabled.")
    payload = {
        "superset_database_id": database_id,
        "catalog": catalog,
        "schema": schema or "",
        "signatures": signatures,
    }
    return _post(config, "/api/v1/metadata/diff", payload)


def search_metadata(
    *,
    database_id: int,
    catalog: str | None,
    schema: str | None,
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    config = _supersql_config()
    if not config["enabled"]:
        raise SupersqlClientError("supersqlApp is not enabled.")
    payload = {
        "superset_database_id": database_id,
        "catalog": catalog,
        "schema": schema or "",
        "query": query,
        "search_type": "auto",
        "limit": limit,
    }
    return _post(config, "/api/v1/metadata/search", payload)


def generate_sql(
    *,
    database_id: int,
    database_name: str,
    dialect: str,
    catalog: str | None,
    schema: str | None,
    question: str,
    allowed_tables: list[str],
    allowed_columns: dict[str, list[str]],
    current_sql: str | None,
) -> dict[str, Any]:
    config = _supersql_config()
    if not config["enabled"]:
        raise SupersqlClientError("supersqlApp is not enabled.")
    payload = {
        "user": _current_user(),
        "database": {
            "superset_database_id": database_id,
            "name": database_name,
            "dialect": dialect,
        },
        "catalog": catalog,
        "schema": schema or "",
        "question": question,
        "current_sql": current_sql or "",
        "allowed_scope": {
            "tables": allowed_tables,
            "columns": allowed_columns,
        },
        "constraints": {
            "readonly": True,
            "no_execute": True,
            "default_limit": 100,
            "max_tables": 10,
            "max_columns": 500,
        },
    }
    return _post(config, "/api/v1/sql/generate", payload)


def feedback(
    *,
    request_id: str,
    accepted: bool | None = None,
    copied: bool | None = None,
    inserted: bool | None = None,
    executed_successfully: bool | None = None,
    user_modified_sql: str | None = None,
    feedback_text: str | None = None,
) -> dict[str, Any]:
    config = _supersql_config()
    if not config["enabled"]:
        raise SupersqlClientError("supersqlApp is not enabled.")
    payload = {
        "request_id": request_id,
        "user": _current_user(),
        "accepted": accepted,
        "copied": copied,
        "inserted": inserted,
        "executed_successfully": executed_successfully,
        "user_modified_sql": user_modified_sql,
        "feedback_text": feedback_text,
    }
    return _post(config, "/api/v1/sql/feedback", payload)
