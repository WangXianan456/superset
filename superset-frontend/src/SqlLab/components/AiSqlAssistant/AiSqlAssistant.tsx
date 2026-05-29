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
import { useCallback, useMemo } from 'react';
import { shallowEqual, useSelector } from 'react-redux';
import { useAppDispatch } from 'src/views/store';
import type { QueryEditor, SqlLabRootState } from 'src/SqlLab/types';
import { queryEditorSetAndSaveSql } from 'src/SqlLab/actions/sqlLab';
import { t } from '@apache-superset/core/translation';
import copyTextToClipboard from 'src/utils/copy';
import { useToasts } from 'src/components/MessageToasts/withToasts';
import AiSqlAssistantPanel from './AiSqlAssistantPanel';
import { useAiSqlAssistant } from './hooks/useAiSqlAssistant';
import { httpAiSqlProvider } from './providers/httpAiSqlProvider';
import type { AiSqlAssistantContext, AiSqlProvider } from './types';

export type AiSqlAssistantProps = {
  provider?: AiSqlProvider;
};

const AiSqlAssistant = ({
  provider = httpAiSqlProvider,
}: AiSqlAssistantProps) => {
  const dispatch = useAppDispatch();
  const { addDangerToast, addSuccessToast } = useToasts();
  const { queryEditor, databaseName } = useSelector<
    SqlLabRootState,
    { queryEditor?: QueryEditor; databaseName?: string }
  >(({ sqlLab: { databases, queryEditors, tabHistory, unsavedQueryEditor } }) => {
    const queryEditorId = tabHistory.slice(-1)[0];
    const savedQueryEditor = queryEditors.find(
      editor => editor.id === queryEditorId,
    );
    const mergedQueryEditor = savedQueryEditor
      ? {
          ...savedQueryEditor,
          ...(unsavedQueryEditor?.id === savedQueryEditor.id &&
            unsavedQueryEditor),
        }
      : undefined;

    return {
      queryEditor: mergedQueryEditor,
      databaseName: mergedQueryEditor?.dbId
        ? databases[mergedQueryEditor.dbId]?.database_name
        : undefined,
    };
  }, shallowEqual);

  const context = useMemo<AiSqlAssistantContext>(
    () => ({
      databaseId: queryEditor?.dbId,
      databaseName,
      schema: queryEditor?.schema,
      currentSql: queryEditor?.sql,
    }),
    [databaseName, queryEditor?.dbId, queryEditor?.schema, queryEditor?.sql],
  );

  const {
    question,
    setQuestion,
    loading,
    error,
    result,
    generateSql,
  } = useAiSqlAssistant(provider, context);

  const copySql = useCallback(() => {
    if (!result?.sql) {
      return;
    }

    copyTextToClipboard(() => Promise.resolve(result.sql))
      .then(() => addSuccessToast(t('Copied to clipboard!')))
      .catch(() => addDangerToast(t('Failed to copy SQL.')));
  }, [addDangerToast, addSuccessToast, result?.sql]);

  const insertSql = useCallback(() => {
    if (queryEditor && result?.sql) {
      dispatch(queryEditorSetAndSaveSql(queryEditor, result.sql, undefined));
    }
  }, [dispatch, queryEditor, result?.sql]);

  return (
    <AiSqlAssistantPanel
      context={context}
      question={question}
      loading={loading}
      error={error}
      result={result}
      onQuestionChange={setQuestion}
      onGenerate={generateSql}
      onCopy={copySql}
      onInsert={insertSql}
    />
  );
};

export default AiSqlAssistant;
