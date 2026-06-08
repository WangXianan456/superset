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
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from flask import current_app, request, Response
from flask_appbuilder.api import expose, permission_name, protect
from marshmallow import fields, Schema, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from superset.ai_sql.services.autosql_client import (
    AutoSqlClientError,
    generate_sql_with_autosql,
    is_autosql_enabled,
)
from superset.ai_sql.services.business_aliases import (
    expand_question_tokens,
    get_business_aliases,
    tokenize_text,
)
from superset.ai_sql.services.llm_config import normalize_ai_sql_config
from superset.commands.database.exceptions import DatabaseNotFoundError
from superset.commands.database.tables import TablesDatabaseCommand
from superset.daos.database import DatabaseDAO
from superset.exceptions import SupersetSecurityException
from superset.extensions import cache_manager, event_logger, security_manager
from superset.sql.parse import Table
from superset.views.base_api import BaseSupersetApi, requires_json, statsd_metrics

logger = logging.getLogger(__name__)

MAX_TABLES_PER_REQUEST = 20
MAX_COLUMNS_PER_TABLE = 80
MAX_SUGGESTED_TABLES = 10
MAX_TABLES_TO_SCORE = 1000
MAX_INDEX_TABLES_PER_REFRESH = 500
MAX_INDEX_COLUMNS_PER_TABLE = 200
MAX_AUTO_SELECTED_TABLES = 5
METADATA_INDEX_CACHE_TIMEOUT = 24 * 60 * 60
MIN_TOKEN_LENGTH = 2
TABLE_COUNT_PATTERNS = (
    "多少表",
    "多少张表",
    "几张表",
    "表数量",
    "表的数量",
    "table count",
    "count tables",
    "how many tables",
)
FIELD_SEARCH_PATTERNS = (
    "哪些表包含",
    "哪些表有",
    "哪些表的字段",
    "哪些表里的字段",
    "哪些表里有",
    "哪些表中有",
    "哪个表包含",
    "哪个表有",
    "哪个表的字段",
    "哪个表里的字段",
    "在哪些表里",
    "在哪些表中",
    "字段在哪些表",
    "字段出现在哪些表",
    "字段属于哪些表",
    "字段来自哪些表",
    "column in tables",
    "tables contain",
    "tables have",
)

BUSINESS_TOKEN_ALIASES = {
    "订单": {"order", "orders"},
    "客户": {"customer", "customers", "client", "clients"},
    "用户": {"user", "users", "account", "accounts"},
    "商品": {"product", "products", "sku", "goods"},
    "销售": {"sale", "sales"},
    "金额": {"amount", "price", "money", "payment"},
    "支付": {"payment", "pay", "paid"},
    "退款": {"refund", "refunds"},
    "库存": {"stock", "inventory"},
    "门店": {"store", "stores", "shop", "shops"},
    "员工": {"employee", "employees", "staff"},
    "组织": {"org", "organization", "organizations"},
    "部门": {"department", "departments", "dept"},
    "日期": {"date", "day"},
    "时间": {"time", "datetime", "timestamp"},
    "日志": {"log", "logs"},
}


class AiSqlGenerateSchema(Schema):
    question = fields.String(required=True)
    database_id = fields.Integer(required=True)
    catalog = fields.String(allow_none=True)
    schema = fields.String(allow_none=True)
    tables = fields.List(fields.String(), load_default=list)
    current_sql = fields.String(allow_none=True)


class AiSqlSuggestTablesSchema(Schema):
    question = fields.String(required=True)
    database_id = fields.Integer(required=True)
    catalog = fields.String(allow_none=True)
    schema = fields.String(allow_none=True)
    limit = fields.Integer(load_default=MAX_SUGGESTED_TABLES)
    force = fields.Boolean(load_default=False)


class AiSqlMetadataIndexRefreshSchema(Schema):
    database_id = fields.Integer(required=True)
    catalog = fields.String(allow_none=True)
    schema = fields.String(allow_none=True)
    force = fields.Boolean(load_default=False)


