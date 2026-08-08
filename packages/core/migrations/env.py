"""Alembic environment. URL and metadata both come from core."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool

from core.config import settings
from core.db import Base
from core import models  # noqa: F401  - import registers models on Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Manage only the objects this project defines.

    The PostGIS image enables postgis_tiger_geocoder and postgis_topology,
    which between them create ~40 tables (addrfeat, edges, tract, zip_lookup,
    place, zcta5, ...) plus spatial_ref_sys. A naive filter lets autogenerate
    read those as "removed" and emit DROP TABLE for every one — 69 destructive
    statements in what should be a create-only migration.

    Allow-list semantics, not deny-list: anything absent from Base.metadata is
    not ours and is left strictly alone.
    """
    if type_ == "table":
        return name in target_metadata.tables

    if type_ == "index" and reflected:
        parent = getattr(obj, "table", None)
        if parent is not None and parent.name not in target_metadata.tables:
            return False

    # GeoAlchemy2 creates the GIST index for a Geometry column as part of
    # CREATE TABLE. Its helper suppresses the duplicate explicit create_index
    # that autogenerate would otherwise emit — without this, the migration
    # dies on "relation idx_cities_bbox already exists".
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            # Renders Geometry(...) correctly in generated migrations and adds
            # the geoalchemy2 import; without these the file is not runnable.
            render_item=alembic_helpers.render_item,
            process_revision_directives=alembic_helpers.writer,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
