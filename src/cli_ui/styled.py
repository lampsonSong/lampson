"""CLI 样式和格式化工具。"""
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.panel import Panel
from rich.box import ROUNDED

console = Console()


class C:
    PROMPT = "bold ansigreen"
    USER_INPUT = "bold cyan"
    BOT = "bold green"
    COMMAND = "bold yellow"
    INFO = "blue"
    SUCCESS = "green"
    WARNING = "bold yellow"
    ERROR = "bold red"
    TOOL = "magenta"
    DIM = "dim"
    BORDER = "cyan"


def print_bot(text: str) -> None:
    console.print()
    console.print(Panel(
        text,
        box=ROUNDED,
        border_style=C.BOT,
        padding=(0, 1),
    ))
    console.print()


def print_command(text: str) -> None:
    console.print(f"[{C.COMMAND}]{text}[/{C.COMMAND}]")


def print_info(text: str) -> None:
    console.print(f"[{C.INFO}]{text}[/{C.INFO}]")


def print_success(text: str) -> None:
    console.print(f"[{C.SUCCESS}]{text}[/{C.SUCCESS}]")


def print_warning(text: str) -> None:
    console.print(f"[{C.WARNING}]{text}[/{C.WARNING}]")


def print_error(text: str) -> None:
    console.print(f"[{C.ERROR}]{text}[/{C.ERROR}]")


def print_tool(text: str) -> None:
    console.print(f"[{C.TOOL}]{text}[/{C.TOOL}]")


def print_divider(char: str = "─", width: int = 50) -> None:
    console.print(f"[{C.DIM}]{char * width}[/{C.DIM}]")


def print_table(title: str, headers: list[str], rows: list[list[str]]) -> None:
    table = Table(title=title, box=ROUNDED, show_header=True, header_style=C.INFO)
    for h in headers:
        table.add_column(h, style=C.USER_INPUT)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def create_progress() -> Progress:
    """创建进度条。"""
    try:
        from rich.progress import TaskProgressColumn
        return Progress(
            SpinnerColumn(),
            TextColumn("[{task.description}]"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        )
    except ImportError:
        return Progress(
            SpinnerColumn(),
            TextColumn("[{task.description}]"),
            BarColumn(),
            console=console,
        )


def print_banner() -> None:
    console.print(f"[{C.DIM}]{'─' * 60}[/{C.DIM}]")
