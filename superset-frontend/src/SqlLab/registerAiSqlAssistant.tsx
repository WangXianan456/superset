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
import { views } from 'src/core';
import { ViewLocations } from 'src/SqlLab/contributions';
import AiSqlAssistant from 'src/SqlLab/components/AiSqlAssistant';

const ENABLE_AI_SQL_ASSISTANT_PLACEHOLDER = true;

if (ENABLE_AI_SQL_ASSISTANT_PLACEHOLDER) {
  views.registerView(
    {
      id: 'autosql-ai-assistant.placeholder',
      name: 'AutoSQL AI Assistant',
      description: 'Placeholder assistant for validating SQL Lab integration.',
    },
    ViewLocations.sqllab.rightSidebar,
    () => <AiSqlAssistant />,
  );
}
