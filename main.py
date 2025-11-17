"""Точка входа в приложение"""
import asyncio
import sys
import argparse
from src.cli.interface import CLIInterface


async def main(session_name: str = None, task: str = None):
    """Главная функция
    
    Args:
        session_name: Имя сессии для запуска (опционально)
        task: Задача для выполнения (опционально)
    """
    cli = CLIInterface()
    try:
        await cli.start(session_name=session_name, task=task)
    except KeyboardInterrupt:
        print("\n[yellow]Завершение работы...[/yellow]")
    finally:
        # Очистка ресурсов (если не была выполнена внутри start)
        if not task:
            await cli._cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebJARVIS - AI Browser Agent")
    parser.add_argument(
        "--session",
        type=str,
        help="Имя сессии для запуска (например: --session test)"
    )
    parser.add_argument(
        "--task",
        nargs=argparse.REMAINDER,
        help="Задача для выполнения (например: --task Прочитай последние 10 писем)"
    )
    
    args = parser.parse_args()
    
    # Объединяем все аргументы задачи в одну строку
    task = None
    if args.task:
        task = " ".join(args.task)
    
    try:
        asyncio.run(main(session_name=args.session, task=task))
    except KeyboardInterrupt:
        print("\n[yellow]Завершение работы...[/yellow]")
        sys.exit(0)

