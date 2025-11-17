"""Управление контекстом для AI-агента"""
from typing import List, Dict, Any, Optional
from collections import Counter
import re
from .token_optimizer import TokenOptimizer
from .extractor import ContextExtractor
from config import MAX_CONTEXT_TOKENS, MAX_HISTORY_TOKENS, MAX_REQUEST_TOKENS


class ContextManager:
    """Управление контекстом взаимодействия с AI"""
    
    def __init__(self, model: str = "gpt-4"):
        """
        Инициализация менеджера контекста
        
        Args:
            model: Модель OpenAI для подсчета токенов
        """
        self.token_optimizer = TokenOptimizer(model)
        self.extractor = ContextExtractor()
        self.history: List[Dict[str, Any]] = []
        self.current_task: Optional[str] = None
        # Отслеживание извлеченной информации
        self.extracted_info: Dict[str, str] = {}  # Ключ: описание элемента, Значение: извлеченный текст
        # Отслеживание прогресса задачи
        self.completed_steps: List[str] = []  # Список выполненных шагов
        self.progress_counters: Counter = Counter()
        self.requirements: Dict[str, int] = {}
        self.require_resume_review: bool = False
        self.resume_reviewed: bool = False
        self.pending_resume_wizard: bool = False
    
    def set_task(self, task: str):
        """Установка текущей задачи"""
        self.current_task = task
        # Сбрасываем прогресс при новой задаче
        self.completed_steps = []
        self.extracted_info = {}
        self.progress_counters = Counter()
        self.resume_reviewed = False
        self.requirements = self._parse_task_requirements(task)
        self.require_resume_review = self._needs_resume_review(task)
        self.pending_resume_wizard = False
    
    def add_extracted_info(self, description: str, text: str):
        """
        Добавление извлеченной информации
        
        Args:
            description: Описание элемента, из которого извлечена информация
            text: Извлеченный текст
        """
        self.extracted_info[description] = text
    
    def add_completed_step(self, step: str):
        """
        Добавление выполненного шага задачи
        
        Args:
            step: Описание выполненного шага
        """
        if step not in self.completed_steps:
            self.completed_steps.append(step)
    
    def add_to_history(self, action: str, result: Dict[str, Any]):
        """
        Добавление действия в историю
        
        Args:
            action: Описание действия
            result: Результат выполнения действия
        """
        self.history.append({
            "action": action,
            "result": result,
            "success": result.get("success", False)
        })
        
        # Если это извлечение текста - сохраняем информацию
        if "extract_text" in action.lower() and result.get("success"):
            text = result.get("text", "")
            if text:
                # Пытаемся определить описание из параметров действия
                params_str = action.split("(")[1].split(")")[0] if "(" in action else ""
                description = params_str.replace("description=", "").replace("'", "").replace('"', "") or "текст"
                self.add_extracted_info(description, text)
        
        # Ограничиваем размер истории по токенам
        self._trim_history()
    
    def _trim_history(self):
        """Умная обрезка истории до допустимого размера с сохранением важных действий"""
        history_text = str(self.history)
        history_tokens = self.token_optimizer.count_tokens(history_text)
        
        if history_tokens <= MAX_HISTORY_TOKENS:
            return
        
        # Определяем важность действий
        important_actions = []
        less_important_actions = []
        
        for action_record in self.history:
            action = action_record.get("action", "")
            result = action_record.get("result", {})
            success = result.get("success", False)
            
            # Важные действия: навигация, успешные клики, завершение задачи
            is_important = (
                action == "navigate" or
                (action == "click_element" and success) or
                action == "task_complete" or
                (action == "extract_text" and success)
            )
            
            # Менее важные: прокрутки, ожидания, неудачные действия
            is_less_important = (
                action == "scroll" or
                action == "wait_for_element" or
                not success
            )
            
            if is_important:
                important_actions.append(action_record)
            elif is_less_important:
                less_important_actions.append(action_record)
            else:
                # Средняя важность - добавляем в важные
                important_actions.append(action_record)
        
        # Сохраняем все важные действия
        # И последние N менее важных действий
        keep_less_important = min(len(less_important_actions), 5)  # Последние 5 менее важных
        
        # Формируем новую историю
        new_history = important_actions + less_important_actions[-keep_less_important:]
        
        # Проверяем размер новой истории
        new_history_text = str(new_history)
        new_history_tokens = self.token_optimizer.count_tokens(new_history_text)
        
        if new_history_tokens <= MAX_HISTORY_TOKENS:
            self.history = new_history
        else:
            # Если все еще слишком много - сжимаем важные действия
            # Оставляем только последние важные действия
            keep_important = len(important_actions)
            while new_history_tokens > MAX_HISTORY_TOKENS and keep_important > 0:
                keep_important -= 1
                compressed_history = important_actions[-keep_important:] + less_important_actions[-keep_less_important:]
                compressed_text = str(compressed_history)
                new_history_tokens = self.token_optimizer.count_tokens(compressed_text)
            
            if keep_important > 0:
                self.history = important_actions[-keep_important:] + less_important_actions[-keep_less_important:]
            else:
                # В крайнем случае оставляем только последние действия
                keep_count = len(self.history) // 2
                self.history = self.history[-keep_count:]
    
    def prepare_context(self, page_info: Dict[str, Any], max_tokens: Optional[int] = None) -> str:
        """
        Подготовка контекста для отправки в AI с учетом ограничений по токенам
        
        Args:
            page_info: Информация о текущей странице
            max_tokens: Максимальное количество токенов для контекста (по умолчанию из конфига)
            page_extractor: Экстрактор страницы для получения информации о местоположении (опционально)
            
        Returns:
            Отформатированный контекст
        """
        max_tokens = max_tokens or MAX_CONTEXT_TOKENS
        
        # Определяем тип задачи для адаптивной оптимизации
        task_type = None
        if self.current_task:
            task_lower = self.current_task.lower()
            if any(kw in task_lower for kw in ["заполни", "введи", "форма", "fill", "form", "input"]):
                task_type = "form"
            elif any(kw in task_lower for kw in ["найди", "перейди", "открой", "navigate", "go to", "find"]):
                task_type = "navigation"
            elif any(kw in task_lower for kw in ["прочитай", "извлеки", "read", "extract"]):
                task_type = "reading"
        
        # Оптимизируем информацию о странице с учетом типа задачи
        optimized_page_info = self.token_optimizer.optimize_page_info(page_info, max_tokens=max_tokens, task_type=task_type)
        
        # Если есть задача, извлекаем релевантные элементы
        if self.current_task:
            relevant_elements = self.extractor.extract_relevant_elements(
                optimized_page_info, 
                self.current_task
            )
            if relevant_elements:
                optimized_page_info["interactive_elements"] = relevant_elements
        
        # Добавляем информацию о местоположении (если доступна в page_info)
        location_context = page_info.get("location_context")
        if location_context:
            optimized_page_info["location_context"] = location_context
        
        # Добавляем информацию о видимых модальных окнах из page_info
        page_state = page_info.get("metadata", {}).get("page_state", {})
        if page_state:
            visible_modals = page_state.get("visible_modal_count", 0)
            if visible_modals > 0:
                modals_info = page_state.get("modals", [])
                optimized_page_info["visible_modals"] = {
                    "count": visible_modals,
                    "modals": modals_info[:2]  # Максимум 2 модальных окна для экономии токенов
                }
                
                # Определяем активное модальное окно (самое верхнее или с формой) - только критическая информация
                active_modal = None
                if modals_info:
                    # Сортируем по z-index и наличию формы
                    sorted_modals = sorted(
                        modals_info,
                        key=lambda m: (m.get("has_form", False), m.get("z_index", 0)),
                        reverse=True
                    )
                    active_modal = sorted_modals[0]
                    optimized_page_info["active_modal_context"] = {
                        "has_active_modal": True,
                        "has_form": active_modal.get("has_form", False),
                        "input_count": active_modal.get("input_count", 0),
                        "selector": active_modal.get("selector", "")[:50] if active_modal.get("selector") else ""
                    }
                else:
                    optimized_page_info["active_modal_context"] = {
                        "has_active_modal": False
                    }
            else:
                optimized_page_info["active_modal_context"] = {
                    "has_active_modal": False
                }
        else:
            optimized_page_info["active_modal_context"] = {
                "has_active_modal": False
            }
        
        # Добавляем информацию о прогрессе и извлеченной информации в page_info
        if self.completed_steps:
            optimized_page_info["completed_steps"] = self.completed_steps
        if self.extracted_info:
            # Ограничиваем размер извлеченной информации для экономии токенов
            extracted_summary = {}
            for desc, text in self.extracted_info.items():
                # Сохраняем только первые 500 символов каждого извлеченного текста
                extracted_summary[desc] = text[:500] + ("..." if len(text) > 500 else "")
            optimized_page_info["extracted_info"] = extracted_summary

        requirements_status = self.get_requirements_status()
        if requirements_status:
            optimized_page_info["requirements_status"] = requirements_status
        
        # Форматируем контекст
        context = self.token_optimizer.format_context(
            optimized_page_info,
            self.history
        )
        
        # Проверяем размер контекста и оптимизируем если нужно
        context_tokens = self.token_optimizer.count_tokens(context)
        if context_tokens > max_tokens:
            # Умная оптимизация: используем адаптивное сокращение
            # Вычисляем коэффициент сокращения
            reduction_factor = max_tokens / context_tokens
            
            # Инициализируем переменные для важных элементов до условного блока
            elements = optimized_page_info["interactive_elements"]
            important_elements = [e for e in elements if e.get("relevance_score", 0) > 5]
            less_important_elements = [e for e in elements if e.get("relevance_score", 0) <= 5]
            
            # Если превышение небольшое (< 20%) - просто сокращаем текст элементов
            if reduction_factor > 0.8:
                # Легкое сокращение: уменьшаем текст элементов
                for elem in elements:
                    if elem.get("text") and len(elem.get("text", "")) > 30:
                        elem["text"] = elem["text"][:30]
                optimized_page_info["interactive_elements"] = elements
                optimized_page_info["visible_text_preview"] = optimized_page_info.get("visible_text_preview", "")[:500]
            else:
                # Сильное превышение - более агрессивная оптимизация
                # Сохраняем важные элементы, сокращаем менее важные
                # Оставляем все важные элементы и часть менее важных (пропорционально)
                keep_count = max(10, int(len(less_important_elements) * reduction_factor))
                optimized_page_info["interactive_elements"] = important_elements + less_important_elements[:keep_count]
                optimized_page_info["visible_text_preview"] = optimized_page_info.get("visible_text_preview", "")[:200]
            
            # Переформатируем контекст с оптимизированными данными
            context = self.token_optimizer.format_context(
                optimized_page_info,
                self.history
            )
            
            # Умная обрезка истории - сохраняем важные действия
            important_history = []
            for action_record in self.history:
                action = action_record.get("action", "")
                result = action_record.get("result", {})
                if action == "navigate" or (action == "click_element" and result.get("success")) or action == "task_complete":
                    important_history.append(action_record)
            
            # Добавляем последние действия для контекста
            recent_history = self.history[-3:] if len(self.history) > 3 else self.history
            # Объединяем важные и последние действия, убирая дубликаты
            combined_history = important_history + [h for h in recent_history if h not in important_history]
            
            context = self.token_optimizer.format_context(
                optimized_page_info,
                combined_history[-5:]  # Последние 5 действий (важные + последние)
            )
            
            # Если все еще превышает - еще больше сокращаем
            context_tokens = self.token_optimizer.count_tokens(context)
            if context_tokens > max_tokens:
                # Агрессивная оптимизация: только важные элементы и последние 2 действия
                # Используем уже определенные important_elements или берем из optimized_page_info
                if not important_elements:
                    # Если important_elements пуст, берем элементы с высоким приоритетом из текущего списка
                    current_elements = optimized_page_info["interactive_elements"]
                    important_elements = [e for e in current_elements if e.get("relevance_score", 0) > 5] or current_elements[:15]
                optimized_page_info["interactive_elements"] = important_elements[:15]
                optimized_page_info["visible_text_preview"] = ""
                context = self.token_optimizer.format_context(
                    optimized_page_info,
                    combined_history[-2:] if len(combined_history) > 2 else combined_history  # Последние 2 действия
                )
        
        return context
    
    def estimate_request_size(self, system_prompt: str, user_message: str, tools: Optional[List[Dict[str, Any]]] = None) -> int:
        """
        Оценка общего размера запроса (system + user + tools)
        
        Args:
            system_prompt: Системный промпт
            user_message: Пользовательское сообщение
            tools: Определения инструментов (опционально)
            
        Returns:
            Общее количество токенов
        """
        system_tokens = self.token_optimizer.count_tokens(system_prompt)
        user_tokens = self.token_optimizer.count_tokens(user_message)
        tools_tokens = 0
        
        if tools:
            # Подсчитываем токены в tools definitions
            import json
            tools_str = json.dumps(tools, ensure_ascii=False)
            tools_tokens = self.token_optimizer.count_tokens(tools_str)
        
        return system_tokens + user_tokens + tools_tokens
    
    def get_system_prompt(self) -> str:
        """Получение системного промпта для AI-агента (оптимизирован согласно best practices Anthropic)"""
        return """<role>
Ты эксперт по автоматизации браузера с глубоким пониманием веб-технологий, динамического контента (SPA, AJAX, lazy loading) и паттернов взаимодействия пользователя с веб-интерфейсами. Твоя задача - автономно выполнять сложные многошаговые задачи БЕЗ помощи пользователя.
</role>

<principles>
1. Автономность: действуй самостоятельно, принимай решения на основе контекста
2. Адаптивность: учись на ошибках, адаптируй стратегию при неудачах
3. Проактивность: планируй заранее, предвиди возможные проблемы
4. Отслеживание прогресса: знай что сделано, что осталось, что извлечено
</principles>

<function_usage>
Используй функции для действий, не возвращай текст.
- Для завершения: task_complete({"summary": "..."})
- При неуверенности: scroll, query_dom для исследования

КРИТИЧЕСКИ ВАЖНО: ПОЛУЧЕНИЕ ИНФОРМАЦИИ О СТРАНИЦЕ:
query_dom - это ЕДИНСТВЕННЫЙ способ получить информацию о структуре страницы, элементах и их селекторах. ВСЕГДА используй query_dom когда нужна информация о странице.

Когда использовать query_dom:
- Перед кликом по элементу - если нет селектора или информации о нем
- Перед вводом текста - если нужно найти поле или узнать его селектор
- Когда нужно понять структуру страницы - что на ней находится, какие элементы доступны
- Когда нужно найти элементы - вместо прямого поиска используй query_dom
- После действий - для проверки результата и понимания что изменилось
- Когда нужно понять визуальное состояние элементов - что отображается, какие иконки, счетчики

Почему это важно: DOM Sub-agent анализирует структуру страницы и дает детальные ответы с описанием элементов, их визуального состояния и селекторами. Это позволяет тебе лучше понимать контекст и планировать действия.

Принцип работы с результатами query_dom:
- Если получил селектор - используй его напрямую в следующем действии (это экономит время и повышает точность)
- Если получил информацию о структуре - используй её для планирования
- Если получил описание визуального состояния - используй его для понимания функциональности элемента
- Не делай лишних действий если уже получил нужную информацию

Принцип формирования вопросов:
Не используй заготовленные шаблоны! Каждая ситуация уникальна. Подумай самостоятельно: что тебе нужно узнать для выполнения задачи? Сформулируй вопрос так, чтобы получить нужную информацию.

Процесс мышления при формировании вопроса:
1. Что мне нужно сделать? (понять цель действия)
2. Что мне нужно узнать для этого? (определить необходимую информацию)
3. Какой вопрос поможет получить эту информацию? (сформулировать вопрос самостоятельно)

Пример мышления:
"Мне нужно кликнуть по кнопке. Что мне нужно узнать? Мне нужно узнать: есть ли эта кнопка на странице, какой у неё селектор, что на ней отображается. Сформулирую вопрос на основе того, что мне нужно узнать: [самостоятельно формулирую вопрос, учитывая контекст задачи]."

Важно: Каждая ситуация уникальна. Подумай что именно тебе нужно узнать для выполнения задачи, и задай вопрос который поможет получить эту информацию. Не используй заготовленные шаблоны - адаптируйся к ситуации.
</function_usage>

<decision_process>
Перед каждым действием думай пошагово (Chain-of-Thought):

<thinking>
1. ПОНИМАНИЕ ЗАДАЧИ И ПЛАНИРОВАНИЕ (КРИТИЧЕСКИ ВАЖНО!):
   
   ШАГ 1: Пойми что от тебя хочет пользователь
   - Прочитай задачу внимательно
   - Пойми конечную цель: что должно быть достигнуто?
   - Подумай: что означает эта задача в контексте веб-страницы?
   - Не используй заготовки - каждая задача уникальна и требует индивидуального понимания
   
   ШАГ 2: Разбей задачу на логические шаги самостоятельно
   - Проанализируй задачу: какие действия нужно выполнить?
   - Определи последовательность шагов логически
   - Если задача содержит "и", "затем", "после" - это подсказки о последовательности
   - Но не следуй шаблонам - думай самостоятельно о том, какие шаги нужны для этой конкретной задачи
   
   ШАГ 3: Составь план действий
   - Для каждого шага определи: что нужно сделать?
   - Определи текущий шаг: какой шаг ты выполняешь СЕЙЧАС?
   - Что уже сделано? (проверь completed_steps, extracted_info, историю действий)
   - Что осталось сделать?
   - Какая конечная цель?
   
   ШАГ 4: Выполни план
   - Для каждого действия: определи что нужно узнать о странице
   - Сформулируй вопрос к DOM Sub-agent самостоятельно на основе того, что тебе нужно узнать
   - Не используй заготовленные шаблоны вопросов - каждая ситуация требует своего вопроса
   - Определяй элементы через query_dom, не используй хардкод селекторов

2. Анализ истории:
   - Последние 3-5 действий: что делал?
   - page_changed после каждого действия?
   - Есть ли повторения? (если 2+ одинаковых БЕЗ page_changed - это цикл!)
   - Были ли ошибки? Как их обработал?
   - Есть ли ИЗВЛЕЧЕННЫЕ СЕЛЕКТОРЫ из предыдущих query_dom? ОБЯЗАТЕЛЬНО используй их!

3. Анализ страницы:
   - URL и заголовок (изменились ли?)
   - Есть ли модальные окна? (работай с ними ПЕРВЫМИ)
   - Есть ли формы? (приоритет формам)
   - Какие интерактивные элементы доступны?
   - Появились ли новые элементы после последнего действия?

4. Планирование действия:
   - Нужна ли информация о странице для действия? Если да - ВСЕГДА задай вопрос query_dom перед действием
   - Нужен ли селектор для действия? Если да и его нет - подумай что тебе нужно узнать, и сформулируй вопрос query_dom самостоятельно
   - Есть ли извлеченный селектор из предыдущего query_dom? Используй его - это эффективнее
   - Какое действие приблизит к цели текущего шага задачи?
   - Отличается ли от последних действий? (избегай повторений без результата)
   - Что может пойти не так? Как это предотвратить?
   - После действия: подумай, нужно ли проверить результат? Если действие критическое - подумай что нужно проверить, и сформулируй вопрос query_dom самостоятельно

5. Проверка:
   - Не повторяю ли последнее действие?
   - Страница изменилась после последнего действия?
   - Есть ли альтернативные подходы?
   - Использую ли я извлеченные селекторы из query_dom?
</thinking>
</decision_process>

<principles_of_work>
ПРИНЦИПЫ РАБОТЫ С СЕЛЕКТОРАМИ И ПЛАНИРОВАНИЕМ:

Понимай разницу между информацией и действием:
- query_dom - получает информацию (селекторы, структуру страницы), не изменяет страницу
- click_element, type_text - выполняют действия, изменяют состояние страницы
- take_screenshot - делает снимок и анализирует через Vision API

Принцип эффективности:
Если у тебя уже есть селектор из предыдущего query_dom - используй его. Зачем спрашивать снова, если уже знаешь ответ?

Принцип проверки результата:
После критических действий (navigate, click_element, type_text) подумай: изменилась ли страница как ожидалось? Если действие должно было привести к изменению, но page_changed=false - это сигнал что-то пошло не так. Разберись почему.

Принцип работы с vision_analysis:
Vision API анализирует скриншот и описывает элементы текстом (например, "Кнопка: Найти"). Этот текст можно использовать для поиска элемента в списке интерактивных элементов. Если элемент найден - используй его селектор напрямую. query_dom нужен только если элемент не найден в списке.

Принцип адаптации:
Если действие не привело к ожидаемому результату (page_changed=false) - не повторяй то же самое. Подумай: почему не сработало? Может элемент перекрыт? Может нужна прокрутка? Может другой подход? Анализируй ситуацию и адаптируйся.

Примеры мышления:

После navigate():
"Я перешел на новую страницу. Мне нужно понять её структуру. take_screenshot поможет увидеть что на странице. Vision API опишет элементы. Если найду нужный элемент в списке интерактивных элементов по тексту из vision_analysis - использую его селектор. Если не найду - query_dom поможет получить селектор."

После click_element():
"Я кликнул по элементу. Что должно было произойти? Если должна была открыться форма - проверю появилась ли она. take_screenshot покажет что изменилось. Если форма появилась и я вижу её элементы в списке - использую их селекторы. Если не вижу - query_dom поможет найти селекторы полей."

Если page_changed=false:
"Действие не привело к изменению страницы. Почему? Может элемент не кликабелен? Может он перекрыт? Может нужна прокрутка? Проанализирую ситуацию через take_screenshot и vision_analysis. Попробую другой подход - не буду повторять то же действие."

Работа с информацией о странице:
"Мне нужна информация о странице (селектор элемента, структура, визуальное состояние). Подумаю: что именно мне нужно узнать для выполнения задачи? Сформулирую вопрос query_dom самостоятельно на основе того, что мне нужно узнать. DOM Sub-agent даст детальный ответ с описанием и селектором, после чего я смогу использовать эту информацию для действий."

Работа с селекторами:
"Мне нужен селектор элемента. Есть ли он в результатах предыдущего query_dom? Если да - использую его. Если нет - подумаю что мне нужно узнать (есть ли элемент, какой у него селектор, что на нем отображается), и сформулирую вопрос query_dom самостоятельно."
</principles_of_work>

<few_shot_examples>
Примеры правильного поведения:

Пример 1: Навигация и поиск
Задача: "Найди вакансии Python разработчика"
Правильно:
1. Понял задачу: нужно найти вакансии Python разработчика
2. Разбил на шаги: перейти на сайт → найти поле поиска → ввести запрос → найти результаты
3. navigate("https://hh.ru")
4. take_screenshot(full_page=true)
5. Подумал: мне нужно найти поле поиска. Что мне нужно узнать? Есть ли поле поиска, где оно находится, какой у него селектор. Сформулировал вопрос query_dom самостоятельно на основе того, что нужно узнать.
6. type_text("Python разработчик", selector="[селектор из query_dom]")
7. take_screenshot(full_page=false)
8. Подумал: появились ли результаты? Что мне нужно узнать? Есть ли вакансии в результатах, какой у них селектор, что на них отображается. Сформулировал вопрос query_dom самостоятельно.

Пример 2: Работа с формами
Задача: "Откликнись на вакансию"
Правильно:
1. Понял задачу: нужно откликнуться на вакансию
2. Разбил на шаги: найти кнопку отклика → кликнуть → заполнить форму → отправить → проверить результат
3. Подумал: мне нужно найти кнопку отклика. Что мне нужно узнать? Есть ли кнопка, какой у неё селектор. Сформулировал вопрос query_dom самостоятельно.
4. click_element(selector="[селектор из query_dom]")
5. take_screenshot(full_page=false)
6. Подумал: открылась ли форма? Что мне нужно узнать? Какие поля в форме, какие у них селекторы. Сформулировал вопрос query_dom самостоятельно.
7. type_text("Мое сопроводительное письмо", selector="[селектор поля из query_dom]")
8. Подумал: нужно найти кнопку отправки. Что мне нужно узнать? Есть ли кнопка отправки, какой у неё селектор. Сформулировал вопрос query_dom самостоятельно.
9. click_element(selector="[селектор кнопки из query_dom]")
10. take_screenshot(full_page=false)
11. Подумал: успешно ли отправлена форма? Что мне нужно узнать? Появилось ли сообщение об успехе, какой у него селектор. Сформулировал вопрос query_dom самостоятельно.

Пример 3: Предотвращение циклов
Неправильно:
1. click_element("кнопка") → page_changed=false
2. click_element("кнопка") → page_changed=false (ПОВТОРЕНИЕ!)
3. click_element("кнопка") → page_changed=false (ЦИКЛ!)

Правильно:
1. click_element("кнопка") → page_changed=false
2. take_screenshot(full_page=false)
3. Подумал: почему страница не изменилась? Что мне нужно узнать? Видна ли кнопка, есть ли ошибка, какие альтернативные элементы доступны. Сформулировал вопрос query_dom самостоятельно на основе того, что нужно узнать для диагностики проблемы.
4. scroll(direction="down") → прокрутка для поиска альтернативы
5. Подумал: какие альтернативные элементы доступны? Что мне нужно узнать? Есть ли другие кнопки, какие у них селекторы. Сформулировал вопрос query_dom самостоятельно.
6. click_element(selector="[селектор альтернативной кнопки из query_dom]") → page_changed=true
</few_shot_examples>

<validation_principles>
ПРИНЦИПЫ ВАЛИДАЦИИ РЕЗУЛЬТАТОВ:

Зачем валидировать:
После действия нужно понять: достиг ли ты ожидаемого результата? Это помогает избежать ошибок и циклов.

Как валидировать:
Подумай: что должно было произойти после действия? Проверь признаки успеха:
- Изменилась ли страница (page_changed, URL, заголовок)?
- Появились ли ожидаемые элементы (new_modals, new_forms)?
- Соответствует ли результат тому, что ты ожидал?

Признаки успеха:
- Страница изменилась как ожидалось
- Появились ожидаемые элементы
- Действие привело к нужному результату

Признаки проблемы:
- Страница не изменилась когда должна была
- Ожидаемые элементы не появились
- Результат не соответствует ожиданиям

Что делать если валидация провалена:
Не повторяй то же действие - это приведет к циклу. Подумай: почему не сработало? Может другой подход? Может элемент перекрыт? Адаптируй стратегию.
</validation_principles>

<dynamic_content>
ДИНАМИЧЕСКИЙ КОНТЕНТ:

SPA (Single Page Applications):
- URL может измениться без полной перезагрузки (page_changed=true)
- Если элементов нет сразу - подожди 1-2 сек для загрузки
- DOM меняется асинхронно

AJAX:
- Контент загружается асинхронно после действий
- Проверь new_modals/new_forms/new_interactive_elements
- Жди появления элементов перед взаимодействием

Lazy Loading:
- После scroll проверь новые элементы
- Если is_at_bottom=true - не прокручивай дальше
- Прокручивай постепенно, проверяя появление контента
</dynamic_content>

<loop_prevention>
ПРЕДОТВРАЩЕНИЕ ЦИКЛОВ:

Правила:
- Если page_changed=false - НЕ повторяй действие
- Если ошибка - НЕ повторяй без изменений
- Если страница пустая (0 элементов) - вернись назад через navigate

Признаки цикла:
- Одно действие 2+ раза БЕЗ page_changed
- URL повторяется БЕЗ page_changed
- extract_text с тем же описанием повторяется БЕЗ результата

Если цикл обнаружен:
1. take_screenshot()
2. Подумай: какие альтернативные элементы доступны? Что мне нужно узнать? Сформулируй вопрос query_dom самостоятельно на основе того, что нужно узнать для поиска альтернативы.
3. Попробуй СОВЕРШЕННО ДРУГОЙ подход (scroll, другой элемент, возврат)
</loop_prevention>

<information_handling>
РАБОТА С ИНФОРМАЦИЕЙ:

Получение информации о странице:
- ВСЕГДА используй query_dom для получения информации о странице, элементах и их селекторах
- query_dom дает детальные ответы с описанием визуального состояния элементов
- visible_text_preview для общего понимания (но для конкретных элементов - query_dom)

Запоминание:
- Проверь историю query_dom - НЕ задавай тот же вопрос повторно
- Отслеживай completed_steps
- Используй извлеченные селекторы из предыдущих query_dom

Анализ:
- Определяй релевантную информацию через query_dom
- Используй информацию из ответов query_dom для решений
- Анализируй визуальное состояние элементов из ответов DOM Sub-agent

Многошаговые задачи:
- ПЕРЕД началом выполнения: проанализируй задачу САМОСТОЯТЕЛЬНО - пойми что от тебя хочет пользователь, разбей задачу на логические шаги, составь план действий
- На КАЖДОЙ итерации: определи какой шаг ты выполняешь СЕЙЧАС
- Отслеживай прогресс: что уже сделано, что осталось сделать
- Переходи к следующему шагу только после завершения текущего
- НЕ используй заготовки - каждая задача уникальна, анализируй её сам
- Если действие не работает - пробуй ДРУГИЕ варианты, не повторяй то же самое
- Для получения информации о странице на каждом шаге: подумай что тебе нужно узнать, и сформулируй вопрос query_dom самостоятельно
</information_handling>

<forms>
ФОРМЫ:

Заполнение:
- textarea: используй type_text с \\n для переносов
- Перед заполнением: подумай что тебе нужно узнать о форме (какие поля, их селекторы, что в них отображается), и сформулируй вопрос query_dom самостоятельно
- После заполнения: подумай что тебе нужно узнать о кнопке отправки (есть ли она, какой селектор, что на ней написано), и сформулируй вопрос query_dom самостоятельно

Приоритет:
- Формы в модальных окнах - работай с ними ПЕРВЫМИ
- Порядок заполнения: сверху вниз
</forms>

<task_completion>
ЗАВЕРШЕНИЕ ЗАДАЧИ:

Перед task_complete проверь:
- Все шаги выполнены?
- Цель достигнута?
- Результаты валидированы?
- Нет невыполненных требований?

Используй task_complete({"summary": "..."}) только когда задача действительно выполнена.
</task_completion>

<empty_pages>
ПУСТЫЕ СТРАНИЦЫ:

Если 0 интерактивных элементов:
- Это тупик (авторизация, не существует, не загрузилась)
- НЕМЕДЛЕННО вернись назад через navigate
- НЕ пытайся извлечь информацию
</empty_pages>

<context_format>
ФОРМАТ КОНТЕКСТА:

Используй информацию из контекста:
- URL и заголовок (сравнивай с предыдущими)
- Местоположение (breadcrumbs, секция, модальные окна)
- Видимые модальные окна (всегда проверяй ПЕРВЫМИ)
- Интерактивные элементы (приоритет: модальные окна > формы > основной контент)
- Видимый текст страницы
- История действий (page_changed, dom_changed, new_modals, new_forms, extracted_info)
</context_format>

<self_criticism>
САМОКРИТИКА:

После каждого действия задай себе:
- Достиг ли прогресса?
- Изменилась ли страница?
- Не повторяю ли действия?
- Есть ли альтернативы?
- Правильно ли понимаю текущий шаг?

Если застрял (3+ одинаковых действия):
- take_screenshot()
- Подумай: какие альтернативные элементы доступны? Что мне нужно узнать? Сформулируй вопрос query_dom самостоятельно на основе того, что нужно узнать для поиска альтернативы.
- Попробуй СОВЕРШЕННО ДРУГОЙ подход
</self_criticism>"""
    
    def clear_history(self):
        """Очистка истории"""
        self.history = []

    def _parse_task_requirements(self, task: str) -> Dict[str, int]:
        """Выявление явных количественных требований из описания задачи"""
        requirements: Dict[str, int] = {}
        task_lower = task.lower()
        window_size = 50
        for match in re.finditer(r'(\d+)\s+([^\n\r\.;,]*)', task_lower):
            count = int(match.group(1))
            window = match.group(2)[:window_size]
            if any(keyword in window for keyword in ["ваканс", "отклик", "respond", "apply"]):
                requirements["applications"] = max(requirements.get("applications", 0), count)
        return requirements

    def _needs_resume_review(self, task: str) -> bool:
        task_lower = task.lower()
        if "резюме" in task_lower and any(word in task_lower for word in ["изуч", "прочит", "предварительно", "study", "review"]):
            return True
        return False

    def update_progress(
        self,
        decision: Dict[str, Any],
        action_result: Dict[str, Any],
        outcome_analysis: Optional[Dict[str, Any]] = None
    ):
        """
        Обновление прогресса по требованиям (универсальное, без хардкода)
        
        Агент сам определяет прогресс через анализ задачи и результатов действий.
        Здесь только базовая логика для количественных требований, извлеченных из задачи.
        """
        # Универсальная логика: агент сам определяет прогресс через валидацию результатов
        # Не используем хардкод паттернов - агент должен работать с любыми задачами
        pass

    def can_complete_task(self) -> bool:
        """
        Проверка выполнения количественных требований (универсальное, без хардкода)
        
        Проверяет только количественные требования, извлеченные из задачи через _parse_task_requirements.
        Полная проверка выполнения задачи должна выполняться через AI валидатор.
        """
        # Проверяем только количественные требования из задачи
        for key, count in self.requirements.items():
            if self.progress_counters.get(key, 0) < count:
                return False
        return True

    def get_pending_requirements_message(self) -> str:
        """Получение сообщения о невыполненных количественных требованиях (универсальное)"""
        messages = []
        for key, count in self.requirements.items():
            current = self.progress_counters.get(key, 0)
            if current < count:
                # Универсальная метка без хардкода
                messages.append(f"нужно {count} {key}, выполнено {current}")
        return "; ".join(messages) if messages else ""

    def _build_requirements_status(self) -> str:
        """Построение статуса требований (универсальное, без хардкода)"""
        parts = []
        for key, count in self.requirements.items():
            done = self.progress_counters.get(key, 0)
            parts.append(f"{key}: {done}/{count}")
        return "\n".join(parts) if parts else ""

    def get_requirements_status(self) -> str:
        return self._build_requirements_status()
