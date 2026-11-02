from typer.testing import CliRunner

from evalgate import __version__
from evalgate.cli import EXIT_INVALID, app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_unimplemented_commands_fail_closed() -> None:
    # Fail closed: nothing may exit 0 before the engine exists.
    assert runner.invoke(app, ["validate", "x"]).exit_code == EXIT_INVALID
    assert runner.invoke(app, ["compare", "a", "b"]).exit_code == EXIT_INVALID
