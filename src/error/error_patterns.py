"""Паттерны ошибок и их решения"""

from typing import Dict, Any, Optional, List


class ErrorPattern:
    """Паттерн ошибки с решением"""
    
    def __init__(self, error_keywords: List[str], solution: str, strategy: str):
        """
        Инициализация паттерна ошибки
        
        Args:
            error_keywords: Ключевые слова в сообщении об ошибке
            solution: Решение проблемы
            strategy: Стратегия повтора (scroll, wait, alternative, scroll_to_element)
        """
        self.error_keywords = error_keywords
        self.solution = solution
        self.strategy = strategy
    
    def matches(self, error_message: str) -> bool:
        """
        Проверка, соответствует ли ошибка паттерну
        
        Args:
            error_message: Сообщение об ошибке
            
        Returns:
            True если ошибка соответствует паттерну
        """
        error_lower = error_message.lower()
        return any(keyword.lower() in error_lower for keyword in self.error_keywords)


class ErrorPatternMatcher:
    """Сопоставление ошибок с паттернами и решениями"""
    
    # Паттерны ошибок с решениями
    PATTERNS = [
        # Элемент не найден
        ErrorPattern(
            ["element not found", "элемент не найден", "selector not found", "селектор не найден"],
            "Элемент не найден на странице. Попробуйте прокрутить страницу или использовать другое описание элемента.",
            "scroll"
        ),
        # Элемент не виден
        ErrorPattern(
            ["element is not visible", "элемент не виден", "not visible", "not attached", "не прикреплен"],
            "Элемент не виден на странице. Прокрутите страницу до элемента или подождите его загрузки.",
            "scroll_to_element"
        ),
        # Элемент перехватывается другим элементом
        ErrorPattern(
            ["intercepts pointer events", "intercepts", "перехватывает", "заблокирован"],
            "Элемент заблокирован другим элементом. Попробуйте закрыть модальные окна или подождать.",
            "wait"
        ),
        # Таймаут ожидания
        ErrorPattern(
            ["timeout", "таймаут", "timeout exceeded", "превышен таймаут"],
            "Превышен таймаут ожидания. Элемент еще не загрузился. Подождите и попробуйте снова.",
            "wait"
        ),
        # Страница не загрузилась
        ErrorPattern(
            ["page not loaded", "страница не загружена", "navigation timeout", "навигация"],
            "Страница еще не загрузилась полностью. Подождите загрузки страницы.",
            "wait"
        ),
        # Неверный селектор
        ErrorPattern(
            ["invalid selector", "неверный селектор", "malformed selector", "неправильный селектор"],
            "Неверный селектор элемента. Попробуйте использовать другое описание элемента.",
            "alternative"
        ),
        # Элемент вне viewport
        ErrorPattern(
            ["outside viewport", "вне viewport", "not in viewport", "не в области видимости"],
            "Элемент находится вне области видимости. Прокрутите страницу до элемента.",
            "scroll_to_element"
        ),
        # Элемент отключен
        ErrorPattern(
            ["element is disabled", "элемент отключен", "disabled", "неактивен"],
            "Элемент отключен и недоступен для взаимодействия. Попробуйте другой элемент или подождите активации.",
            "wait"
        ),
        # Ошибка Page.evaluate() - неправильное количество аргументов
        ErrorPattern(
            ["Page.evaluate() takes from 2 to 3 positional arguments but", "takes from 2 to 3 positional arguments"],
            "Ошибка при извлечении текста из элемента. Попробуйте прокрутить страницу (scroll) и использовать extract_text с другим описанием элемента или извлечь видимый текст страницы другим способом.",
            "scroll"
        ),
        # Ошибка извлечения текста - элемент не найден
        ErrorPattern(
            ["не удалось найти элемент", "элемент не найден для извлечения", "для извлечения текста"],
            "Элемент для извлечения текста не найден. НЕ ПОВТОРЯЙ extract_text с тем же описанием! Вместо этого: 1) Прокрутите страницу (scroll), 2) Попробуйте extract_text с другим описанием, 3) Используйте видимый текст страницы.",
            "scroll"
        ),
        # Элемент не кликабельный
        ErrorPattern(
            ["not clickable", "не кликабельный", "element is not clickable", "cannot click"],
            "Элемент не кликабельный. Попробуйте прокрутить страницу до элемента или использовать другой элемент.",
            "scroll"
        ),
        # Элемент скрыт
        ErrorPattern(
            ["element is hidden", "элемент скрыт", "hidden", "display: none", "visibility: hidden"],
            "Элемент скрыт на странице. Попробуйте прокрутить страницу или подождать его появления.",
            "wait"
        ),
        # Ошибка сети
        ErrorPattern(
            ["network error", "ошибка сети", "net::", "ERR_", "failed to fetch"],
            "Ошибка сети. Подождите и попробуйте перезагрузить страницу или повторить действие.",
            "wait"
        ),
        # Элемент удален из DOM
        ErrorPattern(
            ["detached", "удален из DOM", "element is not attached", "stale element"],
            "Элемент был удален из DOM. Страница изменилась. Обновите информацию о странице и попробуйте снова.",
            "wait"
        ),
        # Ошибка выполнения JavaScript
        ErrorPattern(
            ["javascript error", "js error", "execution context", "evaluation failed"],
            "Ошибка выполнения JavaScript. Попробуйте другой подход или подождите загрузки страницы.",
            "wait"
        ),
        # Элемент находится в iframe
        ErrorPattern(
            ["iframe", "frame", "cross-origin"],
            "Элемент находится в iframe. Используйте специальные методы для работы с iframe или другой элемент.",
            "alternative"
        ),
        # Ошибка авторизации
        ErrorPattern(
            ["unauthorized", "401", "403", "forbidden", "доступ запрещен"],
            "Ошибка авторизации. Проверьте права доступа или выполните вход на сайт.",
            "alternative"
        ),
        # Страница не отвечает
        ErrorPattern(
            ["no response", "нет ответа", "connection refused", "connection timeout"],
            "Страница не отвечает. Подождите и попробуйте перезагрузить страницу.",
            "wait"
        ),
    ]
    
    @staticmethod
    def match_error(error_message: str, action: str = None) -> Optional[ErrorPattern]:
        """
        Поиск паттерна для ошибки
        
        Args:
            error_message: Сообщение об ошибке
            action: Действие, которое вызвало ошибку (опционально)
            
        Returns:
            Найденный паттерн или None
        """
        for pattern in ErrorPatternMatcher.PATTERNS:
            if pattern.matches(error_message):
                return pattern
        
        return None
    
    @staticmethod
    def get_solution(error_message: str, action: str = None) -> Optional[Dict[str, Any]]:
        """
        Получение решения для ошибки
        
        Args:
            error_message: Сообщение об ошибке
            action: Действие, которое вызвало ошибку (опционально)
            
        Returns:
            Словарь с решением и стратегией или None
        """
        pattern = ErrorPatternMatcher.match_error(error_message, action)
        
        if pattern:
            return {
                "solution": pattern.solution,
                "strategy": pattern.strategy,
                "pattern_matched": True
            }
        
        return None