class AiSqlMetadataIndexSearchSchema(Schema):
    question = fields.String(required=True)
    database_id = fields.Integer(required=True)
    catalog = fields.String(allow_none=True)
    schema = fields.String(allow_none=True)
    limit = fields.Integer(load_default=MAX_SUGGESTED_TABLES)


def _tokenize(text: str) -> set[str]:
    return tokenize_text(text)


def _question_tokens(question: str) -> set[str]:
    return expand_question_tokens(question, BUSINESS_TOKEN_ALIASES)


def _score_table(question_tokens: set[str], table_item: dict[str, Any]) -> int:
    table_name = str(table_item.get("value") or "")
    table_tokens = _tokenize(table_name.replace("_", " "))
    compact_table_name = table_name.replace("_", "").lower()
    score = 0

    for token in question_tokens:
        if token == table_name.lower() or token == compact_table_name:
            score += 10
        elif token in table_tokens:
            score += 6
        elif token in table_name.lower() or token in compact_table_name:
            score += 3

    return score


def _suggest_tables_from_payload(
    question: str,
    tables_payload: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    question_tokens = _question_tokens(question)
    scored_tables = []

    for table_item in tables_payload.get("result", [])[:MAX_TABLES_TO_SCORE]:
        table_name = str(table_item.get("value") or "")
        if not table_name:
            continue

        score = _score_table(question_tokens, table_item)
        if score <= 0 and question_tokens:
            continue

        scored_tables.append(
            {
                "name": table_name,
                "type": table_item.get("type"),
                "score": score,
                "reason": (
                    "Matched question tokens in table name."
                    if score > 0
                    else "Fallback candidate from accessible table list."
                ),
            }
        )

    scored_tables.sort(key=lambda item: (-item["score"], item["name"]))
    return scored_tables[:limit]


def _metadata_index_cache_timeout() -> int:
    return int(
        current_app.config.get(
            "AI_SQL_METADATA_INDEX_CACHE_TIMEOUT",
            METADATA_INDEX_CACHE_TIMEOUT,
        )
    )


def _metadata_index_cache_part(value: Any) -> str:
    if value is None:
        return "__none__"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value))


def _metadata_index_cache_key(
    database_id: int,
    catalog: str | None,
    schema: str | None,
) -> str:
    return ":".join(
        [
            "ai_sql",
            "metadata_index",
            str(database_id),
            _metadata_index_cache_part(catalog),
            _metadata_index_cache_part(schema),
        ]
    )


def _get_metadata_index(
    database_id: int,
    catalog: str | None,
    schema: str | None,
) -> dict[str, Any] | None:
    cache_key = _metadata_index_cache_key(database_id, catalog, schema)
    cached_index = cache_manager.cache.get(cache_key)
    return cached_index if isinstance(cached_index, dict) else None


def _column_summary(column: dict[str, Any]) -> dict[str, Any]:
    keys = column.get("keys") or []
    return {
        "name": column.get("name"),
        "type": column.get("type"),
        "long_type": column.get("longType"),
        "comment": column.get("comment"),
        "primary_key": any(key.get("type") == "pk" for key in keys),
    }


def _build_search_text(table_summary: dict[str, Any]) -> str:
    parts = [
        table_summary.get("name"),
        table_summary.get("type"),
        table_summary.get("comment"),
    ]
    for column in table_summary.get("columns", []):
        parts.extend(
            [
                column.get("name"),
                column.get("type"),
                column.get("long_type"),
                column.get("comment"),
            ]
        )
    return " ".join(str(part) for part in parts if part)


