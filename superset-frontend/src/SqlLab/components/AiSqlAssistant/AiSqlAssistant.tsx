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
  >(
    ({
      sqlLab: { databases, queryEditors, tabHistory, unsavedQueryEditor },
    }) => {
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
    },
    shallowEqual,
  );

  const context = useMemo<AiSqlAssistantContext>(
    () => ({
      databaseId: queryEditor?.dbId,
      databaseName,
      catalog: queryEditor?.catalog,
      schema: queryEditor?.schema,
      currentSql: queryEditor?.sql,
    }),
    [
      databaseName,
      queryEditor?.catalog,
      queryEditor?.dbId,
      queryEditor?.schema,
      queryEditor?.sql,
    ],
  );

  const {
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
  } = useAiSqlAssistant(provider, context);

  const copySql = useCallback(() => {
    if (!result?.sql) {
      return;
    }

    copyTextToClipboard(() => Promise.resolve(result.sql))
      .then(() => {
        addSuccessToast(t('Copied to clipboard!'));
        sendFeedback({ copied: true, accepted: true });
      })
      .catch(() => addDangerToast(t('Failed to copy SQL.')));
  }, [addDangerToast, addSuccessToast, result?.sql, sendFeedback]);

  const insertSql = useCallback(() => {
    if (queryEditor && result?.sql) {
      dispatch(queryEditorSetAndSaveSql(queryEditor, result.sql, undefined));
      sendFeedback({ inserted: true, accepted: true });
    }
  }, [dispatch, queryEditor, result?.sql, sendFeedback]);

  const acceptResult = useCallback(() => {
    sendFeedback({ accepted: true });
  }, [sendFeedback]);

  const rejectResult = useCallback(() => {
    sendFeedback({ accepted: false });
  }, [sendFeedback]);

  return (
    <AiSqlAssistantPanel
      context={context}
      question={question}
      loading={loading}
      suggestingTables={suggestingTables}
      refreshing={refreshing}
      error={error}
      result={result}
      suggestedTables={suggestedTables}
      selectedTables={selectedTables}
      metadataStatus={metadataStatus}
      feedbackSent={feedbackSent}
      onQuestionChange={setQuestion}
      onSuggestTables={suggestTables}
      onToggleTable={toggleSelectedTable}
      onGenerate={generateSql}
      onRefreshMetadata={refreshMetadata}
      onAcceptResult={acceptResult}
      onRejectResult={rejectResult}
      onCopy={copySql}
      onInsert={insertSql}
    />
  );
};

export default AiSqlAssistant;
