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
  AiSqlProvider,
} from '../types';

export const useAiSqlAssistant = (
  provider: AiSqlProvider,
  context: AiSqlAssistantContext,
) => {
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AiSqlAssistantResult | null>(null);

  const generateSql = useCallback(async () => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      setError('Enter a question first.');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await provider.generateSql({
        question: trimmedQuestion,
        context,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate SQL.');
    } finally {
      setLoading(false);
    }
  }, [context, provider, question]);

  return {
    question,
    setQuestion,
    loading,
    error,
    result,
    generateSql,
  };
};

