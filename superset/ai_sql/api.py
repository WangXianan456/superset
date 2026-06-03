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
from typing import Any

from flask import request, Response
from flask_appbuilder.api import expose, permission_name, protect
from marshmallow import fields, Schema, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from superset.commands.database.exceptions import DatabaseNotFoundError
from superset.commands.database.tables import TablesDatabaseCommand
from superset.daos.database import DatabaseDAO
from superset.exceptions import SupersetSecurityException
from superset.extensions import event_logger
from superset.extensions import security_manager
from superset.sql.parse import Table
from superset.views.base_api import BaseSupersetApi, requires_json, statsd_metrics

logger = logging.getLogger(__name__)

MAX_TABLES_PER_REQUEST = 20
MAX_COLUMNS_PER_TABLE = 80
MAX_SUGGESTED_TABLES = 10
MAX_TABLES_TO_SCORE = 1000
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


def _tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text or "")
        if len(token) >= MIN_TOKEN_LENGTH
    }


def _question_tokens(question: str) -> set[str]:
    tokens = _tokenize(question)
    for keyword, aliases in BUSINESS_TOKEN_ALIASES.items():
        if keyword in question:
            tokens.update(aliases)
    return tokens


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


def _is_table_count_question(question: str) -> bool:
    normalized_question = question.lower().replace(" ", "")
    return any(pattern in normalized_question for pattern in TABLE_COUNT_PATTERNS)


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
    }
    allow_browser_login = True
    class_permission_name = "SQLLab"
    resource_name = "ai_sql"
    openapi_spec_tag = "AI SQL"
    openapi_spec_component_schemas = (
        AiSqlGenerateSchema,
        AiSqlSuggestTablesSchema,
    )

    generate_schema = AiSqlGenerateSchema()
    suggest_tables_schema = AiSqlSuggestTablesSchema()

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

            warnings.append("No tables were selected. Real schema context is empty.")

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

        result = {
            "sql": _build_mock_sql(schema_context),
            "tables": [table["name"] for table in schema_context["tables"]],
            "explanation": (
                "This is still a backend placeholder response, but it now validates "
                "database access and builds real Superset table metadata context."
            ),
            "warnings": [
                "Mock SQL only. No real AutoSQL service was called.",
                *warnings,
            ],
            "schema_context": schema_context,
        }
        return self.response(200, result=result)