def _refresh_metadata_index(
    database_id: int,
    catalog: str | None,
    schema: str | None,
    force: bool,
) -> dict[str, Any]:
    database = DatabaseDAO.find_by_id(database_id)
    if database is None:
        raise DatabaseNotFoundError()

    tables_payload = TablesDatabaseCommand(
        database_id,
        catalog,
        schema,
        force,
    ).run()
    warnings = []
    accessible_tables = tables_payload.get("result", [])
    if tables_payload.get("count", 0) > MAX_INDEX_TABLES_PER_REFRESH:
        warnings.append(
            "Only the first "
            f"{MAX_INDEX_TABLES_PER_REFRESH} accessible tables/views were indexed."
        )

    index_tables = []
    for table_item in accessible_tables[:MAX_INDEX_TABLES_PER_REFRESH]:
        table_name = str(table_item.get("value") or "")
        if not table_name:
            continue

        table = Table(table_name, schema, catalog)
        try:
            security_manager.raise_for_access(database=database, table=table)
            metadata = database.db_engine_spec.get_table_metadata(database, table)
        except SupersetSecurityException:
            warnings.append(f"Skipped inaccessible table/view: {table_name}")
            continue
        except SQLAlchemyError as ex:
            logger.exception("Unable to index table metadata for %s", table_name)
            warnings.append(f"Skipped {table_name}: {ex}")
            continue

        all_columns = metadata.get("columns", [])
        table_summary = {
            "name": table_name,
            "type": table_item.get("type"),
            "schema": schema,
            "catalog": catalog,
            "comment": metadata.get("comment"),
            "columns": [
                _column_summary(column)
                for column in all_columns[:MAX_INDEX_COLUMNS_PER_TABLE]
            ],
            "column_count": len(all_columns),
            "truncated_columns": len(all_columns) > MAX_INDEX_COLUMNS_PER_TABLE,
        }
        table_summary["search_text"] = _build_search_text(table_summary)
        index_tables.append(table_summary)

    metadata_index = {
        "database_id": database.id,
        "database_name": database.database_name,
        "dialect": getattr(database, "backend", None) or database.db_engine_spec.engine,
        "catalog": catalog,
        "schema": schema,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "table_count": tables_payload.get("count", 0),
        "indexed_table_count": len(index_tables),
        "max_indexed_tables": MAX_INDEX_TABLES_PER_REFRESH,
        "max_indexed_columns_per_table": MAX_INDEX_COLUMNS_PER_TABLE,
        "tables": index_tables,
        "warnings": warnings,
    }
    cache_manager.cache.set(
        _metadata_index_cache_key(database_id, catalog, schema),
        metadata_index,
        timeout=_metadata_index_cache_timeout(),
    )
    return metadata_index


def _search_metadata_index(
    metadata_index: dict[str, Any],
    question: str,
    limit: int,
) -> list[dict[str, Any]]:
    question_tokens = _question_tokens(question)
    scored_tables = []

    for table_summary in metadata_index.get("tables", []):
        table_name = str(table_summary.get("name") or "")
        table_text = str(table_summary.get("search_text") or "").lower()
        table_tokens = _tokenize(table_name.replace("_", " "))
        compact_table_name = table_name.replace("_", "").lower()
        matched_columns = []
        score = 0

        for token in question_tokens:
            normalized_token = token.lower()
            compact_token = normalized_token.replace("_", "")
            if (
                normalized_token == table_name.lower()
                or compact_token == compact_table_name
            ):
                score += 12
            elif normalized_token in table_tokens:
                score += 7
            elif normalized_token in table_text:
                score += 2

        for column in table_summary.get("columns", []):
            column_name = str(column.get("name") or "")
            column_name_lower = column_name.lower()
            compact_column_name = column_name_lower.replace("_", "")
            column_score = 0
            for token in question_tokens:
                normalized_token = token.lower()
                compact_token = normalized_token.replace("_", "")
                if normalized_token == column_name_lower:
                    column_score += 30
                elif compact_token == compact_column_name:
                    column_score += 24
                elif normalized_token in column_name_lower:
                    column_score += 12
                elif normalized_token in str(column.get("comment") or "").lower():
                    column_score += 6
            if column_score > 0:
                score += column_score
                matched_columns.append(
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                        "comment": column.get("comment"),
                        "score": column_score,
                    }
                )

        if score <= 0 and question_tokens:
            continue

        scored_tables.append(
            {
                "name": table_name,
                "type": table_summary.get("type"),
                "score": score,
                "reason": (
                    "Matched question tokens in cached table/column metadata."
                    if matched_columns
                    else "Matched question tokens in cached table metadata."
                ),
                "matched_columns": sorted(
                    matched_columns,
                    key=lambda item: (-item["score"], item["name"] or ""),
                )[:10],
            }
        )

    scored_tables.sort(key=lambda item: (-item["score"], item["name"]))
    return scored_tables[:limit]


