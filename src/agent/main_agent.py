"""Основной AI-агент для автоматизации браузера"""
import asyncio
import json
import re
from typing import Dict, Any, Optional, Callable
from openai import OpenAI

from src.browser.controller import BrowserController
from src.browser.page_extractor import PageExtractor
from src.context.manager import ContextManager
from src.actions.action_executor import ActionExecutor
from src.actions.action_tools import get_action_tools
from src.security.security_layer import SecurityLayer
from src.agent.sub_agents import SubAgentManager
from src.agent.agent_state import AgentState, AgentStateManager
from src.agent.action_validator import ActionResultValidator
from src.error.error_handler import ErrorHandler
from config import OPENAI_API_KEY, OPENAI_MODEL, MAX_ITERATIONS, ENABLE_SUB_AGENTS


class Logger:
    """Простой логгер для вывода информации"""
    
    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None):
        """
        Инициализация логгера
        
        Args:
            log_callback: Функция для вывода логов (level, message)
        """
        self.log_callback = log_callback
    
    def log(self, level: str, message: str):
        """Вывод лога"""
        if self.log_callback:
            self.log_callback(level, message)
        else:
            print(f"[{level}] {message}")
    
    def info(self, message: str):
        """Информационное сообщение"""
        self.log("INFO", message)
    
    def success(self, message: str):
        """Сообщение об успехе"""
        self.log("SUCCESS", message)
    
    def warning(self, message: str):
        """Предупреждение"""
        self.log("WARNING", message)
    
    def error(self, message: str):
        """Ошибка"""
        self.log("ERROR", message)
    
    def debug(self, message: str):
        """Отладочное сообщение"""
        self.log("DEBUG", message)


