"""Оптимизация токенов для управления размером контекста"""
import tiktoken
from typing import List, Dict, Any, Optional
from config import MAX_CONTEXT_TOKENS


class TokenOptimizer:
    """Оптимизатор токенов для управления размером контекста"""
    
    def __init__(self, model: str = "gpt-4"):
        """
        Инициализация оптимизатора
        
        Args:
            model: Модель OpenAI для подсчета токенов
        """
        try:
            self.encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            # Fallback на cl100k_base для GPT-4
            self.encoding = tiktoken.get_encoding("cl100k_base")
        self.max_tokens = MAX_CONTEXT_TOKENS
        # Кэш для подсчета токенов (для одинаковых строк)
        self._token_count_cache: Dict[str, int] = {}
    
    def count_tokens(self, text: str) -> int:
        """
        Подсчет токенов в тексте с кэшированием
        
        Args:
            text: Текст для подсчета токенов
            
        Returns:
            Количество токенов
        """
        # Кэшируем только для коротких строк (до 500 символов) для экономии памяти
        if len(text) <= 500:
            if text in self._token_count_cache:
                return self._token_count_cache[text]
            token_count = len(self.encoding.encode(text))
            self._token_count_cache[text] = token_count
            
            # Ограничиваем размер кэша (храним последние 1000 результатов)
            if len(self._token_count_cache) > 1000:
                # Удаляем самый старый элемент
                oldest_key = next(iter(self._token_count_cache))
                del self._token_count_cache[oldest_key]
            
            return token_count
        else:
            # Для длинных строк не кэшируем
            return len(self.encoding.encode(text))
    
    def optimize_page_info(self, page_info: Dict[str, Any], max_tokens: Optional[int] = None, task_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Оптимизация информации о странице для умещения в лимит токенов
        
        1. Резервируем место для URL, заголовка и истории (20% от max_tokens)
        2. Остальное место распределяем между элементами и текстом
        3. Элементы приоритизируются по релевантности и оптимизируются адаптивно
        4. Информация о местоположении и модальных окнах оптимизируется отдельно
        
        Args:
            page_info: Полная информация о странице
            max_tokens: Максимальное количество токенов (по умолчанию из конфига)
            task_type: Тип задачи (navigation, form, reading) для адаптивной оптимизации
            
        Returns:
            Оптимизированная информация о странице
        """
        max_tokens = max_tokens or self.max_tokens
        
        # Резервируем место для базовой информации (URL, заголовок, история)
        # Примерно 20% от лимита
        reserved_tokens = max(500, int(max_tokens * 0.2))
        available_tokens = max_tokens - reserved_tokens
        
        # Создаем оптимизированную версию
        optimized = {
            "url": page_info.get("url", ""),
            "title": page_info.get("title", ""),
            "interactive_elements": [],
            "visible_text_preview": ""
        }
        
        # Оптимизируем информацию о местоположении (если есть)
        location_context = page_info.get("location_context")
        if location_context:
            # Оптимизируем location_context - ограничиваем размер breadcrumbs и описаний
            optimized_location = {
                "description": location_context.get("description", "")[:200],  # Максимум 200 символов
                "visible_modals_count": location_context.get("visible_modals_count", 0),
                "has_forms": location_context.get("has_forms", False)
            }
            
            # Оптимизируем структуру
            structure = location_context.get("structure", {})
            if structure:
                optimized_structure = {}
                
                # Breadcrumbs - максимум 5 элементов
                breadcrumbs = structure.get("breadcrumbs", [])
                if breadcrumbs:
                    optimized_structure["breadcrumbs"] = [
                        {"text": b.get("text", "")[:50], "href": b.get("href", "")[:100]}
                        for b in breadcrumbs[:5]
                    ]
                
                # Текущая секция - ограничиваем текст
                current_section = structure.get("current_section")
                if current_section:
                    optimized_structure["current_section"] = {
                        "type": current_section.get("type", ""),
                        "text_preview": current_section.get("text_preview", "")[:150]
                    }
                
                # Видимые модальные окна - максимум 1, только критическая информация
                visible_modals = structure.get("visible_modals", [])
                if visible_modals:
                    # Берем только самое важное модальное окно (с формой или первое)
                    important_modal = None
                    for m in visible_modals:
                        if m.get("has_form", False):
                            important_modal = m
                            break
                    if not important_modal and visible_modals:
                        important_modal = visible_modals[0]
                    
                    if important_modal:
                        optimized_structure["visible_modals"] = [
                            {
                                "has_form": important_modal.get("has_form", False),
                                "input_count": important_modal.get("input_count", 0),
                                "selector": important_modal.get("selector", "")[:50] if important_modal.get("selector") else ""
                            }
                        ]
                
                optimized_location["structure"] = optimized_structure
            
            optimized["location_context"] = optimized_location
        
        # Оптимизируем информацию о видимых модальных окнах (если есть) - только критическая информация
        visible_modals = page_info.get("visible_modals")
        if visible_modals:
            modals_list = visible_modals.get("modals", [])
            # Берем только самое важное модальное окно (с формой или первое)
            important_modal = None
            for m in modals_list:
                if m.get("has_form", False):
                    important_modal = m
                    break
            if not important_modal and modals_list:
                important_modal = modals_list[0]
            
            if important_modal:
                optimized["visible_modals"] = {
                    "count": visible_modals.get("count", 0),
                    "modals": [
                        {
                            "has_form": important_modal.get("has_form", False),
                            "input_count": important_modal.get("input_count", 0),
                            "selector": important_modal.get("selector", "")[:50] if important_modal.get("selector") else ""
                        }
                    ]
                }
        
        # Оптимизируем интерактивные элементы - используем 70% доступного места
        elements = page_info.get("interactive_elements", [])
        elements_tokens = int(available_tokens * 0.7)
        optimized_elements = self._optimize_elements(elements, elements_tokens, task_type=task_type)
        optimized["interactive_elements"] = optimized_elements
        
        # Оптимизируем текст - используем оставшиеся 30%
        visible_text = page_info.get("visible_text_preview", "")
        used_tokens = self.count_tokens(str(optimized_elements))
        # Учитываем токены, использованные на location_context и visible_modals
        if location_context:
            used_tokens += self.count_tokens(str(optimized.get("location_context", {})))
        if visible_modals:
            used_tokens += self.count_tokens(str(optimized.get("visible_modals", {})))
        text_tokens = max(100, available_tokens - used_tokens)
        optimized["visible_text_preview"] = self._truncate_text(visible_text, text_tokens)
        
        return optimized
    
    def _optimize_elements(self, elements: List[Dict[str, Any]], max_tokens: int, task_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Умная оптимизация списка элементов с приоритизацией по релевантности и типу задачи
        
        Стратегия:
        1. Сортировка по релевантности (relevance_score) с учетом типа задачи
        2. Адаптивное сокращение текста элементов в зависимости от доступного места
        3. Сохранение важных элементов даже при нехватке места
        4. Постепенное сокращение менее важных полей
        
        Args:
            elements: Список элементов
            max_tokens: Максимальное количество токенов
            task_type: Тип задачи (navigation, form, reading) для адаптивной оптимизации
            
        Returns:
            Оптимизированный список элементов
        """
        if not elements:
            return []
        
        # КРИТИЧЕСКИ ВАЖНО: Сначала отделяем элементы в модальных окнах и формах
        modal_elements = [e for e in elements if e.get("in_modal")]
        form_elements = [e for e in elements if e.get("in_form") and not e.get("in_modal")]
        other_elements = [e for e in elements if not e.get("in_modal") and not e.get("in_form")]
        
        # Улучшенная сортировка с учетом relevance_score и типа задачи
        def sort_key(x):
            relevance_score = x.get("relevance_score", 0)
            # Для задач с формами - приоритет формам и кнопкам
            if task_type == "form":
                form_boost = 20 if x.get("in_form") or x.get("type") in ["button", "input", "select", "textarea"] else 0
                relevance_score += form_boost
            # Для задач навигации - приоритет ссылкам и кнопкам навигации
            elif task_type == "navigation":
                nav_boost = 15 if x.get("type") in ["link", "button"] or x.get("href") else 0
                relevance_score += nav_boost
            # Для задач чтения - приоритет текстовым элементам
            elif task_type == "reading":
                reading_boost = 10 if x.get("text") and len(x.get("text", "")) > 50 else 0
                relevance_score += reading_boost
            
            return (
                relevance_score,  # Сначала по релевантности (с учетом типа задачи)
                bool(x.get("in_modal")),  # Затем элементы в модальных окнах
                bool(x.get("in_form")),  # Затем элементы в формах
                bool(x.get("id")),  # Затем элементы с id
                bool(x.get("text")),  # Затем элементы с текстом
            )
        
        sorted_modal_elements = sorted(modal_elements, key=sort_key, reverse=True)
        sorted_form_elements = sorted(form_elements, key=sort_key, reverse=True)
        sorted_other_elements = sorted(other_elements, key=sort_key, reverse=True)
        
        # Объединяем: сначала модальные окна, затем формы, затем остальные
        sorted_elements = sorted_modal_elements + sorted_form_elements + sorted_other_elements
        
        optimized = []
        current_tokens = 0
        
        # Проходим элементы в порядке релевантности
        for element in sorted_elements:
            relevance_score = element.get("relevance_score", 0)
            
            # Определяем приоритет элемента
            # КРИТИЧЕСКИ ВАЖНО: Элементы в модальных окнах и формах всегда высокоприоритетные
            is_in_modal = element.get("in_modal", False)
            is_in_form = element.get("in_form", False)
            is_high_priority = (
                is_in_modal or  # Элементы в модальных окнах - максимальный приоритет
                is_in_form or   # Элементы в формах - высокий приоритет
                relevance_score > 10 or 
                element.get("id") or 
                element.get("type") in ["button", "link"]
            )
            
            # Адаптивно сокращаем текст в зависимости от доступного места и типа задачи
            remaining_tokens = max_tokens - current_tokens
            
            # Адаптивные лимиты в зависимости от типа задачи
            if task_type == "form":
                # Для форм - больше текста для полей ввода
                if is_in_modal and remaining_tokens > 200:
                    text_limit = 120  # Сокращено с 150
                elif is_in_modal:
                    text_limit = 80  # Сокращено с 100
                elif is_in_form and remaining_tokens > 200:
                    text_limit = 80  # Сокращено с 100
                elif is_high_priority and remaining_tokens > 200:
                    text_limit = 60
                elif is_high_priority and remaining_tokens > 100:
                    text_limit = 40
                else:
                    text_limit = 20
            elif task_type == "navigation":
                # Для навигации - меньше текста, больше селекторов
                if is_in_modal and remaining_tokens > 200:
                    text_limit = 80
                elif is_in_modal:
                    text_limit = 60
                elif is_high_priority and remaining_tokens > 200:
                    text_limit = 50
                else:
                    text_limit = 30
            else:
                # Стандартные лимиты (сокращенные)
                if is_in_modal and remaining_tokens > 200:
                    text_limit = 100  # Сокращено с 150
                elif is_in_modal:
                    text_limit = 70  # Сокращено с 100
                elif is_in_form and remaining_tokens > 200:
                    text_limit = 70  # Сокращено с 100
                elif is_high_priority and remaining_tokens > 200:
                    text_limit = 60  # Сокращено с 100
                elif is_high_priority and remaining_tokens > 100:
                    text_limit = 40  # Сокращено с 50
                elif remaining_tokens > 50:
                    text_limit = 25  # Сокращено с 30
                else:
                    text_limit = 15
            
            # Создаем компактное представление элемента
            compact_element = {
                "type": element.get("type"),
                "selector": element.get("selector"),
            }
            
            # КРИТИЧЕСКИ ВАЖНО: Сохраняем информацию о модальных окнах и формах
            if is_in_modal:
                compact_element["in_modal"] = True
            if is_in_form:
                compact_element["in_form"] = True
            
            # Добавляем текст с учетом лимита
            text = element.get("text", "")
            if text:
                compact_element["text"] = text[:text_limit]
            
            # Сохраняем relevance_score если есть
            if element.get("relevance_score") is not None:
                compact_element["relevance_score"] = element.get("relevance_score")
            
            # Добавляем важные поля в зависимости от доступного места
            # Приоритет: href > id > input_type > placeholder
            if remaining_tokens > 100:
                if element.get("href"):
                    compact_element["href"] = element.get("href")
                if element.get("id"):
                    compact_element["id"] = element.get("id")
                if element.get("input_type"):
                    compact_element["input_type"] = element.get("input_type")
                if element.get("placeholder"):
                    compact_element["placeholder"] = element.get("placeholder")
            elif remaining_tokens > 50:
                # Только самые важные поля
                if element.get("href"):
                    compact_element["href"] = element.get("href")
                if element.get("id"):
                    compact_element["id"] = element.get("id")
            else:
                # Только критически важные поля
                if element.get("id"):
                    compact_element["id"] = element.get("id")
            
            element_str = str(compact_element)
            element_tokens = self.count_tokens(element_str)
            
            # Если элемент помещается - добавляем
            if current_tokens + element_tokens <= max_tokens:
                optimized.append(compact_element)
                current_tokens += element_tokens
            elif is_high_priority:
                # Для высокоприоритетных элементов пробуем еще больше сократить
                # Убираем менее важные поля
                if "href" in compact_element and remaining_tokens < 50:
                    del compact_element["href"]
                if "placeholder" in compact_element:
                    del compact_element["placeholder"]
                if "input_type" in compact_element and remaining_tokens < 30:
                    del compact_element["input_type"]
                
                # Сокращаем текст до минимума
                if "text" in compact_element:
                    compact_element["text"] = compact_element["text"][:20]
                
                element_str = str(compact_element)
                element_tokens = self.count_tokens(element_str)
                
                if current_tokens + element_tokens <= max_tokens:
                    optimized.append(compact_element)
                    current_tokens += element_tokens
                else:
                    # Если даже после сокращения не помещается
                    # КРИТИЧЕСКИ ВАЖНО: Элементы в модальных окнах ВСЕГДА добавляем, даже если превышаем лимит
                    if is_in_modal:
                        # Элементы в модальных окнах критически важны - добавляем всегда
                        optimized.append(compact_element)
                        current_tokens += element_tokens
                    elif is_in_form and relevance_score >= 5:
                        # Элементы в формах также важны
                        optimized.append(compact_element)
                        current_tokens += element_tokens
                    elif relevance_score >= 5:
                        # Для других критически важных элементов добавляем даже если превышаем лимит
                        optimized.append(compact_element)
                        current_tokens += element_tokens
                    else:
                        # Пропускаем только низкоприоритетные элементы
                        continue
            else:
                # Для низкоприоритетных элементов просто пропускаем
                continue
        
        return optimized
    
    def _truncate_text(self, text: str, max_tokens: int) -> str:
        """Обрезка текста до указанного количества токенов"""
        if not text:
            return ""
        
        tokens = self.encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        
        # Обрезаем до нужного количества токенов
        truncated_tokens = tokens[:max_tokens]
        return self.encoding.decode(truncated_tokens)
    
    def format_context(self, page_info: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None) -> str:
        """
        Форматирование контекста для отправки в AI
        
        Args:
            page_info: Информация о текущей странице
            history: История действий (опционально)
            
        Returns:
            Отформатированный контекст
        """
        parts = []
        
        # Информация о текущей странице
        parts.append(f"Текущая страница: {page_info.get('url', '')}")
        parts.append(f"Заголовок: {page_info.get('title', '')}")
        
        # Информация о местоположении (если доступна)
        location_context = page_info.get("location_context")
        if location_context:
            location_desc = location_context.get("description", "")
            if location_desc:
                parts.append(f"\n📍 Местоположение: {location_desc}")
            
            structure = location_context.get("structure", {})
            breadcrumbs = structure.get("breadcrumbs", [])
            if breadcrumbs:
                breadcrumbs_text = " > ".join([b.get("text", "") for b in breadcrumbs])
                parts.append(f"   Навигация: {breadcrumbs_text}")
        
        
        # Интерактивные элементы
        elements = page_info.get("interactive_elements", [])
        if elements:
            # Разделяем элементы на группы для лучшего отображения
            form_elements = [e for e in elements if e.get('in_form')]
            other_elements = [e for e in elements if not e.get('in_form')]
            
            parts.append(f"\nДоступные элементы на странице (всего: {len(elements)}):")
            
            # Сначала показываем элементы в формах (включая элементы в модальных окнах с формами)
            if form_elements:
                parts.append(f"\n📝 ЭЛЕМЕНТЫ В ФОРМЕ:")
                for i, elem in enumerate(form_elements, 1):
                    elem_desc = f"  {i}. {elem.get('type', 'unknown')}"
                    if elem.get('text'):
                        elem_desc += f" - '{elem.get('text')}'"
                    if elem.get('selector'):
                        elem_desc += f" ({elem.get('selector')})"
                    if elem.get('placeholder'):
                        elem_desc += f" [placeholder: '{elem.get('placeholder')}']"
                    if elem.get('relevance_score') is not None:
                        elem_desc += f" [релевантность: {elem.get('relevance_score'):.1f}]"
                    parts.append(elem_desc)
            
            # Остальные элементы
            if other_elements:
                parts.append(f"\nОстальные элементы:")
                for i, elem in enumerate(other_elements, 1):
                    elem_desc = f"  {i}. {elem.get('type', 'unknown')}"
                    if elem.get('text'):
                        elem_desc += f" - '{elem.get('text')}'"
                    if elem.get('selector'):
                        elem_desc += f" ({elem.get('selector')})"
                    if elem.get('href'):
                        href = elem.get('href')
                        if len(href) > 60:
                            href = href[:57] + "..."
                        elem_desc += f" -> {href}"
                    if elem.get('relevance_score') is not None:
                        elem_desc += f" [релевантность: {elem.get('relevance_score'):.1f}]"
                    parts.append(elem_desc)
        
        # Видимый текст (если есть место)
        visible_text = page_info.get("visible_text_preview", "")
        if visible_text:
            parts.append(f"\nВидимый текст на странице (фрагмент):\n{visible_text[:300]}")
        
        # Информация о прогрессе задачи
        completed_steps = page_info.get("completed_steps", [])
        if completed_steps:
            parts.append(f"\n✓ Выполненные шаги задачи:")
            for i, step in enumerate(completed_steps, 1):
                parts.append(f"  {i}. {step}")
        
        # Информация об извлеченной информации
        extracted_info = page_info.get("extracted_info", {})
        if extracted_info:
            parts.append(f"\n📄 Извлеченная информация (уже прочитано, НЕ извлекай повторно):")
            for desc, text in extracted_info.items():
                text_preview = text[:200] + ("..." if len(text) > 200 else "")
                parts.append(f"  - {desc}: {text_preview}")

        requirements_status = page_info.get("requirements_status")
        if requirements_status:
            parts.append(f"\n📌 Прогресс по требованиям задачи:\n{requirements_status}")
        
        # История действий (улучшенное форматирование)
        if history:
            parts.append("\nИстория последних действий:")
            for i, action in enumerate(history[-5:], 1):  # Последние 5 действий
                action_name = action.get('action', 'unknown')
                result = action.get('result', {})
                success = result.get('success', False) if isinstance(result, dict) else False
                success_marker = "✓" if success else "✗"
                parts.append(f"  {i}. {success_marker} {action_name}")
                if isinstance(result, dict) and result.get('message'):
                    parts.append(f"     → {result.get('message')}")
        
        return "\n".join(parts)