def _is_table_count_question(question: str) -> bool:
    normalized_question = question.lower().replace(" ", "")
    return any(pattern in normalized_question for pattern in TABLE_COUNT_PATTERNS)


def _is_field_search_question(question: str) -> bool:
    normalized_question = question.lower().replace(" ", "")
    if any(pattern in normalized_question for pattern in FIELD_SEARCH_PATTERNS):
        return True
    return "字段" in normalized_question and (
        "哪些表" in normalized_question
        or "哪个表" in normalized_question
        or "表里" in normalized_question
        or "表中" in normalized_question
    )


def _build_column_search_sql(schema_name: str | None, column_names: list[str]) -> str:
    if column_names:
        escaped_columns = ", ".join(
            f"'{column_name.replace(chr(39), chr(39) + chr(39))}'"
            for column_name in column_names
        )
        column_predicate = f"column_name IN ({escaped_columns})"
    else:
        column_predicate = "column_name = '<column_name>'"

    schema_predicate = f"table_schema = '{schema_name}'"
    if not schema_name:
        schema_predicate = "table_schema = DATABASE()"
    return "\n".join(
        [
            "-- SQL generated by AutoSQL AI Assistant metadata helper",
            "SELECT table_schema, table_name, column_name, data_type",
            "FROM information_schema.columns",
            f"WHERE {schema_predicate}",
            f"  AND {column_predicate}",
            "ORDER BY table_schema, table_name, ordinal_position;",
        ]
    )


def _build_table_count_sql(schema_name: str | None, dialect: str) -> str:
    if schema_name:
        schema_predicate = f"table_schema = '{schema_name}'"
    else:
        schema_predicate = "table_schema = DATABASE()"

    if dialect in {"postgresql", "postgres"} and not schema_name:
        schema_predicate = "table_schema = current_schema()"

    return "\n".join(
        [
            "-- SQL generated by AutoSQL AI Assistant metadata helper",
            "SELECT COUNT(*) AS table_count",
            "FROM information_schema.tables",
            f"WHERE {schema_predicate}",
            "  AND table_type IN ('BASE TABLE', 'VIEW');",
        ]
    )


