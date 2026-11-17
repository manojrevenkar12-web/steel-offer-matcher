import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from steelmatch.service.db import Base, database_url

target_metadata = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    url = context.config.attributes.get("url") or database_url()
    eng = create_async_engine(url)
    async with eng.connect() as conn:
        await conn.run_sync(run)
        await conn.commit()
    await eng.dispose()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    asyncio.run(main())
