"""Конфигурация приложения"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Путь к корню проекта
PROJECT_ROOT = Path(__file__).parent.parent

# OpenAI настройки
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Используем gpt-4o - самая новая и быстрая модель с лучшим пониманием контекста
# Альтернативы: "gpt-4-turbo" (новая версия turbo), "gpt-4o-mini" (быстрее и дешевле)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Настройки браузера
BROWSER_TYPE = os.getenv("BROWSER_TYPE", "chromium")  # chromium, firefox, webkit
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30000"))  # мс

# Настройки сессий
SESSION_DIR = PROJECT_ROOT / "sessions"
SESSION_DIR.mkdir(exist_ok=True)

# Настройки контекста
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "3000"))
MAX_HISTORY_TOKENS = int(os.getenv("MAX_HISTORY_TOKENS", "2000"))
# Максимальный размер запроса (TPM лимит минус запас для безопасности)
# Для gpt-4o TPM обычно 10000000, но оставляем запас для безопасности
MAX_REQUEST_TOKENS = int(os.getenv("MAX_REQUEST_TOKENS", "25000"))  # Запас для безопасности

# Настройки агента
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "50"))
ENABLE_SUB_AGENTS = os.getenv("ENABLE_SUB_AGENTS", "true").lower() == "true"

# Проверка обязательных переменных
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY не установлен в переменных окружения")

