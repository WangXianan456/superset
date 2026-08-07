/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { t } from '@apache-superset/core/translation';
import { getClientErrorObject, SupersetClient } from '@superset-ui/core';
import type {
  AiSqlAssistantResult,
  AiSqlProvider,
  FeedbackRequest,
  MetadataStatus,
  SuggestTablesResult,
} from '../types';

export const httpAiSqlProvider: AiSqlProvider = {
  async generateSql({ question, context, tables }) {
    try {
      const { json } = await SupersetClient.post({
        endpoint: '/api/v1/ai_sql/generate',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          database_id: context.databaseId,
          catalog: context.catalog,
          schema: context.schema,
          tables,
          current_sql: context.currentSql,
        }),
      });

      return json.result as AiSqlAssistantResult;
    } catch (error) {
      const parsedError = await getClientErrorObject(error);
      throw new Error(
        parsedError.message ||
          parsedError.error ||
          t('Failed to generate SQL.'),
      );
    }
  },

  async suggestTables({ question, context, limit = 10 }) {
    try {
      const { json } = await SupersetClient.post({
        endpoint: '/api/v1/ai_sql/suggest_tables',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          database_id: context.databaseId,
          catalog: context.catalog,
          schema: context.schema,
          limit,
        }),
      });

      return json.result as SuggestTablesResult;
    } catch (error) {
      const parsedError = await getClientErrorObject(error);
      throw new Error(
        parsedError.message ||
          parsedError.error ||
          t('Failed to suggest tables.'),
      );
    }
  },

  async refreshMetadata(context) {
    try {
      const { json } = await SupersetClient.post({
        endpoint: '/api/v1/ai_sql/metadata_index/refresh',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          database_id: context.databaseId,
          catalog: context.catalog,
          schema: context.schema,
          force: false,
        }),
      });

      const result = json.result as {
        updated_at?: string;
        table_count?: number;
        indexed_table_count?: number;
        supersql_synced?: boolean;
        supersql_status?: {
          columns_upserted?: number;
        };
        warnings?: string[];
      };
      return {
        synced: result.supersql_synced === true,
        updatedAt: result.updated_at,
        tableCount: result.table_count,
        indexedTableCount: result.indexed_table_count,
        columnsUpserted: result.supersql_status?.columns_upserted,
        warnings: result.warnings,
      } as MetadataStatus;
    } catch (error) {
      const parsedError = await getClientErrorObject(error);
      throw new Error(
        parsedError.message ||
          parsedError.error ||
          t('Failed to refresh metadata.'),
      );
    }
  },

  async sendFeedback(request: FeedbackRequest) {
    try {
      await SupersetClient.post({
        endpoint: '/api/v1/ai_sql/feedback',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });
    } catch (error) {
      const parsedError = await getClientErrorObject(error);
      throw new Error(
        parsedError.message ||
          parsedError.error ||
          t('Failed to send feedback.'),
      );
    }
  },
};
