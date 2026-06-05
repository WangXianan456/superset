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
from __future__ import annotations

import re
from typing import Any

from flask import current_app

MIN_TOKEN_LENGTH = 2


def tokenize_text(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text or "")
        if len(token) >= MIN_TOKEN_LENGTH
    }


def _normalize_aliases(raw_aliases: Any) -> dict[str, set[str]]:
    if not isinstance(raw_aliases, dict):
        return {}

    normalized: dict[str, set[str]] = {}
    for keyword, aliases in raw_aliases.items():
        keyword_text = str(keyword or "").strip()
        if not keyword_text:
            continue

        alias_tokens = set()
        if isinstance(aliases, str):
            alias_tokens.update(tokenize_text(aliases.replace(",", " ")))
        elif isinstance(aliases, (list, tuple, set)):
            for alias in aliases:
                alias_tokens.update(tokenize_text(str(alias or "").replace("_", " ")))
                alias_text = str(alias or "").strip().lower()
                if alias_text:
                    alias_tokens.add(alias_text)

        if alias_tokens:
            normalized[keyword_text] = alias_tokens

    return normalized


def get_business_aliases(base_aliases: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
    aliases = dict(base_aliases or {})
    configured_aliases = current_app.config.get("AI_SQL_BUSINESS_ALIASES", {})
    aliases.update(_normalize_aliases(configured_aliases))

    assistant_config = current_app.config.get("AI_SQL_ASSISTANT", {})
    if isinstance(assistant_config, dict):
        aliases.update(_normalize_aliases(assistant_config.get("business_aliases")))

    return aliases


def expand_question_tokens(
    question: str,
    base_aliases: dict[str, set[str]] | None = None,
) -> set[str]:
    tokens = tokenize_text(question)
    for keyword, aliases in get_business_aliases(base_aliases).items():
        if keyword in question:
            tokens.update(aliases)
    return tokens
