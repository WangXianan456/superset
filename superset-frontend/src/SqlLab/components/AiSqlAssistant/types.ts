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
export type AiSqlAssistantContext = {
  databaseId?: number;
  databaseName?: string;
  catalog?: string | null;
  schema?: string;
  currentSql?: string;
};

export type AiSqlAssistantResult = {
  sql: string;
  tables: string[];
  explanation?: string;
  warnings?: string[];
  readonly?: boolean;
  provider?: string;
  model?: string;
  request_id?: string;
  retrieval?: AiSqlRetrieval;
};

export type AiSqlRetrieval = {
  mode?: string;
  candidates?: AiSqlRetrievalCandidate[];
};

export type AiSqlRetrievalCandidate = {
  table: string;
  score?: number;
  reason?: string;
  matched_columns?: string[];
};

export type GenerateSqlRequest = {
  question: string;
  context: AiSqlAssistantContext;
  tables: string[];
};

export type AiSqlSuggestedColumn = {
  name: string;
  type?: string;
  comment?: string;
  score?: number;
};

export type AiSqlSuggestedTable = {
  name: string;
  type?: string;
  score: number;
  reason?: string;
  matched_columns?: AiSqlSuggestedColumn[];
};

export type SuggestTablesRequest = {
  question: string;
  context: AiSqlAssistantContext;
  limit?: number;
};

export type SuggestTablesResult = {
  tables: AiSqlSuggestedTable[];
  scanned_table_count?: number;
  total_table_count?: number;
  metadata_index?: {
    used: boolean;
    updated_at?: string;
  };
  warnings?: string[];
};

export type MetadataStatus = {
  synced: boolean;
  updatedAt?: string;
  tableCount?: number;
  indexedTableCount?: number;
  columnsUpserted?: number;
  warnings?: string[];
};

export type FeedbackRequest = {
  request_id: string;
  accepted?: boolean;
  copied?: boolean;
  inserted?: boolean;
  executed_successfully?: boolean;
  user_modified_sql?: string;
  feedback_text?: string;
};

export type AiSqlProvider = {
  generateSql: (request: GenerateSqlRequest) => Promise<AiSqlAssistantResult>;
  suggestTables: (
    request: SuggestTablesRequest,
  ) => Promise<SuggestTablesResult>;
  refreshMetadata: (context: AiSqlAssistantContext) => Promise<MetadataStatus>;
  sendFeedback: (request: FeedbackRequest) => Promise<void>;
};