def _compact_table_metadata(
    table_name: str,
    schema_name: str | None,
    catalog_name: str | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    columns = []
    for column in metadata.get("columns", [])[:MAX_COLUMNS_PER_TABLE]:
        keys = column.get("keys") or []
        columns.append(
            {
                "name": column.get("name"),
                "type": column.get("type"),
                "long_type": column.get("longType"),
                "comment": column.get("comment"),
                "primary_key": any(key.get("type") == "pk" for key in keys),
            }
        )

    return {
        "name": table_name,
        "schema": schema_name,
        "catalog": catalog_name,
        "comment": metadata.get("comment"),
        "columns": columns,
        "column_count": len(metadata.get("columns", [])),
        "truncated_columns": len(metadata.get("columns", [])) > MAX_COLUMNS_PER_TABLE,
        "primary_key": metadata.get("primaryKey"),
        "foreign_keys": metadata.get("foreignKeys") or [],
        "select_star": metadata.get("selectStar"),
    }


def _build_mock_sql(schema_context: dict[str, Any]) -> str:
    first_table = next(iter(schema_context.get("tables", [])), None)
    if first_table and first_table.get("select_star"):
        return "\n".join(
            [
                "-- Mock SQL generated by AutoSQL AI Assistant",
                "-- This query is based on real Superset table metadata.",
                first_table["select_star"],
            ]
        )

    return "\n".join(
        [
            "-- Mock SQL generated by AutoSQL AI Assistant",
            "-- No table metadata was selected for this request.",
            "SELECT 1;",
        ]
    )


class AiSqlRestApi(BaseSupersetApi):
    method_permission_name = {
        "generate": "read",
        "suggest_tables": "read",
        "metadata_index_refresh": "read",
        "metadata_index_search": "read",
        "config_status": "read",
    }
    allow_browser_login = True
    class_permission_name = "SQLLab"
    resource_name = "ai_sql"
    openapi_spec_tag = "AI SQL"
    openapi_spec_component_schemas = (
        AiSqlGenerateSchema,
        AiSqlSuggestTablesSchema,
        AiSqlMetadataIndexRefreshSchema,
        AiSqlMetadataIndexSearchSchema,
    )

    generate_schema = AiSqlGenerateSchema()
    suggest_tables_schema = AiSqlSuggestTablesSchema()
    metadata_index_refresh_schema = AiSqlMetadataIndexRefreshSchema()
    metadata_index_search_schema = AiSqlMetadataIndexSearchSchema()

    @expose("/config_status", methods=("GET",))
    @protect()
    @permission_name("read")
    @statsd_metrics
    def config_status(self) -> Response:
        """Return non-secret AI SQL configuration status for troubleshooting."""
        config = normalize_ai_sql_config()
        endpoint = str(config.get("endpoint") or "")
        parsed_endpoint = urlparse(endpoint)
        endpoint_label = (
            f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}"
            if parsed_endpoint.scheme and parsed_endpoint.netloc
            else None
        )
        return self.response(
            200,
            result={
                "enabled": bool(config.get("enabled")),
                "provider": config.get("provider"),
                "model": config.get("model"),
                "request_format": config.get("request_format"),
                "endpoint_configured": bool(endpoint),
                "endpoint": endpoint_label,
                "authorization_configured": bool(
                    (config.get("headers") or {}).get("Authorization")
                ),
                "timeout_seconds": config.get("timeout_seconds"),
                "business_alias_count": len(get_business_aliases(BUSINESS_TOKEN_ALIASES)),
                "metadata_index_cache_timeout": _metadata_index_cache_timeout(),
            },
        )

    @expose("/metadata_index/refresh", methods=("POST",))
    @protect()
    @permission_name("read")
    @statsd_metrics
    @requires_json
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: (
            f"{self.__class__.__name__}.metadata_index_refresh"
        ),
        log_to_statsd=False,
    )
    def metadata_index_refresh(self) -> Response:
        """Refresh cached table/column metadata for the current SQL Lab context."""
        try:
            payload = self.metadata_index_refresh_schema.load(request.json)
        except ValidationError as error:
            return self.response_400(message=error.messages)

        try:
            metadata_index = _refresh_metadata_index(
                payload["database_id"],
                payload.get("catalog"),
                payload.get("schema"),
                payload["force"],
            )
        except DatabaseNotFoundError:
            return self.response_404()

        return self.response(
            200,
            result={
                "database_id": metadata_index["database_id"],
                "database_name": metadata_index["database_name"],
                "catalog": metadata_index.get("catalog"),
                "schema": metadata_index.get("schema"),
                "updated_at": metadata_index["updated_at"],
                "table_count": metadata_index["table_count"],
                "indexed_table_count": metadata_index["indexed_table_count"],
                "max_indexed_tables": metadata_index["max_indexed_tables"],
                "max_indexed_columns_per_table": metadata_index[
                    "max_indexed_columns_per_table"
                ],
                "warnings": metadata_index["warnings"],
            },
        )

    @expose("/metadata_index/search", methods=("POST",))
    @protect()
    @permission_name("read")
    @statsd_metrics
    @requires_json
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: (
            f"{self.__class__.__name__}.metadata_index_search"
        ),
        log_to_statsd=False,
    )
    def metadata_index_search(self) -> Response:
        """Search cached table/column metadata for a natural-language question."""
        try:
            payload = self.metadata_index_search_schema.load(request.json)
        except ValidationError as error:
            return self.response_400(message=error.messages)

        question = payload["question"].strip()
        if not question:
            return self.response_400(message="Question is required.")

        limit = max(1, min(payload["limit"], MAX_SUGGESTED_TABLES))
        metadata_index = _get_metadata_index(
            payload["database_id"],
            payload.get("catalog"),
            payload.get("schema"),
        )
        if metadata_index is None:
            return self.response(
                200,
                result={
                    "database_id": payload["database_id"],
                    "catalog": payload.get("catalog"),
                    "schema": payload.get("schema"),
                    "tables": [],
                    "index_found": False,
                    "warnings": [
                        "Metadata index is empty. Call metadata_index/refresh first."
                    ],
                },
            )

        return self.response(
            200,
            result={
                "database_id": payload["database_id"],
                "catalog": payload.get("catalog"),
                "schema": payload.get("schema"),
                "tables": _search_metadata_index(metadata_index, question, limit),
                "index_found": True,
                "updated_at": metadata_index.get("updated_at"),
                "indexed_table_count": metadata_index.get("indexed_table_count", 0),
                "warnings": metadata_index.get("warnings", []),
            },
        )

    @expose("/suggest_tables", methods=("POST",))
    @protect()
    @permission_name("read")
    @statsd_metrics
    @requires_json
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: (
            f"{self.__class__.__name__}.suggest_tables"
        ),
        log_to_statsd=False,
    )
    def suggest_tables(self) -> Response:
        """Suggest candidate tables for a natural-language question."""
        try:
            payload = self.suggest_tables_schema.load(request.json)
        except ValidationError as error:
            return self.response_400(message=error.messages)

        question = payload["question"].strip()
        if not question:
            return self.response_400(message="Question is required.")

        limit = max(1, min(payload["limit"], MAX_SUGGESTED_TABLES))
        metadata_index = _get_metadata_index(
            payload["database_id"],
            payload.get("catalog"),
            payload.get("schema"),
        )
        if metadata_index is not None:
            suggestions = _search_metadata_index(metadata_index, question, limit)
            return self.response(
                200,
                result={
                    "database_id": payload["database_id"],
                    "catalog": payload.get("catalog"),
                    "schema": payload.get("schema"),
                    "tables": suggestions,
                    "scanned_table_count": metadata_index.get(
                        "indexed_table_count",
                        0,
                    ),
                    "total_table_count": metadata_index.get("table_count", 0),
                    "metadata_index": {
                        "used": True,
                        "updated_at": metadata_index.get("updated_at"),
                    },
                    "warnings": metadata_index.get("warnings", []),
                },
            )

        try:
            tables_payload = TablesDatabaseCommand(
                payload["database_id"],
                payload.get("catalog"),
                payload.get("schema"),
                payload["force"],
            ).run()
        except DatabaseNotFoundError:
            return self.response_404()

        suggestions = _suggest_tables_from_payload(question, tables_payload, limit)
        warnings = []
        if tables_payload.get("count", 0) > MAX_TABLES_TO_SCORE:
            warnings.append(
                "Only the first "
                f"{MAX_TABLES_TO_SCORE} accessible tables were scored."
            )

        return self.response(
            200,
            result={
                "database_id": payload["database_id"],
                "catalog": payload.get("catalog"),
                "schema": payload.get("schema"),
                "tables": suggestions,
                "scanned_table_count": min(
                    tables_payload.get("count", 0),
                    MAX_TABLES_TO_SCORE,
                ),
                "total_table_count": tables_payload.get("count", 0),
                "metadata_index": {"used": False},
                "warnings": warnings,
            },
        )

    @expose("/generate", methods=("POST",))
    @protect()
    @permission_name("read")
    @statsd_metrics
    @requires_json
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: f"{self.__class__.__name__}.generate",
        log_to_statsd=False,
    )
    def generate(self) -> Response:
        """Generate SQL from a natural-language question.
        ---
        post:
          summary: Generate SQL from natural language
          requestBody:
            required: true
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/AiSqlGenerateSchema'
          responses:
            200:
              description: Generated SQL result
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      result:
                        type: object
            400:
              $ref: '#/components/responses/400'
            401:
              $ref: '#/components/responses/401'
            403:
              $ref: '#/components/responses/403'
            500:
              $ref: '#/components/responses/500'
        """
        try:
            payload = self.generate_schema.load(request.json)
        except ValidationError as error:
            return self.response_400(message=error.messages)

        question = payload["question"].strip()
        if not question:
            return self.response_400(message="Question is required.")

        database_id = payload["database_id"]
        catalog = payload.get("catalog")
        schema = payload.get("schema")
        table_names = [
            table_name.strip()
            for table_name in payload.get("tables", [])
            if table_name and table_name.strip()
        ]
        table_names = list(dict.fromkeys(table_names))

        if len(table_names) > MAX_TABLES_PER_REQUEST:
            return self.response_400(
                message=f"At most {MAX_TABLES_PER_REQUEST} tables are allowed."
            )

        database = DatabaseDAO.find_by_id(database_id)
        if database is None:
            return self.response_404()

        schema_context: dict[str, Any] = {
            "database_id": database.id,
            "database_name": database.database_name,
            "dialect": getattr(database, "backend", None)
            or database.db_engine_spec.engine,
            "schema": schema,
            "catalog": catalog,
            "tables": [],
        }
        warnings = []

        if not table_names:
            if _is_table_count_question(question):
                try:
                    tables_payload = TablesDatabaseCommand(
                        database_id,
                        catalog,
                        schema,
                        False,
                    ).run()
                except DatabaseNotFoundError:
                    return self.response_404()

                table_count = tables_payload.get("count", 0)
                return self.response(
                    200,
                    result={
                        "sql": _build_table_count_sql(
                            schema,
                            schema_context["dialect"],
                        ),
                        "tables": [],
                        "explanation": (
                            f"Superset metadata reports {table_count} accessible "
                            "tables/views for the current database context."
                        ),
                        "warnings": [
                            (
                                "The displayed count is based on Superset-accessible "
                                "tables/views. Direct information_schema SQL may "
                                "return a different number if permissions differ."
                            )
                        ],
                        "schema_context": {
                            **schema_context,
                            "table_count": table_count,
                        },
                    },
                )

            if _is_field_search_question(question):
                metadata_index = _get_metadata_index(database_id, catalog, schema)
                if metadata_index is None:
                    try:
                        metadata_index = _refresh_metadata_index(
                            database_id,
                            catalog,
                            schema,
                            False,
                        )
                    except DatabaseNotFoundError:
                        return self.response_404()
                    warnings.append(
                        "Metadata index was empty and has been refreshed for this "
                        "field-level question."
                    )

                matched_tables = _search_metadata_index(
                    metadata_index,
                    question,
                    MAX_SUGGESTED_TABLES,
                )
                matched_column_names = list(
                    dict.fromkeys(
                        column["name"]
                        for table in matched_tables
                        for column in table.get("matched_columns", [])
                        if column.get("name")
                    )
                )
                return self.response(
                    200,
                    result={
                        "sql": _build_column_search_sql(
                            schema,
                            matched_column_names,
                        ),
                        "tables": [table["name"] for table in matched_tables],
                        "explanation": (
                            "Matched tables from the AI SQL metadata index. "
                            "Refresh the index if recently added columns are missing."
                        ),
                        "warnings": [
                            "No business data rows were read.",
                            *warnings,
                            *metadata_index.get("warnings", []),
                        ],
                        "schema_context": {
                            **schema_context,
                            "metadata_index_search": {
                                "updated_at": metadata_index.get("updated_at"),
                                "matched_tables": matched_tables,
                                "auto_refreshed": (
                                    "Metadata index was empty and has been refreshed "
                                    "for this field-level question."
                                )
                                in warnings,
                                "indexed_table_count": metadata_index.get(
                                    "indexed_table_count",
                                    0,
                                ),
                                "table_count": metadata_index.get("table_count", 0),
                            },
                        },
                    },
                )

            metadata_index = _get_metadata_index(database_id, catalog, schema)
            if metadata_index is not None:
                matched_tables = _search_metadata_index(
                    metadata_index,
                    question,
                    MAX_AUTO_SELECTED_TABLES,
                )
                table_names = [
                    table["name"]
                    for table in matched_tables
                    if table.get("name") and table.get("score", 0) > 0
                ]
                if table_names:
                    schema_context["table_selection"] = {
                        "mode": "metadata_index_auto",
                        "updated_at": metadata_index.get("updated_at"),
                        "matched_tables": matched_tables,
                    }
                    warnings.append(
                        "No tables were manually selected. Auto-selected candidate "
                        "tables from the AI SQL metadata index."
                    )
                else:
                    warnings.append(
                        "No tables were selected and metadata index search returned "
                        "no candidates."
                    )
            else:
                warnings.append(
                    "No tables were selected. Real schema context is empty. Refresh "
                    "the AI SQL metadata index to enable automatic table selection."
                )

        for table_name in table_names:
            table = Table(table_name, schema, catalog)
            try:
                security_manager.raise_for_access(database=database, table=table)
                metadata = database.db_engine_spec.get_table_metadata(database, table)
            except SupersetSecurityException:
                return self.response_404(message=f"No such table: {table_name}")
            except SQLAlchemyError as ex:
                logger.exception("Unable to load table metadata for %s", table_name)
                return self.response_422(message=str(ex))

            schema_context["tables"].append(
                _compact_table_metadata(table_name, schema, catalog, metadata)
            )

        if schema_context["tables"] and is_autosql_enabled():
            try:
                autosql_result = generate_sql_with_autosql(
                    question=question,
                    schema_context=schema_context,
                    current_sql=payload.get("current_sql"),
                )
            except AutoSqlClientError as ex:
                logger.exception("AutoSQL generation failed.")
                return self.response_422(message=str(ex))

            result = {
                "sql": autosql_result["sql"],
                "tables": autosql_result["tables"]
                or [table["name"] for table in schema_context["tables"]],
                "explanation": autosql_result["explanation"],
                "warnings": [
                    *autosql_result["warnings"],
                    *warnings,
                ],
                "readonly": autosql_result["readonly"],
                "provider": "autosql",
                "reasoning_engine": "superset_native_autosql",
                "schema_context": schema_context,
            }
            return self.response(200, result=result)

        result = {
            "sql": _build_mock_sql(schema_context),
            "tables": [table["name"] for table in schema_context["tables"]],
            "explanation": (
                "This is still a backend placeholder response, but it now validates "
                "database access and builds real Superset table metadata context. "
                "Configure AI_SQL_ASSISTANT.enabled and AI_SQL_ASSISTANT.endpoint "
                "to call the AutoSQL service."
            ),
            "warnings": [
                "Mock SQL only. No real AutoSQL service was called.",
                *warnings,
            ],
            "readonly": True,
            "provider": "mock",
            "schema_context": schema_context,
        }
        return self.response(200, result=result)
