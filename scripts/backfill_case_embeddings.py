#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio

import asyncpg
from openai import OpenAI

from chatdba.cases.pgvector_retriever import build_case_embedding_text, vector_literal
from chatdba.cases.repository import optimization_case_from_row
from chatdba.config.settings import Settings
from chatdba.models.qwen_gateway import QwenGateway


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill embeddings for optimization_cases.")
    parser.add_argument("--limit", type=int, default=100, help="Max cases to backfill per run.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate embeddings even when the row already has one.",
    )
    args = parser.parse_args()
    asyncio.run(_backfill(limit=max(1, args.limit), overwrite=args.overwrite))


async def _backfill(*, limit: int, overwrite: bool) -> None:
    settings = Settings()
    gateway = QwenGateway(
        client=OpenAI(
            base_url=settings.qwen_base_url,
            api_key=settings.qwen_api_key,
        ),
        model=settings.qwen_model,
        embedding_model=settings.qwen_embedding_model,
    )
    connection = await asyncpg.connect(
        settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        rows = await connection.fetch(_select_sql(overwrite), limit)
        if not rows:
            print("No optimization cases need embedding backfill.")
            return

        for row in rows:
            case = optimization_case_from_row(dict(row))
            embedding_text = build_case_embedding_text(case)
            embedding = gateway.embed_text(embedding_text)
            await connection.execute(
                """
                UPDATE optimization_cases
                SET embedding = CAST($1 AS vector)
                WHERE case_id = $2
                """,
                vector_literal(embedding),
                case.case_id,
            )
            print(f"Backfilled case embedding: {case.case_id}")
    finally:
        await connection.close()


def _select_sql(overwrite: bool) -> str:
    where_clause = "" if overwrite else "WHERE embedding IS NULL"
    return f"""
        SELECT
            case_id,
            db_type,
            db_version,
            scenario_tags,
            plan_features,
            root_cause_tags,
            optimization_actions,
            case_card,
            full_text,
            quality_score
        FROM optimization_cases
        {where_clause}
        ORDER BY created_at DESC
        LIMIT $1
    """


if __name__ == "__main__":
    main()
