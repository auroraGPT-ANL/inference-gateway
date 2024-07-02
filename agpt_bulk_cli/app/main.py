import logging
import os
from pathlib import Path
from typing import Optional

import typer
import rich

from agpt_bulk_cli.app.batch import batch_app

logger = logging.getLogger()

app = typer.Typer(no_args_is_help=True)


# nb: subcommands are mini typer apps in their own right
app.add_typer(batch_app)


def show_version(show: bool):
    """Display the installed version and quit."""
    version_str = f"agpt-bulk-cli 0.0.0"
    rich.print(version_str)    
    raise typer.Exit()

@app.callback()
def main_info(
    version: Optional[bool] = typer.Option(
        None, "--version", is_eager=True
    )
):
    """
    🌱 Hello, World 🌱
    """
    pass
