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
import type { AiSqlAssistantResult, AiSqlProvider } from '../types';

export const httpAiSqlProvider: AiSqlProvider = {
  async generateSql({ question, context }) {
    try {
      const { json } = await SupersetClient.post({
        endpoint: '/api/v1/ai_sql/generate',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          database_id: context.databaseId,
          schema: context.schema,
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
};
