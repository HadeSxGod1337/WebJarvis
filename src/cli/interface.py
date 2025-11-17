"""CLI интерфейс для взаимодействия с агентом"""
import asyncio
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.live import Live
from rich.table import Table
from rich.text import Text

from src.browser.controller import BrowserController
from src.browser.session_manager import SessionManager
from src.agent.main_agent import MainAgent, Logger


class CLIInterface:
    """Интерфейс командной строки для агента"""
    
    def __init__(self):
        self.console = Console()
        self.browser_controller: Optional[BrowserController] = None
        self.agent: Optional[MainAgent] = None
        self.session_manager = SessionManager()
        self.current_session: Optional[str] = None
        # Создаем логгер с callback для красивого вывода
        self.logger = Logger(log_callback=self._log_message)
    
    def print_banner(self):
        """Вывод баннера приложения"""
        banner = """
╔═══════════════════════════════════════════════════════╗
║          WebJARVIS - AI Browser Agent                 ║
║     Автономный AI-агент для автоматизации браузера   ║
╚═══════════════════════════════════════════════════════╝
        """
        self.console.print(banner, style="bold cyan")
    
    def print_help(self):
        """Вывод справки"""
        help_text = """
Доступные команды:
  help              - Показать эту справку
  task <описание>   - Выполнить задачу
  session <name>    - Использовать сессию (для persistent sessions)
                      ВАЖНО: Используйте сессию ДО входа в аккаунты!
  list-sessions     - Список доступных сессий
  status            - Текущий статус агента
  quit / exit       - Выход

Примеры использования:
  session my_account    - Создать/использовать сессию "my_account"
  task Найди вакансии Python разработчика на hh.ru
  task Найди информацию о погоде в Москве

Запуск с параметрами из командной строки:
  python main.py --session test    - Запустить сразу с сессией "test"
  python main.py --task "Найди вакансии Python"  - Выполнить задачу без сессии
  python main.py --session test --task "Прочитай последние 10 писем"  - Запустить с сессией и выполнить задачу

ВАЖНО про сессии:
  - Используйте команду 'session <имя>' ПЕРЕД входом в аккаунты
  - Или запускайте с --session при старте: python main.py --session <имя>
  - После входа в аккаунт закройте программу через 'quit'
  - При следующем запуске используйте ту же сессию - аккаунт будет сохранен
        """
        self.console.print(Panel(help_text, title="Справка", border_style="blue"))
    
    async def start(self, session_name: Optional[str] = None, task: Optional[str] = None):
        """Запуск CLI интерфейса
        
        Args:
            session_name: Имя сессии для автоматической инициализации (опционально)
            task: Задача для автоматического выполнения (опционально)
        """
        self.print_banner()
        
        # Инициализация браузера
        await self._initialize_browser(session_name=session_name)
        
        # Если задача передана - выполняем её сразу
        if task:
            await self._execute_task(task)
            # После выполнения задачи выходим (не входим в интерактивный режим)
            await self._cleanup()
            return
        
        # Если задача не передана - показываем справку и входим в интерактивный режим
        self.print_help()
        
        # Главный цикл
        while True:
            try:
                command = Prompt.ask("\n[bold cyan]WebJARVIS[/bold cyan]").strip()
                
                if not command:
                    continue
                
                if command.lower() in ["quit", "exit", "q"]:
                    await self._cleanup()
                    self.console.print("\n[bold green]До свидания![/bold green]")
                    break
                
                elif command.lower() == "help":
                    self.print_help()
                
                elif command.lower().startswith("task "):
                    task = command[5:].strip()
                    if task:
                        await self._execute_task(task)
                    else:
                        self.console.print("[red]Ошибка: Укажите описание задачи[/red]")
                
                elif command.lower().startswith("session "):
                    session_name = command[8:].strip()
                    if session_name:
                        await self._switch_session(session_name)
                    else:
                        self.console.print("[red]Ошибка: Укажите имя сессии[/red]")
                
                elif command.lower() == "list-sessions":
                    self._list_sessions()
                
                elif command.lower() == "status":
                    self._show_status()
                
                else:
                    self.console.print(f"[yellow]Неизвестная команда: {command}[/yellow]")
                    self.console.print("Используйте 'help' для справки")
            
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Прервано пользователем[/yellow]")
                await self._cleanup()
                break
            except Exception as e:
                self.console.print(f"[red]Ошибка: {str(e)}[/red]")
    
    async def _initialize_browser(self, session_name: Optional[str] = None):
        """Инициализация браузера"""
        self.console.print("[yellow]Инициализация браузера...[/yellow]")
        
        try:
            user_data_dir = None
            if session_name:
                # Создаем сессию если её нет
                if not self.session_manager.session_exists(session_name):
                    self.session_manager.create_session(session_name)
                    self.console.print(f"[green]Создана новая сессия: {session_name}[/green]")
                else:
                    self.console.print(f"[green]Загрузка существующей сессии: {session_name}[/green]")
                user_data_dir = self.session_manager.get_user_data_dir(session_name)
                self.current_session = session_name
            else:
                self.console.print("[yellow]Внимание: Сессия не указана. Состояние браузера не будет сохранено.[/yellow]")
                self.console.print("[dim]Используйте команду 'session <имя>' для сохранения состояния[/dim]")
            
            self.browser_controller = BrowserController(user_data_dir=user_data_dir)
            await self.browser_controller.start()
            
            # Создание агента с callback для подтверждения и логгером
            self.agent = MainAgent(
                self.browser_controller,
                user_confirmation_callback=self._ask_user_confirmation,
                logger=self.logger
            )
            
            if session_name:
                self.console.print(f"[green]Браузер запущен с сессией '{session_name}'[/green]")
                self.console.print("[dim]Вы можете войти в аккаунты - состояние будет сохранено[/dim]")
            else:
                self.console.print("[green]Браузер запущен[/green]")
        except Exception as e:
            self.console.print(f"[red]Ошибка при инициализации браузера: {str(e)}[/red]")
            raise
    
    async def _execute_task(self, task: str):
        """Выполнение задачи"""
        if not self.agent:
            self.console.print("[red]Агент не инициализирован[/red]")
            return
        
        self.console.print(f"\n[bold cyan]Выполнение задачи:[/bold cyan] {task}\n")
        self.console.print("[dim]Нажмите Ctrl+C для прерывания выполнения[/dim]\n")
        
        # Создаем таблицу для отображения прогресса
        progress_table = Table(show_header=True, header_style="bold magenta")
        progress_table.add_column("Итерация", style="cyan")
        progress_table.add_column("Действие", style="yellow")
        progress_table.add_column("Результат", style="green")
        
        try:
            # Выполнение задачи
            result = await self.agent.execute_task(task)
            
            if result.get("success"):
                self.console.print(f"\n[bold green]✓ Задача выполнена успешно![/bold green]")
                if result.get("message"):
                    self.console.print(f"[green]{result['message']}[/green]")
                if result.get("iterations"):
                    self.console.print(f"[dim]Итераций выполнено: {result['iterations']}[/dim]")
            else:
                if result.get("interrupted"):
                    self.console.print(f"\n[yellow]⚠ Выполнение задачи прервано пользователем[/yellow]")
                elif result.get("needs_user_input"):
                    self.console.print(f"\n[yellow]⚠ Требуется помощь пользователя:[/yellow]")
                    self.console.print(f"[yellow]{result.get('question', '')}[/yellow]")
                    answer = Prompt.ask("Ваш ответ")
                    # Можно продолжить выполнение с ответом пользователя
                else:
                    self.console.print(f"\n[bold red]✗ Ошибка при выполнении задачи[/bold red]")
                    if result.get("error"):
                        self.console.print(f"[red]{result['error']}[/red]")
                    if result.get("suggestion"):
                        self.console.print(f"[yellow]Предложение: {result['suggestion']}[/yellow]")
        
        except KeyboardInterrupt:
            self.console.print(f"\n[yellow]⚠ Прерывание выполнения задачи...[/yellow]")
            if self.agent:
                self.agent.stop()
            # Даем время на graceful shutdown
            await asyncio.sleep(0.5)
            self.console.print("[yellow]Выполнение остановлено[/yellow]")
        except Exception as e:
            self.console.print(f"\n[bold red]Критическая ошибка:[/bold red] {str(e)}")
    
    def _ask_user_confirmation(self, message: str) -> bool:
        """Запрос подтверждения у пользователя"""
        return Confirm.ask(f"[yellow]⚠ {message}[/yellow]")
    
    def _log_message(self, level: str, message: str):
        """Вывод лога с форматированием через rich"""
        level_colors = {
            "INFO": "cyan",
            "SUCCESS": "green",
            "WARNING": "yellow",
            "ERROR": "red"
        }
        color = level_colors.get(level, "white")
        self.console.print(f"[{color}][{level}][/{color}] {message}")
    
    async def _switch_session(self, session_name: str):
        """Переключение на другую сессию"""
        self.console.print(f"[yellow]Переключение на сессию: {session_name}[/yellow]")
        
        # Сохраняем текущую сессию перед переключением
        if self.browser_controller:
            self.console.print("[dim]Сохранение текущей сессии...[/dim]")
            await self.browser_controller.close()
            # Даем время на сохранение данных
            import asyncio
            await asyncio.sleep(1)
        
        # Инициализируем новую сессию
        await self._initialize_browser(session_name)
        self.console.print(f"[green]Сессия '{session_name}' активирована[/green]")
    
    def _list_sessions(self):
        """Вывод списка сессий"""
        sessions = self.session_manager.list_sessions()
        
        if not sessions:
            self.console.print("[yellow]Нет сохраненных сессий[/yellow]")
            return
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Имя сессии", style="cyan")
        table.add_column("Статус", style="green")
        
        for session in sessions:
            status = "активна" if session == self.current_session else "неактивна"
            table.add_row(session, status)
        
        self.console.print(table)
    
    def _show_status(self):
        """Отображение текущего статуса"""
        if not self.agent:
            self.console.print("[yellow]Агент не инициализирован[/yellow]")
            return
        
        state_info = self.agent.get_state()
        
        status_table = Table(show_header=False, box=None)
        status_table.add_column("Параметр", style="cyan")
        status_table.add_column("Значение", style="yellow")
        
        status_table.add_row("Состояние", state_info.get("state", "unknown"))
        status_table.add_row("Задача", state_info.get("task", "нет") or "нет")
        status_table.add_row("Итераций", str(state_info.get("iteration_count", 0)))
        status_table.add_row("Действий", str(state_info.get("actions_count", 0)))
        
        if state_info.get("last_error"):
            status_table.add_row("Последняя ошибка", state_info.get("last_error"))
        
        self.console.print(Panel(status_table, title="Статус агента", border_style="blue"))
    
    async def _cleanup(self):
        """Очистка ресурсов"""
        if self.browser_controller:
            if self.current_session:
                self.console.print(f"[dim]Сохранение сессии '{self.current_session}'...[/dim]")
            await self.browser_controller.close()
            # Даем время на сохранение данных для persistent sessions
            if self.current_session:
                import asyncio
                await asyncio.sleep(1)
                self.console.print(f"[green]Сессия '{self.current_session}' сохранена[/green]")

