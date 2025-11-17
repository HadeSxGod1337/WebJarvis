"""Обработка и адаптация ошибок"""
from typing import Dict, Any, Optional, Callable, List
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL
from .error_patterns import ErrorPatternMatcher
import hashlib
import json


class ErrorHandler:
    """Обработчик ошибок с адаптивной стратегией"""
    
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.error_history: List[Dict[str, Any]] = []
        self.max_retries = 3
        # Кэш решений для типичных ошибок (улучшенный с учетом контекста)
        self._error_solution_cache: Dict[str, Dict[str, Any]] = {}
    
    def _get_cache_key(self, error_message: str, action: str, context: str) -> str:
        """Генерация умного ключа кэша с учетом контекста"""
        # Нормализуем сообщение об ошибке (убираем динамические части)
        normalized_error = error_message.lower()
        # Убираем числа и специфичные значения
        for char in "0123456789":
            normalized_error = normalized_error.replace(char, "")
        
        # Берем первые 100 символов контекста для учета типа страницы
        context_snippet = context[:100].lower() if context else ""
        
        # Создаем ключ из действия, нормализованной ошибки и типа контекста
        key_data = {
            "action": action,
            "error": normalized_error[:200],
            "context_type": "modal" if "модальн" in context_snippet or "modal" in context_snippet else 
                           "form" if "форма" in context_snippet or "form" in context_snippet else
                           "empty" if len(context_snippet) < 20 else "normal"
        }
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _heuristic_error_handling(self, error_message: str, action: str) -> Optional[Dict[str, Any]]:
        """Быстрая эвристическая обработка ошибок без AI"""
        error_lower = error_message.lower()
        
        # Быстрые эвристики для частых ошибок
        
        # Элемент не найден
        if "not found" in error_lower or "не найден" in error_lower or "element not found" in error_lower:
            if action == "click_element":
                return {
                    "suggestion": "Элемент не найден на странице. Используйте query_dom для поиска элемента с правильным селектором, прокрутите страницу или попробуйте альтернативное описание элемента.",
                    "strategy": "scroll"
                }
            elif action == "extract_text":
                return {
                    "suggestion": "Элемент не найден. КРИТИЧЕСКИ ВАЖНО: НЕ ПОВТОРЯЙ extract_text с тем же описанием! Прокрутите страницу, используйте другое описание или переходите к следующему шагу задачи.",
                    "strategy": "scroll"
                }
            elif action == "type_text":
                return {
                    "suggestion": "Поле ввода не найдено. Используйте query_dom для поиска поля с правильным селектором или проверьте, открыта ли форма.",
                    "strategy": "alternative"
                }
        
        # Таймаут
        if "timeout" in error_lower or "таймаут" in error_lower or "timed out" in error_lower:
            return {
                "suggestion": "Превышен таймаут ожидания. Страница может загружаться медленно или элемент появляется с задержкой. Подождите загрузки страницы и попробуйте снова.",
                "strategy": "wait"
            }
        
        # Элемент не виден
        if "not visible" in error_lower or "не виден" in error_lower or "is not visible" in error_lower:
            return {
                "suggestion": "Элемент не виден на экране. Прокрутите страницу до элемента, закройте перекрывающие модальные окна или подождите его появления.",
                "strategy": "scroll_to_element"
            }
        
        # Элемент отключен
        if "disabled" in error_lower or "отключен" in error_lower or "is disabled" in error_lower:
            return {
                "suggestion": "Элемент отключен и недоступен для взаимодействия. Попробуйте другой элемент, выполните необходимые действия для активации или подождите активации элемента.",
                "strategy": "wait"
            }
        
        # Элемент перекрыт другим элементом
        if "intercepted" in error_lower or "перекрыт" in error_lower or "obscured" in error_lower or "is not clickable" in error_lower:
            return {
                "suggestion": "Элемент перекрыт другим элементом (модальное окно, баннер, overlay). Закройте перекрывающие элементы или прокрутите страницу, чтобы элемент стал доступен.",
                "strategy": "close_modals"
            }
        
        # Неверный селектор
        if "invalid selector" in error_lower or "неверный селектор" in error_lower or "malformed" in error_lower:
            return {
                "suggestion": "Неверный селектор элемента. Используйте query_dom для получения правильного селектора элемента или используйте описание элемента вместо селектора.",
                "strategy": "alternative_description"
            }
        
        # Страница не загрузилась
        if "page not loaded" in error_lower or "страница не загружена" in error_lower or "navigation" in error_lower:
            return {
                "suggestion": "Страница еще не загрузилась полностью. Подождите завершения загрузки страницы и попробуйте снова.",
                "strategy": "wait"
            }
        
        # Элемент вне области видимости
        if "out of viewport" in error_lower or "вне области видимости" in error_lower or "not in viewport" in error_lower:
            return {
                "suggestion": "Элемент находится вне области видимости. Прокрутите страницу до элемента или используйте scroll_to_element.",
                "strategy": "scroll_to_element"
            }
        
        # Ошибка сети
        if "network" in error_lower or "сеть" in error_lower or "connection" in error_lower:
            return {
                "suggestion": "Ошибка сети при загрузке страницы. Проверьте подключение к интернету и попробуйте снова через несколько секунд.",
                "strategy": "wait"
            }
        
        # JavaScript ошибка
        if "javascript" in error_lower or "js error" in error_lower or "script error" in error_lower:
            return {
                "suggestion": "Ошибка JavaScript на странице. Попробуйте другой подход или обновите страницу через navigate.",
                "strategy": "alternative"
            }
        
        # Элемент удален из DOM
        if "detached" in error_lower or "stale" in error_lower or "удален" in error_lower:
            return {
                "suggestion": "Элемент был удален из DOM или страница изменилась. Обновите контекст страницы через query_dom и попробуйте найти элемент снова.",
                "strategy": "alternative"
            }
        
        return None
    
    async def handle_error(
        self, 
        error: Exception, 
        action: str, 
        context: str,
        retry_count: int = 0
    ) -> Dict[str, Any]:
        """
        Обработка ошибки с анализом и предложением решения
        
        Args:
            error: Исключение
            action: Действие, которое вызвало ошибку
            context: Контекст страницы
            retry_count: Количество попыток повтора
            
        Returns:
            Результат обработки с предложенным решением
        """
        error_info = {
            "error": str(error),
            "action": action,
            "retry_count": retry_count,
            "context": context[:500]  # Ограничиваем контекст
        }
        
        self.error_history.append(error_info)
        
        # Если превышен лимит попыток
        if retry_count >= self.max_retries:
            return {
                "success": False,
                "error": str(error),
                "suggestion": "Превышен лимит попыток. Требуется помощь пользователя.",
            }
        
        error_message = str(error)
        
        # 1. Быстрая эвристическая обработка (самый быстрый способ)
        heuristic_result = self._heuristic_error_handling(error_message, action)
        if heuristic_result:
            suggestion = heuristic_result["suggestion"]
            strategy = heuristic_result["strategy"]
        else:
            # 2. Проверяем паттерны ошибок (быстро и без AI)
            pattern_solution = ErrorPatternMatcher.get_solution(error_message, action)
            
            if pattern_solution:
                # Используем решение из паттерна
                suggestion = pattern_solution["solution"]
                strategy = pattern_solution["strategy"]
                
                # Для extract_text добавляем дополнительные рекомендации
                if action == "extract_text":
                    suggestion += " ВАЖНО: НЕ ПОВТОРЯЙ extract_text с тем же описанием! Используй альтернативные стратегии."
            else:
                # 3. Если паттерн не найден - проверяем улучшенный кэш
                cache_key = self._get_cache_key(error_message, action, context)
                if cache_key in self._error_solution_cache:
                    cached_solution = self._error_solution_cache[cache_key]
                    suggestion = cached_solution["suggestion"]
                    strategy = cached_solution.get("strategy")
                else:
                    # 4. Используем AI только для новых типов ошибок
                    suggestion = await self._analyze_error(error, action, context)
                    strategy = self._extract_strategy_from_suggestion(suggestion)
                    # Сохраняем в кэш с улучшенным ключом
                    self._error_solution_cache[cache_key] = {
                        "suggestion": suggestion,
                        "strategy": strategy
                    }
                    
                    # Ограничиваем размер кэша (храним последние 200 результатов)
                    if len(self._error_solution_cache) > 200:
                        # Удаляем самый старый элемент
                        oldest_key = next(iter(self._error_solution_cache))
                        del self._error_solution_cache[oldest_key]
        
        return {
            "success": False,
            "error": error_message,
            "suggestion": suggestion,
            "strategy": strategy,
            "should_retry": retry_count < self.max_retries,
            "retry_count": retry_count + 1
        }
    
    async def _analyze_error(self, error: Exception, action: str, context: str) -> str:
        """
        Анализ ошибки через AI для предложения решения
        
        Args:
            error: Исключение
            action: Действие
            context: Контекст
            
        Returns:
            Предложение по решению проблемы
        """
        prompt = f"""<role>
Ты эксперт по анализу ошибок автоматизации браузера. Твоя задача - проанализировать ошибку и предложить конкретное решение.
</role>

<error_info>
Действие: {action}
Ошибка: {str(error)}
Контекст страницы: {context[:1000]}
</error_info>

<analysis>
Проанализируй ошибку по шагам:

1. Тип ошибки:
   - Элемент не найден?
   - Элемент не виден?
   - Элемент заблокирован?
   - Страница не загрузилась?
   - Неверный селектор?
   - Другая причина?

2. Контекст действия:
   - Что пытались сделать?
   - На какой странице?
   - Есть ли модальные окна или формы?

3. Решение:
   - Какое конкретное действие нужно предпринять?
   - Какая стратегия повтора подходит?
</analysis>

<solution>
Предложи конкретное решение для повторной попытки. Решение должно быть:
- Конкретным и выполнимым
- Учитывать тип действия ({action})
- Предлагать альтернативные подходы если основной не работает
- Избегать повторения того же действия без изменений
</solution>"""

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Ты помощник для анализа ошибок автоматизации браузера."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.3
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Не удалось проанализировать ошибку: {str(e)}. Попробуй прокрутить страницу или использовать другой подход."
    
    def should_retry(self, error_result: Dict[str, Any]) -> bool:
        """Проверка, стоит ли повторять действие"""
        return error_result.get("should_retry", False) and \
               error_result.get("retry_count", 0) < self.max_retries
    
    def _extract_strategy_from_suggestion(self, suggestion: str) -> Optional[str]:
        """
        Извлечение стратегии повтора из предложения AI
        
        Args:
            suggestion: Предложение от AI
            
        Returns:
            Стратегия повтора
        """
        suggestion_lower = suggestion.lower()
        
        # Улучшенные эвристики для определения стратегии
        if "поиск" in suggestion_lower or "search" in suggestion_lower or "поисков" in suggestion_lower:
            if "строк" in suggestion_lower or "search" in suggestion_lower:
                return "use_search"
        if "прокрут" in suggestion_lower or "scroll" in suggestion_lower:
            if "и повтори" in suggestion_lower or "and retry" in suggestion_lower or "повтори" in suggestion_lower:
                return "scroll_and_retry"
            elif "до элемента" in suggestion_lower or "to element" in suggestion_lower:
                return "scroll_to_element"
            return "scroll"
        elif "альтернатив" in suggestion_lower or "alternative" in suggestion_lower or "другое описание" in suggestion_lower:
            if "описание" in suggestion_lower or "description" in suggestion_lower:
                return "alternative_description"
            return "alternative"
        elif "подождать" in suggestion_lower or "wait" in suggestion_lower or "загрузк" in suggestion_lower:
            return "wait"
        elif "закрыть" in suggestion_lower or "close" in suggestion_lower or "модальн" in suggestion_lower:
            return "close_modals"
        
        return "wait"  # По умолчанию - ожидание
    
    def get_retry_strategy(self, error_result: Dict[str, Any]) -> Optional[str]:
        """
        Получение стратегии повтора из результата обработки ошибки
        
        Args:
            error_result: Результат обработки ошибки
            
        Returns:
            Стратегия повтора
        """
        # Сначала проверяем явно указанную стратегию
        strategy = error_result.get("strategy")
        if strategy:
            return strategy
        
        # Если стратегия не указана - извлекаем из предложения
        suggestion = error_result.get("suggestion", "")
        return self._extract_strategy_from_suggestion(suggestion)
    
    def clear_history(self):
        """Очистка истории ошибок"""
        self.error_history = []

