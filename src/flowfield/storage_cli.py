"""Offline storage diagnostics and guarded recovery; never starts a service."""

import sqlite3
from collections.abc import Callable
from typing import Annotated, Any

import typer

from flowfield import storage
from flowfield.errors import ApplicationError


def register(app: typer.Typer) -> None:
    from flowfield.cli import Json, output

    commands = typer.Typer(
        no_args_is_help=True, help="Inspect storage and recover an upgrade offline."
    )
    app.add_typer(commands, name="storage")

    def run(action: Callable[[], Any], json_output: bool, human: Callable[[Any], None]) -> None:
        def checked() -> Any:
            try:
                return action()
            except sqlite3.Error as error:
                raise ApplicationError(
                    "invalid_database", f"Cannot read workspace database: {error}"
                ) from error

        output(checked, json_output, human)

    def show_status(value: dict[str, Any]) -> None:
        typer.echo(f"Database: {value['database']}")
        typer.echo(
            f"Schema: {value['schema_version'] or 'not initialized'}; "
            f"installed build: {value['supported_version']}"
        )
        if value["migration_required"]:
            typer.echo("A backed-up migration will run on the next service start.")
        elif value["schema_version"] and value["schema_version"] != value["supported_version"]:
            typer.echo("Incompatible schema; use a matching Flowfield build.")

    def show_backups(values: list[dict[str, Any]]) -> None:
        if not values:
            typer.echo("No pre-upgrade database backups.")
        for value in values:
            typer.echo(f"{value['id']} · schema {value['schema_version']} · {value['created_at']}")

    @commands.command("status")
    def status(ctx: typer.Context, json_output: Json = False) -> None:
        """Show schema compatibility without creating or upgrading a database."""
        run(lambda: storage.status(ctx.obj.directory), json_output, show_status)

    @commands.command("backups")
    def backups(ctx: typer.Context, json_output: Json = False) -> None:
        """List verified pre-upgrade database snapshots."""
        run(lambda: storage.backups(ctx.obj.directory), json_output, show_backups)

    @commands.command("restore")
    def restore(
        ctx: typer.Context,
        backup: str,
        confirm: Annotated[
            bool, typer.Option(help="Restore this database snapshot while stopped.")
        ] = False,
        json_output: Json = False,
    ) -> None:
        """Restore a pre-upgrade snapshot only if no work has changed since it was taken."""

        def recover() -> dict[str, str]:
            if not confirm:
                raise ApplicationError(
                    "confirmation_required",
                    "Review `flowfield storage backups`, "
                    "stop the service, then repeat with --confirm.",
                )
            return storage.restore(ctx.obj.directory, backup)

        run(
            recover,
            json_output,
            lambda value: typer.echo(
                f"{value['message']}\nSafety backup: {value['safety_backup']}"
            ),
        )
