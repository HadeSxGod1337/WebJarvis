"""Извлечение релевантной информации из контекста"""
from typing import Dict, Any, List
import re


class ContextExtractor:
    """Извлечение и фильтрация релевантной информации"""
    
    # Стоп-слова для фильтрации (короткие и неинформативные слова)
    STOP_WORDS = {"и", "в", "на", "с", "по", "для", "от", "до", "из", "к", "о", "об", "со", "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with"}
    
    # Важные слова для разных типов задач
    TASK_KEYWORDS = {
        "navigation": ["найти", "найди", "открыть", "открой", "перейти", "перейди", "ссылка", "link", "find", "open", "go"],
        "form": ["форма", "заполнить", "заполни", "ввести", "введи", "поле", "field", "form", "fill", "input"],
        "extraction": ["прочитать", "прочитай", "извлечь", "извлеки", "найти информацию", "read", "extract", "get"],
        "action": ["кликнуть", "кликни", "нажать", "нажми", "click", "press", "button"]
    }
    
    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """
        Извлечение значимых ключевых слов из текста
        
        Args:
            text: Текст для анализа
            
        Returns:
            Список значимых ключевых слов
        """
        # Убираем знаки препинания и приводим к нижнему регистру
        text_lower = text.lower()
        # Разбиваем на слова
        words = re.findall(r'\b\w+\b', text_lower)
        # Фильтруем стоп-слова и короткие слова
        keywords = [w for w in words if len(w) > 2 and w not in ContextExtractor.STOP_WORDS]
        return keywords
    
    @staticmethod
    def _calculate_relevance_score(element: Dict[str, Any], task_keywords: List[str], task_type: str = None) -> int:
        """
        Вычисление релевантности элемента к задаче
        
        Args:
            element: Элемент страницы
            task_keywords: Ключевые слова из задачи
            task_type: Тип задачи (опционально)
            
        Returns:
            Оценка релевантности
        """
        score = 0
        element_text = element.get("text", "").lower()
        element_type = element.get("type", "").lower()
        element_href = element.get("href", "").lower()
        element_placeholder = element.get("placeholder", "").lower()
        
        # Базовый подсчет совпадений ключевых слов
        for keyword in task_keywords:
            # Точное совпадение в тексте элемента
            if keyword in element_text:
                score += 3
            # Совпадение в href (для ссылок)
            if keyword in element_href:
                score += 2
            # Совпадение в placeholder (для полей ввода)
            if keyword in element_placeholder:
                score += 2
            # Частичное совпадение (слово содержит ключевое слово или наоборот)
            if len(keyword) > 3:
                if keyword in element_text or element_text in keyword:
                    score += 1
        
        # Бонусы за тип элемента в зависимости от типа задачи
        if task_type:
            task_keywords_list = ContextExtractor.TASK_KEYWORDS.get(task_type, [])
            for task_kw in task_keywords_list:
                if task_kw in element_text:
                    score += 2
        
        # Бонус за элементы с id (обычно более важные)
        if element.get("id"):
            score += 1
        
        # Бонус за элементы в основном контенте (если есть информация)
        if element.get("in_main_content"):
            score += 2
        
        # Штраф за элементы в footer/header (обычно менее релевантны)
        if element.get("in_footer_header"):
            score -= 1
        
        # Бонус за видимые элементы
        if element.get("visible", True):
            score += 1
        
        return score
    
    @staticmethod
    def _detect_task_type(task_description: str) -> str:
        """
        Определение типа задачи
        
        Args:
            task_description: Описание задачи
            
        Returns:
            Тип задачи: 'navigation', 'form', 'extraction', 'action', или None
        """
        task_lower = task_description.lower()
        
        for task_type, keywords in ContextExtractor.TASK_KEYWORDS.items():
            if any(kw in task_lower for kw in keywords):
                return task_type
        
        return None
    
    @staticmethod
    def extract_relevant_elements(page_info: Dict[str, Any], task_description: str) -> List[Dict[str, Any]]:
        """
        Извлечение релевантных элементов на основе описания задачи
        
        Улучшенный алгоритм:
        - Извлекает значимые ключевые слова из задачи
        - Учитывает тип задачи
        - Приоритизирует элементы по релевантности
        - Сохраняет важные элементы даже при обрезке
        
        Args:
            page_info: Информация о странице
            task_description: Описание текущей задачи
            
        Returns:
            Список релевантных элементов, отсортированных по релевантности
        """
        elements = page_info.get("interactive_elements", [])
        if not elements:
            return []
        
        # Извлекаем ключевые слова из задачи
        task_keywords = ContextExtractor._extract_keywords(task_description)
        
        # Определяем тип задачи
        task_type = ContextExtractor._detect_task_type(task_description)
        
        # Вычисляем релевантность для каждого элемента
        scored_elements = []
        for element in elements:
            score = ContextExtractor._calculate_relevance_score(element, task_keywords, task_type)
            
            if score > 0:
                element_copy = element.copy()
                element_copy["relevance_score"] = score
                scored_elements.append(element_copy)
        
        # Сортировка по релевантности
        scored_elements.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        
        # Возвращаем ВСЕ элементы, отсортированные по релевантности
        # Агент должен видеть все доступные элементы для самостоятельного выбора по ТЗ
        # НЕ ограничиваем количество - агент сам решит, что использовать
        result = scored_elements  # ВСЕ элементы, не только топ-30!
        
        # Также добавляем ВСЕ элементы с id или важными атрибутами, которые не попали в scored_elements
        important_elements = []
        scored_selectors = {e.get("selector") for e in scored_elements}
        
        for element in elements:
            # Если элемент еще не в результате
            if element.get("selector") not in scored_selectors:
                # Элементы с id обычно важны
                if element.get("id"):
                    important_elements.append(element)
                # Элементы с data-атрибутами (для тестирования)
                elif any(key.startswith("data_") for key in element.keys()):
                    important_elements.append(element)
        
        # Добавляем ВСЕ важные элементы в результат
        for elem in important_elements:
            if elem.get("selector") not in scored_selectors:
                elem_copy = elem.copy()
                elem_copy["relevance_score"] = 1  # Минимальный score
                result.append(elem_copy)
        
        return result

