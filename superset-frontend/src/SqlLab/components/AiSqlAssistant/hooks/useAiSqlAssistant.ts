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
import { useCallback, useState } from 'react';
import type {
  AiSqlAssistantContext,
  AiSqlAssistantResult,
  AiSqlSuggestedTable,
  AiSqlProvider,
  FeedbackRequest,
  MetadataStatus,
} from '../types';

export const useAiSqlAssistant = (
  provider: AiSqlProvider,
  context: AiSqlAssistantContext,
) => {
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [suggestingTables, setSuggestingTables] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AiSqlAssistantResult | null>(null);
  const [suggestedTables, setSuggestedTables] = useState<AiSqlSuggestedTable[]>(
    [],
  );
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [metadataStatus, setMetadataStatus] = useState<MetadataStatus | null>(
    null,
  );
  const [feedbackSent, setFeedbackSent] = useState(false);

  const refreshMetadata = useCallback(async () => {
    if (!context.databaseId) {
      setError('Select a database first.');
      return;
    }

    setRefreshing(true);
    setError(null);

    try {
      const status = await provider.refreshMetadata(context);
      setMetadataStatus(status);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to refresh metadata.',
      );
    } finally {
      setRefreshing(false);
    }
  }, [context, provider]);

  const suggestTables = useCallback(async () => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      setError('Enter a question first.');
      return;
    }

    if (!context.databaseId) {
      setError('Select a database first.');
      return;
    }

    setSuggestingTables(true);
    setError(null);

    try {
      const response = await provider.suggestTables({
        question: trimmedQuestion,
        context,
      });
      setSuggestedTables(response.tables);
      setSelectedTables(response.tables.map(table => table.name));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to suggest tables.',
      );
    } finally {
      setSuggestingTables(false);
    }
  }, [context, provider, question]);

  const toggleSelectedTable = useCallback((tableName: string) => {
    setSelectedTables(currentTables =>
      currentTables.includes(tableName)
        ? currentTables.filter(name => name !== tableName)
        : [...currentTables, tableName],
    );
  }, []);

  const generateSql = useCallback(async () => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      setError('Enter a question first.');
      return;
    }

    setLoading(true);
    setError(null);
    setFeedbackSent(false);

    try {
      const response = await provider.generateSql({
        question: trimmedQuestion,
        context,
        tables: selectedTables,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate SQL.');
    } finally {
      setLoading(false);
    }
  }, [context, provider, question, selectedTables]);

  const sendFeedback = useCallback(
    async (feedback: Omit<FeedbackRequest, 'request_id'>) => {
      if (!result?.request_id || feedbackSent) {
        return;
      }

      try {
        await provider.sendFeedback({
          request_id: result.request_id,
          ...feedback,
        });
        setFeedbackSent(true);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : 'Failed to send feedback.',
        );
      }
    },
    [feedbackSent, provider, result?.request_id],
  );

  return {
    question,
    setQuestion,
    loading,
    suggestingTables,
    refreshing,
    error,
    result,
    suggestedTables,
    selectedTables,
    metadataStatus,
    feedbackSent,
    refreshMetadata,
    suggestTables,
    toggleSelectedTable,
    generateSql,
    sendFeedback,
  };
};