class MainAgent:
    """Основной автономный AI-агент"""
    
    def __init__(
        self,
        browser_controller: BrowserController,
        user_confirmation_callback: Optional[Callable[[str], bool]] = None,
        logger: Optional[Logger] = None
    ):
        """
        Инициализация основного агента
        
        Args:
            browser_controller: Контроллер браузера
            user_confirmation_callback: Функция для запроса подтверждения у пользователя
            logger: Логгер для вывода информации
        """
        self.browser = browser_controller
        self.logger = logger or Logger()
        # PageExtractor будет инициализирован после запуска браузера
        self.page_extractor: Optional[PageExtractor] = None
        self.context_manager = ContextManager(OPENAI_MODEL)
        # ActionExecutor будет инициализирован после инициализации page_extractor
        self.action_executor: Optional[ActionExecutor] = None
        self.security_layer = SecurityLayer(user_confirmation_callback)
        self.sub_agent_manager = SubAgentManager(ENABLE_SUB_AGENTS)
        self.state_manager = AgentStateManager()
        self.error_handler = ErrorHandler()
        self.action_validator = ActionResultValidator()
        
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.action_tools = get_action_tools()
        self.user_confirmation_callback = user_confirmation_callback
        
        self.max_iterations = MAX_ITERATIONS
        self.current_iteration = 0
        self._should_stop = False  # Флаг для прерывания выполнения
        # Отслеживание состояния страницы для обнаружения изменений
        self._page_state_before_action: Optional[Dict[str, str]] = None
    
    async def execute_task(self, task: str) -> Dict[str, Any]:
        """
        Выполнение задачи
        
        Args:
            task: Описание задачи
            
        Returns:
            Результат выполнения задачи
        """
        # Инициализируем PageExtractor если еще не инициализирован
        if not self.page_extractor and self.browser.page:
            self.page_extractor = PageExtractor(self.browser.page, logger=self.logger)
        
        if not self.page_extractor:
            return {
                "success": False,
                "error": "Браузер не инициализирован"
            }
        
        # Инициализируем ActionExecutor если еще не инициализирован
        if not self.action_executor:
            self.action_executor = ActionExecutor(
                self.browser, 
                self.page_extractor, 
                logger=self.logger,
                sub_agent_manager=self.sub_agent_manager,
                state_manager=self.state_manager
            )
        
        # Устанавливаем текущую задачу в ActionExecutor для анализа модальных окон
        self.action_executor.set_task(task)
        
        self.state_manager.set_task(task)
        self.context_manager.set_task(task)
        self.current_iteration = 0
        self._should_stop = False  # Сброс флага прерывания
        
        try:
            self.logger.info(f"Начало выполнения задачи: {task}")
            while self.current_iteration < self.max_iterations and not self._should_stop:
                self.current_iteration += 1
                self.logger.info(f"\n--- Итерация {self.current_iteration}/{self.max_iterations} ---")
                
                # 1. OBSERVE - Наблюдение за текущим состоянием страницы
                self.state_manager.set_state(AgentState.OBSERVING)
                self.logger.info("🔍 Наблюдение за текущим состоянием страницы...")
                page_info = await self.page_extractor.extract_page_info()
                
                # Получаем состояние страницы для обнаружения изменений
                page_state = await self.page_extractor.get_page_state_hash()
                page_info["metadata"]["page_state"] = page_state
                
                # Добавляем информацию о местоположении
                try:
                    location_context = await self.page_extractor._extract_location_context()
                    page_info["location_context"] = location_context
                except Exception as e:
                    # Если не удалось извлечь информацию о местоположении - продолжаем без неё
                    pass
                
                current_url = page_info.get('url', '')
                
                # Отслеживание посещенных URL
                if current_url:
                    if self.state_manager.is_url_visited(current_url):
                        self.logger.warning(f"⚠️  Страница уже была посещена ранее: {current_url}")
                    else:
                        self.state_manager.add_visited_url(current_url)
                
                # Проверка на циклы - АГРЕССИВНОЕ обнаружение
                loop_info = self.state_manager.detect_loop()
                if loop_info:
                    reason = loop_info.get("reason", "Повторяющиеся действия")
                    self.logger.error(f"🔄 Обнаружен цикл! {reason}")
                    return {
                        "success": False,
                        "error": f"Обнаружен цикл: {reason}",
                        "loop_info": loop_info,
                        "suggestion": "Агент застрял в цикле повторяющихся действий. Попробуйте уточнить задачу или разбить её на более мелкие шаги.",
                        "iteration": self.current_iteration
                    }
                
                context = self.context_manager.prepare_context(page_info)
                self.logger.info(f"📄 Страница: {current_url} | Заголовок: {page_info.get('title', 'unknown')}")
                self.logger.info(f"🎯 Найдено интерактивных элементов: {len(page_info.get('interactive_elements', []))}")
                
                # 2. DECIDE - Принятие решения о следующем действии
                self.state_manager.set_state(AgentState.DECIDING)
                self.logger.info("🤔 Принятие решения о следующем действии...")
                decision = await self._decide_action(context, task, page_info)
                
                if not decision.get("success"):
                    return {
                        "success": False,
                        "error": decision.get("error", "Не удалось принять решение"),
                        "iteration": self.current_iteration
                    }
                
                # Проверка на завершение задачи
                if decision.get("action") == "task_complete":
                    # Проверяем количественные требования
                    if not self.context_manager.can_complete_task():
                        reason = self.context_manager.get_pending_requirements_message() or "Требования задачи не выполнены"
                        self.logger.warning(f"⚠️  Преждевременное завершение отклонено: {reason}")
                        self.context_manager.add_to_history(
                            "task_complete_rejected",
                            {
                                "success": False,
                                "error": reason
                            }
                        )
                        continue
                    
                    # Универсальная проверка выполнения задачи через AI валидатор
                    try:
                        completion_check = await self.action_validator.check_task_completion(
                            task=task,
                            history=self.state_manager.action_history,
                            page_info=page_info,
                            completed_steps=self.context_manager.completed_steps,
                            extracted_info=self.context_manager.extracted_info
                        )
                        
                        if not completion_check.get("is_completed", False):
                            completion_msg = completion_check.get("message", "Задача не выполнена")
                            missing_steps = completion_check.get("missing_steps", [])
                            suggestions = completion_check.get("suggestions", [])
                            
                            self.logger.warning(f"⚠️  Проверка выполнения задачи провалена: {completion_msg}")
                            if missing_steps:
                                self.logger.warning(f"   Невыполненные шаги: {', '.join(missing_steps)}")
                            if suggestions:
                                self.logger.info(f"   Рекомендации: {', '.join(suggestions)}")
                            
                            self.context_manager.add_to_history(
                                "task_complete_rejected",
                                {
                                    "success": False,
                                    "error": completion_msg,
                                    "missing_steps": missing_steps,
                                    "suggestions": suggestions
                                }
                            )
                            continue
                        else:
                            completion_percentage = completion_check.get("completion_percentage", 100)
                            self.logger.info(f"✓ Проверка выполнения задачи пройдена ({completion_percentage}%)")
                    except Exception as completion_error:
                        self.logger.warning(f"Не удалось проверить выполнение задачи через валидатор: {completion_error}")
                        # Продолжаем с базовой проверкой количественных требований
                    
                    self.state_manager.set_state(AgentState.COMPLETED)
                    summary = decision.get("parameters", {}).get("summary", "Задача выполнена")
                    self.logger.success(f"✅ Задача выполнена: {summary}")
                    return {
                        "success": True,
                        "message": summary,
                        "iterations": self.current_iteration
                    }
                
                # Агент должен действовать самостоятельно, без запросов к пользователю
                
                # 3. ACT - Выполнение действия
                self.state_manager.set_state(AgentState.ACTING)
                action_name = decision.get("action", "unknown")
                action_params = decision.get("parameters", {})
                self.logger.info(f"⚙️  Выполнение действия: {action_name}")
                if action_params:
                    params_str = ", ".join([f"{k}={v}" for k, v in action_params.items() if k not in ["selector"]])
                    if params_str:
                        self.logger.info(f"   Параметры: {params_str}")
                
                # Сохраняем состояние страницы ПЕРЕД действием (URL, заголовок, DOM-хеш)
                page_state_before = await self.page_extractor.get_page_state_hash()
                # Получаем текущую позицию прокрутки для scroll действий
                scroll_y_before = 0
                if action_name == "scroll":
                    try:
                        scroll_y_before = await self.browser.page.evaluate("() => window.scrollY || window.pageYOffset")
                    except:
                        pass
                
                self._page_state_before_action = {
                    "url": current_url,
                    "title": page_info.get('title', ''),
                    "dom_hash": page_state_before.get('dom_hash', ''),
                    "interactive_count": page_state_before.get('interactive_count', 0),
                    "visible_modal_count": page_state_before.get('visible_modal_count', 0),
                    "visible_form_count": page_state_before.get('visible_form_count', 0),
                    "scroll_y": scroll_y_before
                }
                
                # Проверка для query_dom: был ли уже задан этот вопрос?
                if action_name == "query_dom":
                    query = action_params.get("query", "")
                    if query and self.state_manager.was_query_asked(query, current_url):
                        # Вопрос уже был задан - получаем ответ из истории
                        previous_answer = self.state_manager.get_query_answer(query, current_url)
                        self.logger.warning(f"⚠️  Вопрос query_dom уже был задан ранее: {query[:100]}")
                        if previous_answer:
                            self.logger.info(f"   Используй предыдущий ответ: {previous_answer[:200]}")
                        # Все равно выполняем действие, но предупреждаем агента
                
                action_result = await self._execute_action(decision)
                
                # Адаптивное ожидание загрузки динамического контента
                await self._wait_for_dynamic_content(action_name, action_result)
                
                page_info_after = await self.page_extractor.extract_page_info()
                page_state_after = await self.page_extractor.get_page_state_hash()
                
                url_after = page_info_after.get('url', '')
                title_after = page_info_after.get('title', '')
                dom_hash_after = page_state_after.get('dom_hash', '')
                
                # Оптимизированное обнаружение изменений: проверяем только критичные атрибуты
                url_changed = url_after != self._page_state_before_action["url"]
                title_changed = title_after != self._page_state_before_action["title"]
                # DOM hash проверяем только для критических действий
                dom_changed = False
                if action_name in ["navigate", "click_element", "reload_page"]:
                    dom_changed = dom_hash_after != self._page_state_before_action["dom_hash"]
                else:
                    # Для остальных действий проверяем только если DOM hash сильно изменился (более 10%)
                    # Это оптимизация - не вычисляем точное сравнение для некритических действий
                    if dom_hash_after and self._page_state_before_action["dom_hash"]:
                        # Простая проверка: если хеши разные - DOM изменился
                        dom_changed = dom_hash_after != self._page_state_before_action["dom_hash"]
                
                # Обнаружение новых элементов (модальные окна, формы) - только критичные изменения
                new_modals = page_state_after.get('visible_modal_count', 0) > self._page_state_before_action["visible_modal_count"]
                new_forms = page_state_after.get('visible_form_count', 0) > self._page_state_before_action["visible_form_count"]
                # Для интерактивных элементов проверяем только значительное увеличение (>10%)
                interactive_before = self._page_state_before_action["interactive_count"]
                interactive_after = page_state_after.get('interactive_count', 0)
                new_interactive = interactive_after > interactive_before and (
                    interactive_before == 0 or (interactive_after - interactive_before) / interactive_before > 0.1
                )
                
                # Для scroll действия: проверяем изменение scroll position и появление новых элементов
                scroll_position_changed = False
                if action_name == "scroll":
                    scroll_result = action_result.get("scroll_position")
                    if scroll_result:
                        # Проверяем, изменилась ли позиция прокрутки
                        scroll_before_y = self._page_state_before_action.get("scroll_y", 0)
                        scroll_after_y = scroll_result.get("y", 0) if isinstance(scroll_result, dict) else 0
                        scroll_position_changed = scroll_after_y != scroll_before_y
                        
                        # Сохраняем позицию прокрутки для следующей проверки
                        self._page_state_before_action["scroll_y"] = scroll_after_y
                
                # Страница изменилась, если изменился URL, заголовок, DOM или появились новые элементы
                # Для scroll также учитываем изменение позиции прокрутки или появление новых элементов
                page_changed = url_changed or title_changed or dom_changed or new_modals or new_forms or (action_name == "scroll" and (scroll_position_changed or new_interactive))
                
                # Информация о новых элементах
                new_elements_info = {
                    "new_modals": new_modals,
                    "new_forms": new_forms,
                    "new_interactive_elements": new_interactive,
                    "modals_info": page_state_after.get('modals', [])
                }
                
                # Добавляем информацию об изменении страницы в результат действия
                action_result["page_changed"] = page_changed
                action_result["url_before"] = self._page_state_before_action["url"]
                action_result["url_after"] = url_after
                action_result["title_before"] = self._page_state_before_action["title"]
                action_result["title_after"] = title_after
                action_result["dom_changed"] = dom_changed
                action_result["new_elements"] = new_elements_info
                
                # Логируем изменение страницы
                if page_changed:
                    changes = []
                    if url_changed:
                        changes.append(f"URL: {self._page_state_before_action['url']} → {url_after}")
                    if title_changed:
                        changes.append(f"Заголовок: '{self._page_state_before_action['title']}' → '{title_after}'")
                    if dom_changed:
                        changes.append(f"DOM изменился (хеш: {self._page_state_before_action['dom_hash']} → {dom_hash_after})")
                    if new_modals:
                        changes.append(f"Появилось модальное окно (было: {self._page_state_before_action['visible_modal_count']}, стало: {page_state_after.get('visible_modal_count', 0)})")
                    if new_forms:
                        changes.append(f"Появилась форма (было: {self._page_state_before_action['visible_form_count']}, стало: {page_state_after.get('visible_form_count', 0)})")
                    if new_interactive:
                        changes.append(f"Новые интерактивные элементы (было: {self._page_state_before_action['interactive_count']}, стало: {page_state_after.get('interactive_count', 0)})")
                    
                    self.logger.info(f"📄 Страница изменилась: {'; '.join(changes)}")
                else:
                    # Для type_text и query_dom это нормально - страница не должна изменяться
                    if action_name not in ["type_text", "query_dom"]:
                        self.logger.warning(f"⚠️  Страница НЕ изменилась после действия '{action_name}' (URL, заголовок и DOM остались теми же)")
                        self.logger.warning(f"   Это может означать, что действие не сработало или не привело к переходу")
                    else:
                        # Для type_text и query_dom это нормально
                        if action_name == "type_text":
                            self.logger.debug(f"ℹ️  Страница не изменилась после type_text (это нормально - только заполнение поля)")
                        elif action_name == "query_dom":
                            self.logger.debug(f"ℹ️  Страница не изменилась после query_dom (это нормально - информационный запрос)")
                
                # Анализ результата действия через суб-агента (для обнаружения мастеров резюме и т.п.)
                outcome_analysis = None
                if ENABLE_SUB_AGENTS and self.sub_agent_manager:
                    try:
                        post_action_context = self.context_manager.prepare_context(page_info_after)
                        params_repr = ", ".join([f"{k}={v}" for k, v in action_params.items()]) or action_name
                        outcome_analysis = await self.sub_agent_manager.evaluate_outcome(
                            post_action_context,
                            task,
                            f"{action_name}({params_repr})"
                        )
                        if outcome_analysis.get("resume_wizard"):
                            reason = outcome_analysis.get("reason", "обнаружен мастер резюме")
                            self.logger.warning(f"⚠️  Обнаружен мастер резюме: {reason}")
                    except Exception as outcome_error:
                        self.logger.warning(f"Не удалось оценить результат действия через суб-агента: {outcome_error}")
                
                # Валидация результата действия через AI (только для критических действий)
                validation_result = None
                try:
                    # Валидируем только критические действия
                    if self.action_validator._is_critical_action(action_name):
                        validation_result = await self.action_validator.validate_action_result(
                            action=action_name,
                            action_params=action_params,
                            action_result=action_result,
                            task=task,
                            page_info=page_info_after,
                            history=self.state_manager.action_history[-5:] if len(self.state_manager.action_history) > 0 else []
                        )
                        
                        if not validation_result.get("is_valid", True):
                            validation_msg = validation_result.get("validation_message", "")
                            cache_info = " (из кэша)" if validation_result.get("from_cache", False) else ""
                            heuristic_info = " (эвристика)" if validation_result.get("heuristic", False) else ""
                            self.logger.warning(f"⚠️  Валидация результата действия провалена{cache_info}{heuristic_info}: {validation_msg}")
                            # Добавляем информацию о валидации в результат действия
                            action_result["validation_failed"] = True
                            action_result["validation_message"] = validation_msg
                            action_result["validation_suggestions"] = validation_result.get("suggestions", [])
                        else:
                            cache_info = " (из кэша)" if validation_result.get("from_cache", False) else ""
                            self.logger.info(f"✓ Валидация результата действия пройдена{cache_info}")
                    else:
                        self.logger.debug(f"Валидация пропущена для некритического действия: {action_name}")
                except Exception as validation_error:
                    self.logger.warning(f"Не удалось выполнить валидацию результата действия: {validation_error}")
                
                # Добавляем в историю с информацией об изменении страницы
                action_record = {
                    "action": decision.get("action"),
                    "parameters": decision.get("parameters"),
                    "result": action_result,
                    "page_changed": page_changed,
                    "url_before": self._page_state_before_action["url"],
                    "url_after": url_after,
                    "title_before": self._page_state_before_action["title"],
                    "title_after": title_after,
                    "dom_changed": dom_changed,
                    "new_elements": new_elements_info
                }
                self.state_manager.add_action(action_record)
                
                # Если это query_dom - сохраняем вопрос и ответ в историю
                if action_name == "query_dom" and action_result.get("success"):
                    query = action_params.get("query", "")
                    answer = action_result.get("answer", "") or action_result.get("message", "")
                    if query:
                        self.state_manager.add_query_dom(query, answer, current_url)
                
                # Если это навигация, отслеживаем URL
                if decision.get("action") == "navigate" and action_result.get("success"):
                    final_url = action_result.get("url", "")
                    if final_url:
                        self.state_manager.add_visited_url(final_url)
                self.context_manager.add_to_history(
                    f"{decision.get('action')}({decision.get('parameters')})",
                    action_result
                )
                if action_result.get("success"):
                    try:
                        self.context_manager.update_progress(decision, action_result, outcome_analysis)
                    except Exception as progress_error:
                        self.logger.warning(f"Не удалось обновить прогресс задачи: {progress_error}")
                
                # Отслеживание прогресса задачи (универсальное, без хардкода)
                if action_result.get("success"):
                    action_name = decision.get("action", "")
                    # Сохраняем извлеченную информацию для extract_text (универсальное действие)
                    if action_name == "extract_text":
                        extracted_text = action_result.get("text", "")
                        if extracted_text:
                            desc = decision.get("parameters", {}).get("description", "текст")
                            self.context_manager.add_extracted_info(desc, extracted_text)
                    # Агент сам определяет выполненные шаги через анализ задачи и контекста
                    # Не используем хардкод паттернов - агент должен работать с любыми задачами
                
                # 4. REFLECT - Рефлексия над результатом
                self.state_manager.set_state(AgentState.REFLECTING)
                if action_result.get("success"):
                    self.logger.success(f"✓ Действие выполнено успешно")
                    if action_result.get("message"):
                        self.logger.info(f"   {action_result.get('message')}")
                else:
                    # Обработка ошибки через ErrorHandler
                    error_msg = action_result.get("error", "Unknown error")
                    self.logger.error(f"✗ Ошибка при выполнении действия: {error_msg}")
                    
                    # Используем ErrorHandler для анализа ошибки и получения рекомендаций
                    try:
                        # Формируем краткий контекст для анализа ошибки
                        error_context = f"URL: {current_url}\nЗаголовок: {page_info.get('title', '')}\nДействие: {action_name}"
                        
                        # Анализируем ошибку через ErrorHandler
                        error_analysis = await self.error_handler.handle_error(
                            Exception(error_msg),
                            action_name,
                            error_context,
                            retry_count=0
                        )
                        
                        # Добавляем рекомендацию от ErrorHandler в контекст для следующей итерации
                        if error_analysis.get("suggestion"):
                            suggestion = error_analysis.get("suggestion", "")
                            strategy = error_analysis.get("strategy", "")
                            self.logger.info(f"💡 Рекомендация: {suggestion}")
                            if strategy:
                                self.logger.info(f"   Стратегия: {strategy}")
                            
                            # Сохраняем рекомендацию в истории для использования в следующей итерации
                            action_result["error_suggestion"] = suggestion
                            action_result["error_strategy"] = strategy
                    except Exception as e:
                        # Если ErrorHandler не сработал - продолжаем без него
                        self.logger.warning(f"Не удалось проанализировать ошибку через ErrorHandler: {e}")
                    
                    # Проверяем, не застряли ли мы в цикле - АГРЕССИВНОЕ обнаружение
                    loop_info = self.state_manager.detect_loop()
                    if loop_info:
                        reason = loop_info.get("reason", "Повторяющиеся действия")
                        self.logger.error(f"🔄 Обнаружен цикл после ошибки! {reason}")
                        return {
                            "success": False,
                            "error": f"Обнаружен цикл: {reason}",
                            "loop_info": loop_info,
                            "iteration": self.current_iteration
                        }
                
                # Небольшая задержка между действиями
                await asyncio.sleep(1)
            
            # Проверка причины выхода из цикла
            if self._should_stop:
                self.logger.warning("⚠️  Получен сигнал остановки")
                return {
                    "success": False,
                    "error": "Задача прервана пользователем",
                    "iteration": self.current_iteration,
                    "interrupted": True
                }
            
            # Превышен лимит итераций
            self.logger.warning(f"⚠️  Превышен лимит итераций ({self.max_iterations})")
            return {
                "success": False,
                "error": f"Превышен лимит итераций ({self.max_iterations})",
                "iterations": self.current_iteration
            }
        
        except KeyboardInterrupt:
            self.logger.warning("⚠️  Выполнение задачи прервано пользователем (Ctrl+C)")
            self._should_stop = True
            return {
                "success": False,
                "error": "Задача прервана пользователем",
                "iteration": self.current_iteration,
                "interrupted": True
            }
        except Exception as e:
            self.state_manager.set_error(str(e))
            self.logger.error(f"💥 Критическая ошибка: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "iteration": self.current_iteration
            }
    
    def stop(self):
        """Остановка выполнения задачи"""
        self._should_stop = True
        self.logger.warning("⚠️  Получен сигнал остановки")
    
    def _detect_navigation_loop(self) -> Optional[str]:
        """
        Обнаружение циклов навигации между одними и теми же страницами
        
        Returns:
            Предупреждение о цикле или None
        """
        if len(self.state_manager.action_history) < 4:
            return None
        
        # Получаем последние навигационные действия
        recent_navigations = []
        for action in self.state_manager.action_history[-10:]:
            if action.get("action") == "navigate":
                url = action.get("url", "")
                if url:
                    # Нормализуем URL (убираем параметры для сравнения)
                    normalized_url = url.split("?")[0].split("#")[0]
                    recent_navigations.append(normalized_url)
        
        # Проверяем паттерны циклов
        if len(recent_navigations) >= 4:
            # Паттерн A-B-A-B (переход туда-сюда)
            if len(recent_navigations) >= 4:
                last_four = recent_navigations[-4:]
                if (last_four[0] == last_four[2] and 
                    last_four[1] == last_four[3] and 
                    last_four[0] != last_four[1]):
                    return "Обнаружен цикл навигации! Ты переходишь между двумя страницами туда-сюда. ОСТАНОВИСЬ! Работай с контентом на текущей странице!"
            
            # Паттерн повторения одной и той же страницы
            if len(recent_navigations) >= 3:
                last_three = recent_navigations[-3:]
                if len(set(last_three)) == 1:
                    return "Обнаружен цикл! Ты переходишь на одну и ту же страницу 3 раза подряд. ОСТАНОВИСЬ! Эта страница пустая или недоступна. Вернись на предыдущую страницу с контентом!"
        
        return None
    
    def _build_visited_urls_info(self) -> str:
        """Формирование информации о посещенных URL"""
        if not self.state_manager.visited_urls:
            return ""
        
        visited_urls_list = self.state_manager.visited_urls[-5:]
        info = f"\n\nПосещенные страницы ({len(self.state_manager.visited_urls)}):\n"
        for i, url in enumerate(visited_urls_list, 1):
            info += f"  {i}. {url}\n"
        info += "\nВАЖНО: Ты можешь вернуться на любую из этих страниц через navigate, если это нужно для выполнения задачи!"
        if len(self.state_manager.visited_urls) > 5:
            info += f"\n(показаны последние 5 из {len(self.state_manager.visited_urls)})"
        return info
    
    def _detect_consecutive_loop(self, recent_actions: list) -> str:
        """Обнаружение последовательных повторений действий (циклов)"""
        if len(recent_actions) < 2:
            return ""
        
        action_signatures = []
        for action in recent_actions:
            action_name = action.get("action", "unknown")
            params = action.get("parameters", {})
            desc = params.get("description") or params.get("element_description") or params.get("url", "")
            page_changed = action.get("page_changed", False)
            signature = f"{action_name}:{desc}"
            action_signatures.append((signature, page_changed))
        
        consecutive_repeats = 1
        last_signature = action_signatures[-1][0]
        last_page_changed = action_signatures[-1][1]
        
        for i in range(len(action_signatures) - 2, -1, -1):
            sig, page_ch = action_signatures[i]
            if sig == last_signature and not page_ch:
                consecutive_repeats += 1
            else:
                break
        
        if consecutive_repeats >= 3 and not last_page_changed:
            return f"""
КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: ОБНАРУЖЕН ЦИКЛ!
Действие '{last_signature}' повторяется {consecutive_repeats} раз подряд БЕЗ изменения страницы!
Это означает, что ты застрял в цикле повторяющихся действий.

НЕМЕДЛЕННО:
1. ОСТАНОВИСЬ и проанализируй ситуацию
2. НЕ ПОВТОРЯЙ это действие снова!
3. Попробуй СОВЕРШЕННО ДРУГОЙ подход:
   - Если это navigate - попробуй другой URL или вернись назад
   - Если это click_element - попробуй другой элемент или прокрути страницу (scroll)
   - Если это type_text - проверь, правильно ли ты нашел поле
   - Если это extract_text - информация уже извлечена, переходи к следующему шагу
4. Используй scroll для поиска альтернативных элементов
5. Если нужно - вернись на предыдущую страницу через navigate

ПОМНИ: Повторение одного и того же действия без результата - это ОШИБКА!

"""
        return ""
    
    def _build_recent_actions_info(self) -> tuple[str, str]:
        """Формирование информации о последних действиях"""
        if not self.state_manager.action_history:
            return "", ""
        
        recent_actions = self.state_manager.action_history[-5:]
        recent_actions_info = "\n\nПоследние выполненные действия:\n"
        loop_warning = self._detect_consecutive_loop(recent_actions)
        
        # Проверяем использование vision_analysis и query_dom после критических действий
        query_dom_missing_warning = ""
        vision_analysis_warning = ""
        if len(recent_actions) > 0:
            last_action = recent_actions[-1]
            last_action_name = last_action.get("action", "")
            last_action_result = last_action.get("result", {})
            
            # Проверяем использование vision_analysis после take_screenshot
            if last_action_name == "take_screenshot":
                vision_analysis = last_action_result.get("vision_analysis")
                if vision_analysis:
                    # Проверяем, используется ли vision_analysis в следующих действиях
                    # Если после take_screenshot сразу идет query_dom без использования vision_analysis - предупреждаем
                    if len(self.state_manager.action_history) > len(recent_actions):
                        next_action_idx = len(self.state_manager.action_history) - len(recent_actions)
                        if next_action_idx < len(self.state_manager.action_history):
                            next_action = self.state_manager.action_history[next_action_idx]
                            if next_action.get("action") == "query_dom":
                                # Проверяем, содержит ли vision_analysis информацию, которая могла бы ответить на query_dom
                                query_text = next_action.get("parameters", {}).get("query", "").lower()
                                vision_lower = vision_analysis.lower()
                                
                                # Ищем ключевые слова из query_dom в vision_analysis
                                common_keywords = ["кнопка", "button", "поле", "field", "ссылка", "link", "найти", "find", "откликнуться", "apply"]
                                has_relevant_info = any(keyword in vision_lower and keyword in query_text for keyword in common_keywords)
                                
                                if has_relevant_info:
                                    vision_analysis_warning = f"""
КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: После take_screenshot был получен vision_analysis, который содержит информацию о нужных элементах, но ты используешь query_dom вместо него!

vision_analysis уже содержит описание элементов с ТОЧНЫМ ТЕКСТОМ (например, "Кнопка: Найти").
ИСПОЛЬЗУЙ ЭТОТ ТЕКСТ для поиска элементов в списке интерактивных элементов по тексту и получения их селекторов напрямую!

ПРАВИЛЬНЫЙ ПОДХОД:
1. Прочитай vision_analysis из результата take_screenshot (он есть выше в контексте)
2. Извлеки ТОЧНЫЙ ТЕКСТ элемента из vision_analysis (например, из "Кнопка: Найти" извлеки "Найти")
3. Найди в списке интерактивных элементов элемент с таким текстом
4. Используй селектор найденного элемента напрямую в click_element() или type_text()

НЕПРАВИЛЬНО: take_screenshot() → vision_analysis содержит "Кнопка: Найти" → query_dom("Есть ли кнопка Найти?")
ПРАВИЛЬНО: take_screenshot() → vision_analysis содержит "Кнопка: Найти" → найди в списке интерактивных элементов элемент с текстом "Найти" → click_element("Найти")

query_dom нужен ТОЛЬКО если элемент НЕ найден в списке интерактивных элементов по тексту из vision_analysis!
"""
                                else:
                                    vision_analysis_warning = f"""
ВАЖНО: После take_screenshot был получен vision_analysis. Проверь, можно ли использовать текст элементов из vision_analysis для поиска в списке интерактивных элементов вместо query_dom.
query_dom нужен только если элемент НЕ найден в списке интерактивных элементов по тексту из vision_analysis.
"""
            
            # Критические действия, после которых ОБЯЗАТЕЛЬНО нужен анализ (vision_analysis или query_dom)
            critical_actions = ["navigate", "click_element", "type_text"]
            
            if last_action_name in critical_actions:
                # Проверяем, был ли анализ после этого действия
                has_analysis_after = False
                if len(self.state_manager.action_history) > 1:
                    # Проверяем следующие действия после последнего
                    next_action_idx = len(self.state_manager.action_history) - len(recent_actions) + 1
                    if next_action_idx < len(self.state_manager.action_history):
                        next_action = self.state_manager.action_history[next_action_idx]
                        next_action_name = next_action.get("action", "")
                        # Анализ может быть через take_screenshot (с vision_analysis) или query_dom
                        if next_action_name == "query_dom":
                            has_analysis_after = True
                        elif next_action_name == "take_screenshot":
                            # Проверяем есть ли vision_analysis в результате take_screenshot
                            next_result = next_action.get("result", {})
                            if next_result.get("vision_analysis"):
                                has_analysis_after = True
                
                if not has_analysis_after:
                    query_dom_missing_warning = f"""
КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: После действия '{last_action_name}' НЕ БЫЛ использован анализ!
ОБЯЗАТЕЛЬНО сделай take_screenshot (автоматически получишь vision_analysis) или используй query_dom СЕЙЧАС!
Пример: take_screenshot() → используй vision_analysis → если не помогло, тогда query_dom()
"""
        
        for i, action in enumerate(recent_actions, 1):
            action_name = action.get("action", "unknown")
            params = action.get("parameters", {})
            desc = params.get("description") or params.get("element_description") or params.get("url", "")
            result = action.get("result", {})
            success = result.get("success", False)
            page_changed = action.get("page_changed", False)
            success_marker = "✓" if success else "✗"
            page_marker = "[PAGE]" if page_changed else "[NO_CHANGE]"
            query_marker = "[QUERY]" if action_name == "query_dom" else ""
            vision_marker = "[VISION]" if action_name == "take_screenshot" and result.get("vision_analysis") else ""
            recent_actions_info += f"{i}. {success_marker} {page_marker} {query_marker} {vision_marker} {action_name}: {desc}\n"
        
        if query_dom_missing_warning:
            loop_warning += query_dom_missing_warning
        
        if vision_analysis_warning:
            loop_warning += vision_analysis_warning
        
        return recent_actions_info, loop_warning
    
    def _build_last_action_result_info(self) -> str:
        """Формирование детальной информации о результате последнего действия"""
        if not self.state_manager.action_history:
            return ""
        
        last_action = self.state_manager.action_history[-1]
        last_action_name = last_action.get("action", "unknown")
        last_action_params = last_action.get("parameters", {})
        last_action_result = last_action.get("result", {})
        last_action_success = last_action_result.get("success", False)
        page_changed = last_action.get("page_changed", False)
        dom_changed = last_action.get("dom_changed", False)
        new_elements = last_action.get("new_elements", {})
        url_before = last_action.get("url_before", "")
        url_after = last_action.get("url_after", "")
        title_before = last_action.get("title_before", "")
        title_after = last_action.get("title_after", "")
        
        error_suggestion = last_action_result.get("error_suggestion")
        error_strategy = last_action_result.get("error_strategy")
        validation_failed = last_action_result.get("validation_failed", False)
        validation_message = last_action_result.get("validation_message", "")
        validation_suggestions = last_action_result.get("validation_suggestions", [])
        
        last_action_desc = (
            last_action_params.get("description") or 
            last_action_params.get("element_description") or 
            last_action_params.get("url", "") or
            str(last_action_params)
        )
        
        # Определяем, нужно ли показывать предупреждение о page_changed=false
        # Для type_text и query_dom это нормально - страница не должна изменяться
        show_page_changed_warning = True
        if last_action_name == "type_text":
            show_page_changed_warning = False  # type_text только заполняет поле, страница не меняется
        elif last_action_name == "query_dom":
            show_page_changed_warning = False  # query_dom - информационный запрос, страница не меняется
        
        page_changed_text = 'Да' if page_changed else ('НЕТ - КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ!' if show_page_changed_warning else 'НЕТ (это нормально)')
        
        info = f"""
=== РЕЗУЛЬТАТ ПОСЛЕДНЕГО ДЕЙСТВИЯ ===
Действие: {last_action_name} ({last_action_desc})
Успешно: {'Да' if last_action_success else 'Нет'}
Страница изменилась: {page_changed_text}
"""
        
        # Добавляем специальную обработку результата query_dom
        if last_action_name == "query_dom":
            query_text = last_action_params.get("query", "")
            query_answer = last_action_result.get("answer", "") or last_action_result.get("message", "")
            extracted_selector = last_action_result.get("extracted_selector")
            
            selector_info = ""
            if extracted_selector:
                selector_info = f"""
=== ИЗВЛЕЧЕННЫЙ СЕЛЕКТОР: {extracted_selector} ===

Подумай: Зачем ты спрашивал про селектор? Чтобы использовать его в действии.
Теперь у тебя есть селектор - используй его. Это эффективнее чем искать элемент заново.

Пример мышления:
"Я получил селектор '{extracted_selector}' из query_dom. Мне нужно кликнуть по этому элементу. 
Вместо того чтобы искать элемент по описанию, использую селектор напрямую - это точнее и быстрее."

Использование: click_element(selector="{extracted_selector}")
"""
            
            info += f"""
=== РЕЗУЛЬТАТ query_dom ===
Вопрос: {query_text}
Ответ DOM SubAgent: {query_answer}
{selector_info}

ПОНИМАНИЕ: query_dom - это информационный запрос. Страница не изменилась после него - это нормально, он только получает информацию.

КАК ИСПОЛЬЗОВАТЬ РЕЗУЛЬТАТ:
Подумай: Зачем ты задавал этот вопрос? Чтобы получить информацию для следующего действия.
- Если получил селектор - используй его в следующем действии (это эффективнее поиска по описанию)
- Если получил описание визуального состояния - используй его для понимания функциональности элемента
- Если получил информацию о структуре - используй её для планирования
- Не делай лишних действий если уже получил нужную информацию

Пример мышления:
"Я спросил про селектор кнопки и получил его вместе с описанием визуального состояния. Теперь мне нужно кликнуть по этой кнопке. 
Использую полученный селектор - зачем искать элемент заново, если я уже знаю его селектор и понимаю что на нем отображается?"

Если селектор не был извлечен автоматически, найди его в ответе:
- Ответ содержит "Селектор: ..." → извлеки и используй
- Ответ описывает визуальное состояние → используй эту информацию для понимания функциональности
- Ответ описывает структуру → используй информацию для планирования

ПРИНЦИП ФОРМИРОВАНИЯ ВОПРОСОВ К DOM SUB-AGENT:
Не используй заготовленные шаблоны! Каждая ситуация уникальна. Подумай самостоятельно: что тебе нужно узнать для выполнения задачи? Сформулируй вопрос так, чтобы получить нужную информацию.

Процесс мышления при формировании вопроса:
1. Что мне нужно сделать? (понять цель действия)
2. Что мне нужно узнать для этого? (определить необходимую информацию)
3. Какой вопрос поможет получить эту информацию? (сформулировать вопрос самостоятельно)

Важно: Фокусируйся на конкретном элементе или результате, а не на всей странице. Указывай контекст (где находится элемент, после какого действия). ВСЕГДА проси селектор в ответе. Проси описание визуального состояния если это релевантно (что отображается, какие иконки, счетчики).

DOM Sub-agent даст детальный ответ с описанием визуального состояния элементов (текст, иконки, стрелки, счетчики) и селекторами - используй эту информацию для действий.
"""
        
        # Добавляем vision_analysis если есть (из результата take_screenshot)
        vision_analysis = last_action_result.get("vision_analysis")
        if vision_analysis:
            tokens_used = last_action_result.get("tokens_used")
            tokens_info = f" (использовано токенов: {tokens_used})" if tokens_used else ""
            info += f"""
=== VISION АНАЛИЗ СКРИНШОТА ===
Изображение было отправлено в Vision API и проанализировано{tokens_info}.

АНАЛИЗ:
{vision_analysis}

КАК ИСПОЛЬЗОВАТЬ ЭТУ ИНФОРМАЦИЮ:

Принцип: Vision API описывает элементы текстом (например, "Кнопка: Найти"). Этот текст можно использовать для поиска элемента в списке интерактивных элементов.

Процесс мышления:
1. Прочитай vision_analysis и найди описание нужного элемента
2. Извлеки текст элемента (например, из "Кнопка: Найти" → текст "Найти")
3. Найди элемент в списке интерактивных элементов по этому тексту
4. Если найден - используй его селектор напрямую (это эффективнее query_dom)
5. Если не найден - тогда query_dom поможет получить селектор

Пример:
vision_analysis: "Кнопка: Найти"
→ Найди в списке интерактивных элементов элемент с текстом "Найти"
→ Если найден - используй его селектор: click_element("Найти") или click_element(selector="...")
→ Если не найден - query_dom("Есть ли кнопка Найти? Селектор?")
"""
        
        if validation_failed:
            info += f"""
ВАЛИДАЦИЯ РЕЗУЛЬТАТА ДЕЙСТВИЯ ПРОВАЛЕНА:
{validation_message}

КРИТИЧЕСКИ ВАЖНО: Действие не привело к ожидаемому результату!
- Проверь, действительно ли действие сработало как ожидалось
- Соответствует ли результат тому, что требовалось в задаче?
- Если нет - попробуй другой подход или элемент

"""
            if validation_suggestions:
                info += f"Рекомендации по исправлению:\n"
                for suggestion in validation_suggestions:
                    info += f"   - {suggestion}\n"
        
        if page_changed:
            info += f"URL: {url_before} → {url_after}\n"
            
            if last_action_name == "navigate":
                info += f"""
КРИТИЧЕСКИ ВАЖНО: Последнее действие было navigate()!
СЛЕДУЮЩИЙ ШАГ ОБЯЗАТЕЛЬНО ДОЛЖЕН БЫТЬ:
1. take_screenshot(full_page=true) - автоматически анализируется через Vision API
2. Используй vision_analysis из результата take_screenshot для понимания структуры страницы
3. Если vision_analysis не помог найти нужный элемент - используй query_dom
4. ТОЛЬКО ПОСЛЕ этого планируй следующее действие (click_element, type_text и т.д.)

ПРАВИЛО: navigate() → take_screenshot() → используй vision_analysis → (query_dom если нужно) → действие
НЕ ПЛАНИРУЙ другие действия сразу после navigate() без take_screenshot()!

"""
            
            is_spa_navigation = url_before != url_after and title_after == title_before
            if is_spa_navigation:
                info += f"SPA НАВИГАЦИЯ: URL изменился без полной перезагрузки страницы. Это нормально для SPA - DOM меняется асинхронно.\n"
                info += f"   Если элементов пока нет - подожди 1-2 секунды для загрузки динамического контента, затем проверь контекст.\n"
            
            if title_after != title_before:
                info += f"Заголовок: '{title_before}' → '{title_after}'\n"
            if dom_changed:
                info += f"DOM изменился (появились новые элементы или изменилась структура страницы)\n"
            
            if new_elements.get("new_modals"):
                modals_info = new_elements.get("modals_info", [])
                info += f"ВАЖНО: Появилось модальное окно! Проверь элементы в модальном окне в списке доступных элементов.\n"
                if modals_info:
                    for modal in modals_info[:2]:
                        if modal.get("has_form"):
                            info += f"   - Модальное окно содержит форму с {modal.get('input_count', 0)} полями\n"
                        if modal.get("text_preview"):
                            preview = modal.get("text_preview", "")[:50]
                            info += f"   - Текст модального окна: '{preview}...'\n"
            
            if new_elements.get("new_forms"):
                info += f"ВАЖНО: Появилась форма! Ищи поля ввода в списке доступных элементов.\n"
            
            if new_elements.get("new_interactive_elements"):
                info += f"Появились новые интерактивные элементы на странице.\n"
        else:
            # Показываем предупреждение только если это не type_text или query_dom
            if show_page_changed_warning:
                ajax_hint = ""
                if last_action_name in ["click_element", "type_text"]:
                    ajax_hint = "\n   ВОЗМОЖНО: Контент загружается через AJAX. Подожди 1-2 секунды, затем проверь контекст - возможно появятся новые элементы без изменения URL."
                
                info += f"""
КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: Страница НЕ изменилась после действия '{last_action_name}'!
   URL остался: {url_before}
   Заголовок остался: '{title_before}'
   DOM не изменился (не появились новые элементы)
   
   Это означает, что действие либо не сработало, либо не привело к переходу.{ajax_hint}
   
ОБЯЗАТЕЛЬНЫЙ СЛЕДУЮЩИЙ ШАГ:
1. take_screenshot() - для визуального анализа почему действие не сработало
2. query_dom(КОНКРЕТНЫЙ вопрос про проблему или элемент) - НЕ про всю страницу!
   Примеры КОНКРЕТНЫХ вопросов:
   ПЛОХО: "Что на странице? Какие элементы видны?"
   ХОРОШО: "Видна ли кнопка '{last_action_desc}' на странице? Селектор? Есть ли сообщение об ошибке? Селектор сообщения?"
   ХОРОШО: "Перекрыта ли кнопка модальным окном? Селектор модального окна?"
3. ТОЛЬКО ПОСЛЕ этого планируй другой подход

ПРАВИЛО: page_changed = false → take_screenshot() → query_dom(конкретный вопрос) → другой подход (ОБЯЗАТЕЛЬНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ!)

   НЕ ПОВТОРЯЙ это действие - попробуй другой элемент или подход!
   Если это действие уже повторялось - это ЦИКЛ! Измени стратегию немедленно!
"""
        
        if not last_action_success:
            error_msg = last_action_result.get("error", "Unknown error")
            info += f"\nОшибка: {error_msg}\n"
            
            if error_suggestion:
                info += f"Рекомендация по исправлению: {error_suggestion}\n"
            if error_strategy:
                strategy_descriptions = {
                    "scroll": "Прокрути страницу",
                    "scroll_to_element": "Прокрути до элемента",
                    "wait": "Подожди загрузки",
                    "alternative": "Попробуй альтернативный подход",
                    "alternative_description": "Используй другое описание элемента",
                    "close_modals": "Закрой модальные окна",
                    "use_search": "Используй поисковую строку"
                }
                strategy_desc = strategy_descriptions.get(error_strategy, error_strategy)
                info += f"   Стратегия: {strategy_desc}\n"
        
        return info
    
    def _build_dynamic_content_hint(self) -> str:
        """Формирование подсказок о динамическом контенте"""
        if not self.state_manager.action_history:
            return ""
        
        last_action = self.state_manager.action_history[-1]
        last_action_name = last_action.get("action", "")
        
        if last_action_name == "scroll":
            return "\n\nНАПОМИНАНИЕ О LAZY LOADING:\nПосле прокрутки (scroll) контент может загружаться динамически. Проверь контекст страницы - появились ли новые элементы? Если нужный элемент не найден - прокрути дальше или попробуй другой подход."
        
        if last_action_name in ["navigate", "click_element"]:
            page_changed = last_action.get("page_changed", False)
            url_before = last_action.get("url_before", "")
            url_after = last_action.get("url_after", "")
            
            if url_before != url_after and page_changed:
                return "\n\nНАПОМИНАНИЕ О SPA:\nURL изменился (возможно SPA навигация). Если элементов пока мало или они не загрузились - подожди 1-2 секунды для загрузки динамического контента, затем проверь контекст снова."
        
        return ""
    
    def _build_user_message(self, context: str, task: str, use_template: bool = True) -> str:
        """
        Формирование user message для принятия решения
        
        Args:
            context: Контекст страницы
            task: Текущая задача
            use_template: Использовать ли специализированный шаблон для типа задачи (не используется)
            
        Returns:
            Отформатированное user message
        """
        visited_info = self._build_visited_urls_info()
        recent_actions_info, loop_warning = self._build_recent_actions_info()
        last_action_result_info = self._build_last_action_result_info()
        
        # Проверяем циклы навигации
        navigation_loop_warning = self._detect_navigation_loop()
        if navigation_loop_warning:
            loop_warning += f"\n\nКРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ О ЦИКЛЕ НАВИГАЦИИ:\n{navigation_loop_warning}\n"
        
        # Проверяем пустую страницу
        is_empty_page = "about:blank" in context or "интерактивных элементов: 0" in context or len(self.state_manager.action_history) == 0
        if is_empty_page:
            loop_warning += "\n\nКРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: СТРАНИЦА ПУСТАЯ (0 интерактивных элементов)!\n"
            loop_warning += "Это означает, что страница недоступна или требует авторизации.\n"
            loop_warning += "НЕМЕДЛЕННО вернись на предыдущую страницу через navigate (используй URL из истории посещенных страниц)!\n"
            loop_warning += "НЕ ПОВТОРЯЙ переход на эту пустую страницу!\n"
            loop_warning += "Работай с контентом на странице, где есть интерактивные элементы!\n"
        
        # Анализ задачи и планирование
        task_analysis = ""
        task_lower = task.lower()
        
        # Определяем, является ли задача многошаговой
        multi_step_keywords = ["и", "затем", "после", "потом", "сначала", "потом", "предварительно", "изучив"]
        has_multiple_actions = any(keyword in task_lower for keyword in multi_step_keywords) or \
                              task_lower.count(" ") > 5  # Длинные задачи обычно многошаговые
        
        if has_multiple_actions:
            task_analysis = "\n=== ПЛАНИРОВАНИЕ ЗАДАЧИ ===\n"
            task_analysis += "КРИТИЧЕСКИ ВАЖНО: Эта задача содержит несколько шагов! Ты ДОЛЖЕН сам проанализировать задачу и спланировать последовательность действий!\n\n"
            task_analysis += "ОБЯЗАТЕЛЬНО ПЕРЕД НАЧАЛОМ ВЫПОЛНЕНИЯ:\n"
            task_analysis += "1. ПРОАНАЛИЗИРУЙ задачу и разбей её на логические шаги:\n"
            task_analysis += "   - Прочитай задачу внимательно\n"
            task_analysis += "   - Определи что нужно сделать ПЕРВЫМ\n"
            task_analysis += "   - Определи что нужно сделать ВТОРЫМ\n"
            task_analysis += "   - Определи что нужно сделать ДАЛЬШЕ\n"
            task_analysis += "   - Определи конечную цель\n\n"
            task_analysis += "2. ОПРЕДЕЛИ текущий шаг на КАЖДОЙ итерации:\n"
            task_analysis += "   - Какой шаг задачи ты выполняешь СЕЙЧАС?\n"
            task_analysis += "   - Что уже сделано? (проверь историю действий)\n"
            task_analysis += "   - Что осталось сделать?\n\n"
            task_analysis += "3. ПЛАНИРУЙ конкретные действия для текущего шага:\n"
            task_analysis += "   - Какие элементы нужны для этого шага? (используй query_dom для поиска)\n"
            task_analysis += "   - Какие действия нужно выполнить?\n"
            task_analysis += "   - В каком порядке?\n\n"
            task_analysis += "ВАЖНО: Не действуй хаотично! Всегда знай:\n"
            task_analysis += "   - Какой шаг задачи ты выполняешь СЕЙЧАС\n"
            task_analysis += "   - Что уже сделано (проверь историю действий)\n"
            task_analysis += "   - Что нужно сделать ДАЛЬШЕ\n"
            task_analysis += "   - Какая конечная цель\n\n"
            task_analysis += "ПРАВИЛА ПЛАНИРОВАНИЯ:\n"
            task_analysis += "- Анализируй задачу САМОСТОЯТЕЛЬНО - не используй заготовки\n"
            task_analysis += "- Определяй элементы САМОСТОЯТЕЛЬНО через query_dom - не используй хардкод селекторов\n"
            task_analysis += "- Если действие не работает - пробуй ДРУГИЕ варианты, не повторяй то же самое\n"
            task_analysis += "- Отслеживай прогресс: что сделано, что осталось\n\n"
        
        dynamic_content_hint = self._build_dynamic_content_hint()
        
        # Добавляем информацию о предыдущих query_dom вопросах и извлеченных селекторах
        query_dom_history_info = ""
        recent_queries = self.state_manager.get_recent_query_dom_info(limit=5)
        if recent_queries:
            query_dom_history_info = "\n\n=== ПРЕДЫДУЩИЕ query_dom ВОПРОСЫ И ОТВЕТЫ ===\n"
            query_dom_history_info += "ВАЖНО: Эти вопросы уже были заданы! НЕ ПОВТОРЯЙ их! Используй ответы из истории!\n\n"
            extracted_selectors = []
            for i, q in enumerate(recent_queries, 1):
                query_text = q.get("query", "")[:150]
                answer_text = q.get("answer", "")[:200]
                query_dom_history_info += f"{i}. Вопрос: {query_text}\n"
                query_dom_history_info += f"   Ответ: {answer_text}\n"
                
                # Извлекаем селектор из ответа если есть
                selector = self.action_executor.extract_selector_from_answer(answer_text) if self.action_executor else None
                if selector:
                    extracted_selectors.append({
                        "query": query_text,
                        "selector": selector
                    })
                    query_dom_history_info += f"   ✅ ИЗВЛЕЧЕННЫЙ СЕЛЕКТОР: {selector}\n"
                query_dom_history_info += "\n"
            
            if extracted_selectors:
                query_dom_history_info += "=== ДОСТУПНЫЕ ИЗВЛЕЧЕННЫЕ СЕЛЕКТОРЫ ===\n"
                query_dom_history_info += "Эти селекторы уже были извлечены из предыдущих query_dom.\n\n"
                query_dom_history_info += "Принцип эффективности:\n"
                query_dom_history_info += "Если у тебя уже есть селектор элемента - используй его. Зачем искать элемент заново, если ты уже знаешь его селектор?\n\n"
                for sel_info in extracted_selectors:
                    query_dom_history_info += f"Селектор: {sel_info['selector']} (из вопроса: {sel_info['query'][:80]}...)\n"
                    query_dom_history_info += f"   Использование: click_element(selector=\"{sel_info['selector']}\") или type_text(\"текст\", selector=\"{sel_info['selector']}\")\n\n"
                query_dom_history_info += "Пример мышления:\n"
                query_dom_history_info += "\"Мне нужно кликнуть по элементу. Есть ли у меня селектор для него? Да, есть в списке выше. "
                query_dom_history_info += "Использую его напрямую - это эффективнее чем искать элемент по описанию или делать новый query_dom.\"\n\n"
            
            query_dom_history_info += "Принцип: Если информация уже есть в истории - используй её. Не задавай тот же вопрос снова - это неэффективно и может привести к циклу.\n\n"
            query_dom_history_info += "КРИТИЧЕСКИ ВАЖНО: query_dom - это ЕДИНСТВЕННЫЙ способ получить информацию о структуре страницы, элементах и их селекторах.\n"
            query_dom_history_info += "ВСЕГДА используй query_dom когда нужна информация о странице - перед кликами, перед вводом текста, для понимания структуры.\n"
            query_dom_history_info += "DOM Sub-agent дает детальные ответы с описанием визуального состояния элементов и селекторами - используй эту информацию для действий.\n"
        
        # Формируем итоговое сообщение
        base_message = f"""=== ТЕКУЩАЯ ЗАДАЧА ===
{task}{task_analysis}

=== КОНТЕКСТ СТРАНИЦЫ ===
{context}

=== ИСТОРИЯ ДЕЙСТВИЙ ===
Всего выполнено действий: {len(self.state_manager.action_history)}{visited_info}{recent_actions_info}{loop_warning}{last_action_result_info}{dynamic_content_hint}{query_dom_history_info}"""
        
        requirements_status = self.context_manager.get_requirements_status()
        if requirements_status:
            base_message += f"\n\n=== ПРОГРЕСС ПО ТРЕБОВАНИЯМ ===\n{requirements_status}"
        
        validation_reminder = """
=== ПРИНЦИПЫ ВАЛИДАЦИИ РЕЗУЛЬТАТОВ ===
Подумай после каждого действия: достиг ли ты ожидаемого результата?

Вопросы для самопроверки:
- Действие привело к ожидаемому результату?
- Соответствует ли результат цели задачи?
- Если ожидалось изменение (форма, переход, элемент) - произошло ли оно?

Если результат не соответствует ожиданиям - не повторяй то же действие. Подумай почему и попробуй другой подход."""

        query_dom_reminder = """
=== КОГДА ИСПОЛЬЗОВАТЬ query_dom ===

Подумай: Нужна ли тебе информация о структуре страницы или селекторе элемента?

Используй query_dom когда:
- Тебе нужен селектор элемента для точного взаимодействия
- Ты не уверен в структуре страницы после действия
- Нужно проверить результат действия

Принцип: query_dom получает информацию, не изменяет страницу. Используй полученную информацию для следующего действия.

Пример мышления:
"Я кликнул по кнопке. Что должно было произойти? Если должна была открыться форма - проверю появилась ли она. 
Если не уверен в структуре - query_dom поможет понять что изменилось."
"""
        
        base_message += validation_reminder
        base_message += query_dom_reminder

        return base_message
    
    async def _decide_action(self, context: str, task: str, page_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Принятие решения о следующем действии через OpenAI
        
        Args:
            context: Контекст страницы
            task: Текущая задача
            page_info: Информация о странице (для пересоздания контекста при оптимизации)
            
        Returns:
            Решение о действии
        """
        # Используем sub-агента для получения рекомендации (опционально)
        if ENABLE_SUB_AGENTS:
            try:
                recommendation = await self.sub_agent_manager.get_recommendation(context, task)
                if recommendation.get("success"):
                    agent_name = recommendation.get('agent', 'SubAgent')
                    analysis = recommendation.get('analysis', '')
                    # Добавляем рекомендацию в контекст с форматированием
                    context += f"\n\n=== РЕКОМЕНДАЦИЯ ОТ {agent_name} ===\n{analysis}\n\nУчти эту рекомендацию при принятии решения."
            except Exception as e:
                # Если ошибка при получении рекомендации - продолжаем без неё
                self.logger.warning(f"Не удалось получить рекомендацию от sub-агента: {e}")
        
        # Формируем промпт для принятия решения
        system_prompt = self.context_manager.get_system_prompt()
        user_message = self._build_user_message(context, task)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # Проверяем размер запроса перед отправкой
        from config import MAX_REQUEST_TOKENS
        request_size = self.context_manager.estimate_request_size(
            system_prompt, 
            user_message, 
            self.action_tools
        )
        
        # Если запрос слишком большой - уменьшаем контекст
        if request_size > MAX_REQUEST_TOKENS:
            self.logger.warning(f"⚠️  Запрос слишком большой ({request_size} токенов), оптимизирую...")
            
            # Вычисляем сколько токенов нужно освободить
            tools_tokens = self.context_manager.estimate_request_size("", "", self.action_tools)
            system_tokens = self.context_manager.token_optimizer.count_tokens(system_prompt)
            available_for_context = MAX_REQUEST_TOKENS - tools_tokens - system_tokens - 500  # Запас 500 токенов
            
            if available_for_context > 0:
                # Пересоздаем контекст с ограничением
                if page_info is None:
                    page_info = await self.page_extractor.extract_page_info()
                context = self.context_manager.prepare_context(page_info, max_tokens=available_for_context)
                user_message = self._build_user_message(context, task)
                messages[1]["content"] = user_message
                
                # Проверяем еще раз
                request_size = self.context_manager.estimate_request_size(
                    system_prompt, 
                    user_message, 
                    self.action_tools
                )
                self.logger.info(f"📊 Размер запроса после оптимизации: {request_size} токенов")
            else:
                self.logger.error(f"❌ Невозможно уместить запрос даже после оптимизации. Tools: {tools_tokens}, System: {system_tokens}")
                return {
                    "success": False,
                    "error": f"Запрос слишком большой ({request_size} токенов). Tools definitions занимают {tools_tokens} токенов."
                }
        
        try:
            # Используем больший лимит токенов для получения полного summary при завершении задачи
            # Обычно достаточно 500, но для task_complete может потребоваться больше
            max_response_tokens = 1500
            
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                tools=self.action_tools,
                tool_choice="required",  # ОБЯЗАТЕЛЬНО использовать функции, нельзя возвращать текст
                temperature=0.3,
                max_tokens=max_response_tokens
            )
            
            message = response.choices[0].message
            
            # КРИТИЧНО: Агент ОБЯЗАН использовать только функции для действий
            # Удалена проверка текста на завершение задачи - теперь только через task_complete
            
            # Проверка на вызов функции
            if message.tool_calls:
                tool_call = message.tool_calls[0]
                function_name = tool_call.function.name
                try:
                    args_str = tool_call.function.arguments
                    function_args = json.loads(args_str)
                except json.JSONDecodeError as e:
                    self.logger.warning(f"Ошибка парсинга аргументов функции: {e}")
                    # Улучшенная логика исправления JSON
                    try:
                        fixed_str = args_str.strip()
                        
                        # Стратегия 1: Если JSON обрывается - пытаемся закрыть незакрытые структуры
                        if not fixed_str.endswith('}') and not fixed_str.endswith(']'):
                            # Подсчитываем открывающие и закрывающие скобки
                            open_braces = fixed_str.count('{')
                            close_braces = fixed_str.count('}')
                            open_brackets = fixed_str.count('[')
                            close_brackets = fixed_str.count(']')
                            
                            # Добавляем недостающие закрывающие скобки
                            missing_braces = open_braces - close_braces
                            missing_brackets = open_brackets - close_brackets
                            
                            fixed_str += '}' * missing_braces
                            fixed_str += ']' * missing_brackets
                        
                        # Стратегия 2: Исправляем незакрытые строки
                        if '"' in fixed_str:
                            # Проверяем, все ли строки закрыты
                            quote_count = fixed_str.count('"')
                            if quote_count % 2 != 0:
                                # Нечетное количество кавычек - незакрытая строка
                                # Находим последнюю незакрытую кавычку
                                last_quote_idx = fixed_str.rfind('"')
                                if last_quote_idx > 0:
                                    # Проверяем экранирование
                                    before_quote = fixed_str[:last_quote_idx]
                                    escape_count = len(before_quote) - len(before_quote.rstrip('\\'))
                                    if escape_count % 2 == 0:  # Кавычка не экранирована
                                        # Добавляем закрывающую кавычку перед закрывающей скобкой
                                        if not fixed_str.endswith('}'):
                                            fixed_str += '"}'
                                        else:
                                            # Вставляем кавычку перед последней скобкой
                                            fixed_str = fixed_str[:-1] + '"' + fixed_str[-1]
                        
                        function_args = json.loads(fixed_str)
                        self.logger.info(f"JSON успешно исправлен и распарсен")
                    except json.JSONDecodeError as e2:
                        # Если не удалось исправить - пробуем извлечь хотя бы часть параметров
                        self.logger.warning(f"Не удалось исправить JSON полностью: {e2}")
                        function_args = {}
                        # Пробуем извлечь хотя бы простые параметры через регулярные выражения
                        # Ищем простые пары ключ-значение
                        simple_params = re.findall(r'"(\w+)":\s*"([^"]*)"', args_str)
                        for key, value in simple_params:
                            function_args[key] = value
                        if function_args:
                            self.logger.info(f"Извлечены частичные параметры: {list(function_args.keys())}")
                    except Exception as e2:
                        # Если все попытки не удались - используем пустой словарь
                        function_args = {}
                        self.logger.error(f"Не удалось исправить JSON, используем пустые аргументы: {e2}")
                except Exception as e:
                    self.logger.error(f"Неожиданная ошибка при парсинге аргументов: {e}")
                    function_args = {}
                
                return {
                    "success": True,
                    "action": function_name,
                    "parameters": function_args
                }
            
            # Если нет вызова функции, но есть текст ответа
            # С tool_choice="required" это не должно происходить, но обрабатываем на всякий случай
            # Агент должен действовать самостоятельно, не спрашивать пользователя
            # Если модель не вызвала функцию - это критическая ошибка
            if message.content:
                # Логируем полный текст для диагностики (не только первые 100 символов)
                full_content = message.content
                content_preview = full_content[:500] + ("..." if len(full_content) > 500 else "")
                self.logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: Модель вернула текст вместо вызова функции (tool_choice='required' не сработал)")
                self.logger.error(f"Полный текст ответа модели ({len(full_content)} символов): {content_preview}")
                if len(full_content) > 500:
                    self.logger.error(f"... (пропущено {len(full_content) - 500} символов)")
                return {
                    "success": False,
                    "error": f"Модель не вызвала функцию, хотя это обязательно. Получен текст: {content_preview}"
                }
            
            return {
                "success": False,
                "error": "Не удалось определить действие"
            }
        
        except Exception as e:
            return {
                "success": False,
                "error": f"Ошибка при принятии решения: {str(e)}"
            }
    
    async def _wait_for_dynamic_content(self, action_name: str, action_result: Dict[str, Any], max_wait: float = 3.0) -> None:
        """
        Адаптивное ожидание загрузки динамического контента
        
        Args:
            action_name: Название действия
            action_result: Результат действия
            max_wait: Максимальное время ожидания в секундах
        """
        # Базовое ожидание зависит от типа действия
        base_delay = {
            "navigate": 2.0,
            "click_element": 1.5,
            "type_text": 0.5,
            "scroll": 1.0,
            "search_on_page": 2.0,
            "reload_page": 2.0
        }.get(action_name, 1.0)
        
        # Если действие завершилось ошибкой - минимальное ожидание
        if not action_result.get("success", True):
            await asyncio.sleep(0.5)
            return
        
        # Ждем базовую задержку
        await asyncio.sleep(base_delay)
        
        # Проверяем, загрузился ли контент (network idle или появление элементов)
        try:
            # Пытаемся дождаться network idle (если доступно)
            try:
                await self.browser.page.wait_for_load_state("networkidle", timeout=1000)
                return  # Если network idle достигнут - выходим
            except:
                pass  # Если не удалось - продолжаем адаптивное ожидание
            
            # Проверяем изменения страницы с адаптивным увеличением задержки
            for attempt in range(3):
                page_state = await self.page_extractor.get_page_state_hash()
                interactive_count = page_state.get('interactive_count', 0)
                modal_count = page_state.get('visible_modal_count', 0)
                
                # Если есть интерактивные элементы или модальные окна - контент загрузился
                if interactive_count > 0 or modal_count > 0:
                    return
                
                # Если это navigate и нет элементов - увеличиваем ожидание
                if action_name == "navigate" and attempt < 2:
                    await asyncio.sleep(1.0)
                else:
                    break
        except Exception:
            # В случае ошибки просто ждем базовую задержку
            pass
    
    async def _execute_action(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """
        Выполнение действия с проверкой безопасности
        
        Args:
            decision: Решение о действии
            
        Returns:
            Результат выполнения действия
        """
        action_name = decision.get("action")
        parameters = decision.get("parameters", {})
        
        # Проверка безопасности
        security_check = await self.security_layer.check_action(action_name, parameters)
        
        if not security_check.get("allowed"):
            return {
                "success": False,
                "error": "Действие отклонено пользователем",
                "requires_confirmation": True
            }
        
        # Выполнение действия
        result = await self.action_executor.execute_action(action_name, parameters)
        
        # Логирование деструктивных действий
        if security_check.get("requires_confirmation"):
            self.security_layer.log_action(action_name, parameters, result)
        
        return result
    
    def get_state(self) -> Dict[str, Any]:
        """Получение текущего состояния агента"""
        return self.state_manager.get_state_info()

