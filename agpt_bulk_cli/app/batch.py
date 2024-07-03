import typer

batch_app = typer.Typer(name="batch", no_args_is_help=True)

@batch_app.command()
def prepare():
    print("Under construction")