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
import { ChangeEvent } from 'react';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import { Button, Input } from '@superset-ui/core/components';
import type {
  AiSqlAssistantContext,
  AiSqlAssistantResult,
  AiSqlSuggestedTable,
} from './types';

const Panel = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    height: 100%;
    padding: ${theme.sizeUnit * 4}px;
    gap: ${theme.sizeUnit * 3}px;
    color: ${theme.colorText};
    background: ${theme.colorBgBase};
  `}
`;

const Header = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    padding-bottom: ${theme.sizeUnit * 2}px;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const Title = styled.h3`
  ${({ theme }) => css`
    margin: 0;
    font-size: ${theme.fontSizeLG}px;
    font-weight: ${theme.fontWeightStrong};
  `}
`;

const ContextText = styled.div`
  ${({ theme }) => css`
    font-size: ${theme.fontSizeSM}px;
    color: ${theme.colorTextSecondary};
    overflow-wrap: anywhere;
  `}
`;

const Actions = styled.div`
  ${({ theme }) => css`
    display: flex;
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

const TableList = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    max-height: 180px;
    overflow: auto;
    padding: ${theme.sizeUnit * 2}px;
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    background: ${theme.colorBgContainer};
  `}
`;

const TableOption = styled.label`
  ${({ theme }) => css`
    display: flex;
    align-items: flex-start;
    gap: ${theme.sizeUnit * 2}px;
    font-size: ${theme.fontSizeSM}px;
    cursor: pointer;

    input {
      flex: 0 0 auto;
      margin-top: 2px;
    }
  `}
`;

const TableOptionMeta = styled.span`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    margin-left: ${theme.sizeUnit}px;
  `}
`;

const SqlBlock = styled.pre`
  ${({ theme }) => css`
    flex: 0 0 auto;
    max-height: 280px;
    overflow: auto;
    margin: 0;
    padding: ${theme.sizeUnit * 3}px;
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    background: ${theme.colorBgLayout};
    font-size: ${theme.fontSizeSM}px;
    white-space: pre-wrap;
  `}
`;

const ResultSection = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    min-height: 0;
  `}
`;

export type AiSqlAssistantPanelProps = {
  context: AiSqlAssistantContext;
  question: string;
  loading: boolean;
  suggestingTables: boolean;
  error: string | null;
  result: AiSqlAssistantResult | null;
  suggestedTables: AiSqlSuggestedTable[];
  selectedTables: string[];
  onQuestionChange: (question: string) => void;
  onSuggestTables: () => void;
  onToggleTable: (tableName: string) => void;
  onGenerate: () => void;
  onCopy: () => void;
  onInsert: () => void;
};

const formatContext = (context: AiSqlAssistantContext) =>
  [
    context.databaseName ?? context.databaseId ?? t('No database selected'),
    context.schema ? `${t('schema')}: ${context.schema}` : t('default schema'),
  ].join(' / ');

const AiSqlAssistantPanel = ({
  context,
  question,
  loading,
  suggestingTables,
  error,
  result,
  suggestedTables,
  selectedTables,
  onQuestionChange,
  onSuggestTables,
  onToggleTable,
  onGenerate,
  onCopy,
  onInsert,
}: AiSqlAssistantPanelProps) => (
  <Panel data-test="autosql-ai-assistant">
    <Header>
      <Title>{t('AutoSQL AI Assistant')}</Title>
      <ContextText>{formatContext(context)}</ContextText>
    </Header>

    <Input.TextArea
      value={question}
      rows={5}
      placeholder={t('Describe the data question you want to answer')}
      onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
        onQuestionChange(event.target.value)
      }
    />

    <Actions>
      <Button
        disabled={loading || suggestingTables}
        onClick={onSuggestTables}
        block
      >
        {suggestingTables ? t('Finding tables...') : t('Suggest tables')}
      </Button>
      <Button
        buttonStyle="primary"
        disabled={loading || suggestingTables}
        onClick={onGenerate}
        block
      >
        {loading ? t('Generating...') : t('Generate SQL')}
      </Button>
    </Actions>

    {suggestedTables.length > 0 && (
      <ResultSection>
        <ContextText>
          {t('Suggested tables')}: {selectedTables.length}/
          {suggestedTables.length}
        </ContextText>
        <TableList>
          {suggestedTables.map(table => (
            <TableOption key={table.name}>
              <input
                type="checkbox"
                checked={selectedTables.includes(table.name)}
                onChange={() => onToggleTable(table.name)}
              />
              <span>
                {table.name}
                <TableOptionMeta>
                  {table.type ? `${table.type} / ` : ''}
                  {t('score')}: {table.score}
                </TableOptionMeta>
              </span>
            </TableOption>
          ))}
        </TableList>
      </ResultSection>
    )}

    {error && <Alert type="error" message={error} closable={false} />}

    {result && (
      <ResultSection>
        <SqlBlock>{result.sql}</SqlBlock>

        {result.tables.length > 0 && (
          <ContextText>
            {t('Tables')}: {result.tables.join(', ')}
          </ContextText>
        )}

        {result.explanation && (
          <Alert
            type="info"
            message={result.explanation}
            closable={false}
          />
        )}

        {result.warnings?.map(warning => (
          <Alert
            key={warning}
            type="warning"
            message={warning}
            closable={false}
          />
        ))}

        <Actions>
          <Button onClick={onCopy}>{t('Copy')}</Button>
          <Button buttonStyle="primary" onClick={onInsert}>
            {t('Insert')}
          </Button>
        </Actions>
      </ResultSection>
    )}
  </Panel>
);

export default AiSqlAssistantPanel;
