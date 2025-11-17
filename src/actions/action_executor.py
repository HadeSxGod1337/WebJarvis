"""Выполнение действий агента"""
from typing import Dict, Any, Optional
import asyncio
from urllib.parse import urlparse
from src.browser.controller import BrowserController
from src.browser.page_extractor import PageExtractor
from src.actions.action_validator import ActionValidator
from src.browser.screenshot_manager import ScreenshotManager


class ActionExecutor:
    """Исполнитель действий агента"""
    
    def __init__(
        self, 
        browser_controller: BrowserController, 
        page_extractor: PageExtractor,
        logger=None,
        sub_agent_manager=None,
        state_manager=None
    ):
        """
        Инициализация исполнителя действий
        
        Args:
            browser_controller: Контроллер браузера
            page_extractor: Экстрактор информации о странице
            logger: Логгер для вывода информации
            sub_agent_manager: Менеджер sub-агентов для query_dom (опционально)
        """
        self.browser = browser_controller
        self.extractor = page_extractor
        self.validator = ActionValidator()
        self.logger = logger
        self.sub_agent_manager = sub_agent_manager
        self.state_manager = state_manager
        self.screenshot_manager = ScreenshotManager()
        self.current_task: Optional[str] = None  # Текущая задача для анализа модальных окон
    
    def set_task(self, task: str):
        """Установка текущей задачи для анализа модальных окон"""
        self.current_task = task
    
    async def execute_action(self, action_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Выполнение действия
        
        Args:
            action_name: Название действия
            parameters: Параметры действия
            
        Returns:
            Результат выполнения действия
        """
        # Валидация действия
        is_valid, error_msg = self.validator.validate_action(action_name, parameters)
        if not is_valid:
            return {
                "success": False,
                "error": error_msg
            }
        
        # Выполнение действия
        try:
            if action_name == "click_element":
                return await self._click_element(parameters)
            elif action_name == "type_text":
                return await self._type_text(parameters)
            elif action_name == "navigate":
                return await self._navigate(parameters)
            elif action_name == "scroll":
                return await self._scroll(parameters)
            elif action_name == "wait_for_element":
                return await self._wait_for_element(parameters)
            elif action_name == "extract_text":
                return await self._extract_text(parameters)
            elif action_name == "take_screenshot":
                return await self._take_screenshot(parameters)
            elif action_name == "query_dom":
                return await self._query_dom(parameters)
            elif action_name == "search_on_page":
                return await self._search_on_page(parameters)
            elif action_name == "reload_page":
                return await self._reload_page(parameters)
            elif action_name == "task_complete":
                return {"success": True, "message": parameters.get("summary", "Задача выполнена")}
            elif action_name == "ask_user":
                # ask_user больше не поддерживается - агент должен действовать самостоятельно
                # Но обрабатываем на случай, если модель все же попытается вызвать
                return {
                    "success": False,
                    "error": "ask_user больше не поддерживается. Агент должен действовать самостоятельно на основе контекста."
                }
            else:
                return {
                    "success": False,
                    "error": f"Неизвестное действие: {action_name}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    @staticmethod
    def _is_clickable_element(element: Dict[str, Any]) -> bool:
        if not element:
            return False
        elem_type = (element.get("type") or "").lower()
        tag = (element.get("tag") or "").lower()
        role = (element.get("role") or "").lower()
        return (
            elem_type in {"button", "link", "a"} or
            tag in {"button", "a", "label"} or
            role in {"button", "link", "menuitem"}
        )

    @staticmethod
    def _description_suggests_search(description: str) -> bool:
        if not description:
            return False
        lowered = description.lower()
        return any(keyword in lowered for keyword in ["поиск", "search", "найти", "query", "строка поиска"])

    @staticmethod
    def _is_text_input_element(element: Optional[Dict[str, Any]]) -> bool:
        if not element:
            return False
        elem_type = (element.get("type") or "").lower()
        tag = (element.get("tag") or "").lower()
        role = (element.get("role") or "").lower()
        contenteditable = str(element.get("contenteditable", "")).lower()
        if elem_type in {"input", "textarea", "search_input"}:
            return True
        if tag in {"input", "textarea"}:
            return True
        if role in {"textbox", "searchbox", "combobox"}:
            return True
        if contenteditable == "true":
            return True
        return False

    async def _click_element(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Клик по элементу"""
        selector = parameters.get("selector")
        description = parameters.get("description", "")
        
        # Диагностическое логирование: проверяем наличие извлеченных селекторов
        if self.logger:
            self.logger.debug(f"🔍 Диагностика click_element: selector={selector}, description='{description}'")
        
        # Если селектор не указан, пытаемся найти извлеченный селектор из последних действий
        if not selector and hasattr(self, 'state_manager') and self.state_manager:
            # Сначала проверяем extracted_selector из результата последнего действия
            if self.state_manager.action_history:
                last_action = self.state_manager.action_history[-1]
                if last_action.get("action") == "query_dom":
                    last_result = last_action.get("result", {})
                    # Проверяем есть ли extracted_selector в результате
                    extracted_from_result = last_result.get("extracted_selector")
                    if extracted_from_result:
                        selector = extracted_from_result
                        if self.logger:
                            self.logger.info(f"   ✅ Использован extracted_selector из результата последнего query_dom: {selector}")
                    elif self.logger:
                        self.logger.debug(f"   ℹ️  Последнее действие было query_dom, но extracted_selector не найден в результате")
            
            # Если не нашли в результате последнего действия, проверяем query_dom_history
            if not selector:
                recent_queries = self.state_manager.get_recent_query_dom_info(limit=3)
                if self.logger and recent_queries:
                    self.logger.debug(f"   ℹ️  Проверяю {len(recent_queries)} последних query_dom запросов для поиска селектора")
                
                for query_info in reversed(recent_queries):  # Проверяем от последнего к первому
                    answer = query_info.get("answer", "")
                    if answer:
                        # Извлекаем селектор из ответа query_dom
                        extracted_selector = self.extract_selector_from_answer(answer)
                        if extracted_selector:
                            selector = extracted_selector
                            query_text = query_info.get("query", "")[:50]
                            if self.logger:
                                self.logger.info(f"   ✅ Использован извлеченный селектор из query_dom истории: {selector}")
                                self.logger.debug(f"      Источник: вопрос '{query_text}...'")
                            break
                        elif self.logger:
                            self.logger.debug(f"   ℹ️  Не удалось извлечь селектор из ответа query_dom")
                else:
                    if self.logger:
                        self.logger.debug(f"   ℹ️  Не найдено извлеченных селекторов в последних {len(recent_queries)} query_dom запросах")
        
        # Валидация: проверяем что есть либо селектор, либо описание
        if not selector and not description:
            error_msg = "Не указан ни селектор, ни описание элемента для клика"
            if self.logger:
                self.logger.error(f"   ❌ {error_msg}")
                # Диагностическая информация
                if hasattr(self, 'state_manager') and self.state_manager:
                    if self.state_manager.action_history:
                        last_action = self.state_manager.action_history[-1]
                        if last_action.get("action") == "query_dom":
                            self.logger.error(f"   💡 Подсказка: Последнее действие было query_dom, но селектор не был извлечен или передан")
            return {
                "success": False,
                "error": error_msg
            }
        
        # Логируем информацию о клике
        if self.logger:
            if selector:
                self.logger.info(f"🖱️  Клик по элементу (селектор: '{selector}')")
                if description:
                    self.logger.info(f"   Описание: '{description}'")
            else:
                self.logger.info(f"🖱️  Клик по элементу: '{description}'")
                self.logger.warning(f"   ⚠️  ВНИМАНИЕ: Селектор не указан, будет выполнен поиск по описанию '{description}'")
                # Диагностическая информация
                if hasattr(self, 'state_manager') and self.state_manager:
                    recent_queries = self.state_manager.get_recent_query_dom_info(limit=1)
                    if recent_queries:
                        self.logger.debug(f"   💡 Подсказка: Есть последний query_dom, но селектор не был извлечен автоматически")
        
        element = None
        if not selector:
            if self.logger:
                self.logger.info("   Поиск элемента по описанию...")
            element = await self.extractor.find_element_by_description(description)
            if element:
                selector = element.get("selector")
                element_text = element.get("text", "")
                element_type = element.get("type", "")
                if self.logger:
                    self.logger.info(f"   Найден селектор: {selector}")
                    self.logger.info(f"   Тип элемента: {element_type}, текст: '{element_text[:50]}...'")
            else:
                if self.logger:
                    self.logger.error(f"   Элемент '{description}' не найден на странице после всех стратегий поиска")
                return {
                    "success": False,
                    "error": f"Элемент '{description}' не найден на странице"
                }
        
        if not selector:
            return {
                "success": False,
                "error": f"Не удалось найти селектор для элемента '{description}'"
            }

        # Если элемент не найден через описание, но есть селектор - пытаемся найти элемент в page_info
        if not element and selector:
            try:
                page_info = await self.extractor.extract_page_info(include_text=False, use_cache=True)
                elements = page_info.get("interactive_elements", [])
                for elem in elements:
                    if elem.get("selector") == selector:
                        element = elem
                        if self.logger:
                            self.logger.debug(f"   Найден элемент в page_info по селектору: {elem.get('type', 'unknown')}")
                        break
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"   Не удалось найти элемент в page_info: {e}")
        
        # Универсальная проверка: если селектор указывает на контейнер (list_item, card, item и т.д.)
        # - проверяем кликабельность контейнера и ищем кликабельный элемент внутри
        # Это работает для любых ситуаций без хардкода
        is_container = False
        if element:
            is_container = element.get("type") == "list_item"
        elif selector:
            # Проверяем по селектору - если содержит типичные паттерны контейнеров
            # Но не ограничиваемся только list_item - проверяем любые контейнеры
            container_patterns = ['listitem', 'list-item', 'messageitem', 'message-item', 'card', 'item', 'row', 'cell']
            selector_lower = selector.lower()
            is_container = any(pattern in selector_lower for pattern in container_patterns)
        
        if is_container:
            # ВАЖНО: Для контейнеров (list_item, card и т.д.) приоритет - использовать сам контейнер,
            # если он кликабелен через делегирование событий. Это более надежно чем искать потомков.
            
            # Сначала проверяем кликабельность самого контейнера через find_clickable_descendant
            # (он проверяет cursor: pointer и обработчики событий)
            if self.logger:
                self.logger.info(f"   Селектор указывает на контейнер, проверяем кликабельность контейнера и ищем оптимальный элемент для клика")
            
            try:
                clickable_selector = await self.extractor.find_clickable_descendant(selector)
                if clickable_selector:
                    # Нормализуем селекторы для сравнения (убираем пробелы, приводим к нижнему регистру)
                    normalized_original = selector.lower().strip()
                    normalized_found = clickable_selector.lower().strip()
                    
                    # Проверяем - это сам контейнер или потомок?
                    # Сравниваем по классам и тегам, а не по точному совпадению строк
                    is_same_element = (
                        normalized_original == normalized_found or
                        normalized_original in normalized_found or
                        normalized_found in normalized_original
                    )
                    
                    # Дополнительная проверка: если найденный селектор содержит классы из оригинального
                    # (например, MessageListItem__root в обоих), то это скорее всего тот же элемент
                    original_classes = set([c for c in normalized_original.split('.') if c and not c.startswith('#')])
                    found_classes = set([c for c in normalized_found.split('.') if c and not c.startswith('#')])
                    if original_classes and found_classes:
                        common_classes = original_classes.intersection(found_classes)
                        # Если есть общие классы и они составляют значительную часть - это тот же элемент
                        if common_classes and len(common_classes) >= min(2, len(original_classes)):
                            is_same_element = True
                    
                    if is_same_element:
                        # Это сам контейнер - используем его (клик обрабатывается через делегирование)
                        if self.logger:
                            self.logger.info(f"   ✅ Контейнер кликабелен через делегирование событий, используем его: {selector}")
                        # selector уже правильный, не меняем
                    else:
                        # Это потомок - проверяем что это не avatar/icon
                        # Исключаем элементы с подозрительными классами
                        exclude_keywords = ['avatar', 'icon', 'image', 'img', 'thumbnail', 'badge']
                        should_exclude = any(keyword in normalized_found for keyword in exclude_keywords)
                        
                        if should_exclude:
                            # Найденный элемент - это avatar/icon, используем сам контейнер
                            if self.logger:
                                self.logger.warning(f"   ⚠️  Найденный элемент похож на avatar/icon ({clickable_selector}), используем контейнер: {selector}")
                            # selector уже правильный, не меняем
                        else:
                            # Это нормальный потомок - используем его
                            if self.logger:
                                self.logger.info(f"   ✅ Найден кликабельный потомок: {clickable_selector}")
                            selector = clickable_selector
                else:
                    # Если кликабельный элемент не найден, используем сам контейнер
                    # (возможно клик обрабатывается через делегирование событий на уровне выше)
                    if self.logger:
                        self.logger.info(f"   ℹ️  Кликабельный элемент не найден, используем контейнер (возможно клик обрабатывается через делегирование событий)")
                    # selector уже правильный, не меняем
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"   Не удалось найти кликабельный элемент: {e}")
                # В случае ошибки используем сам контейнер
                if self.logger:
                    self.logger.info(f"   ℹ️  Используем контейнер напрямую: {selector}")
        
        # Если найденный элемент оказался не кликабельным (например span в кнопке) – ищем кликабельного предка
        if element and not self._is_clickable_element(element):
            try:
                ancestor_selector = await self.extractor.find_clickable_ancestor(selector)
                if ancestor_selector:
                    if self.logger:
                        self.logger.info(f"   Используем кликабельный родитель: {ancestor_selector}")
                    selector = ancestor_selector
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Не удалось получить кликабельный родитель: {e}")
        
        element_in_modal = element.get("in_modal", False) if element else False
        
        # Проверяем и обрабатываем модальные окна перед кликом
        try:
            modal_check = await self._check_and_handle_modals(
                target_element_in_modal=element_in_modal,
                task=self.current_task
            )
            if modal_check.get("found") and self.logger:
                modals_count = len(modal_check.get("modals", []))
                if element_in_modal:
                    self.logger.info(f"   ℹ️  Обнаружено {modals_count} модальных окон, целевой элемент находится внутри модального окна")
                elif modal_check.get("handled"):
                    self.logger.info(f"   ✓ Обработано {modals_count} модальных окон перед кликом")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"   Не удалось проверить модальные окна: {e}")
        
        # Закрываем только overlay и баннеры перед кликом, но НЕ закрываем модальные окна с формами
        # (они могут быть нужны для работы)
        try:
            await self._close_overlays_and_banners()
        except Exception as e:
            if self.logger:
                self.logger.warning(f"   Не удалось закрыть overlay/баннеры: {e}")
        
        # Прокручиваем до элемента перед кликом (если нужно)
        # Примечание: Playwright автоматически прокручивает до элемента при клике через locator.click(),
        # но явная прокрутка может помочь для сложных случаев (например, динамический контент, lazy loading)
        try:
            scroll_result = await self._scroll_to_element(selector)
            if not scroll_result.get("success") and self.logger:
                self.logger.debug(f"   Не удалось прокрутить до элемента явно, Playwright попробует автоматически при клике")
        except Exception as e:
            if self.logger:
                self.logger.debug(f"   Ошибка при прокрутке: {e}")
        
        # Проверяем видимость элемента перед кликом
        # Примечание: Playwright автоматически ждет видимости элемента при клике через locator.click(),
        # но проверка помогает для диагностики и понимания состояния элемента
        visibility_check = await self.browser.check_element_visibility(selector)
        if not visibility_check.get("visible", True) and self.logger:
            self.logger.debug(f"   Элемент может быть невидим сейчас, но Playwright попробует дождаться видимости при клике")
        
        # Пробуем клик с обработкой перехватывающих элементов
        # Используем сохраненный search_text если есть, иначе description
        search_text = parameters.get("_search_text", description)
        
        # Сохраняем состояние страницы ПЕРЕД кликом для проверки результата
        page_state_before = None
        try:
            page_state_before = await self.extractor.get_page_state_hash()
        except Exception:
            pass
        
        result = await self._click_with_retry(selector, search_text)
        if result.get("success"):
            result["message"] = f"Клик по элементу '{description}' выполнен"
            if self.logger:
                self.logger.success(f"   ✓ Клик выполнен успешно")
            
            # КРИТИЧЕСКИ ВАЖНО: После успешного клика проверяем результат действия
            # Ждем немного для загрузки динамических элементов
            await asyncio.sleep(1.5)  # Увеличена задержка для загрузки модальных окон
            
            # Проверяем результат действия (изменение страницы, появление модальных окон и форм)
            verification_result = await self._verify_action_result(
                action_type="click",
                description=description,
                page_state_before=page_state_before
            )
            
            # Добавляем информацию о проверке результата
            result.update(verification_result)
            
            # Если клик выполнен успешно, но страница не изменилась - пробуем Mouse API fallback
            # Это может помочь для сайтов, которые обрабатывают клики через JavaScript обработчики событий
            if not verification_result.get("action_verified", True):
                if self.logger:
                    self.logger.info(f"   🔄 Клик выполнен, но страница не изменилась, пробуем через Mouse API (fallback)")
                
                # Пробуем клик через Mouse API
                mouse_result = await self.browser.click_with_mouse_events(selector, timeout=10000)
                if mouse_result.get("success"):
                    if self.logger:
                        self.logger.info(f"   ✅ Клик через Mouse API выполнен успешно")
                    
                    # Ждем немного для загрузки динамических элементов после Mouse API клика
                    await asyncio.sleep(1.5)
                    
                    # Проверяем результат действия снова
                    verification_result_after_mouse = await self._verify_action_result(
                        action_type="click",
                        description=description,
                        page_state_before=page_state_before
                    )
                    
                    # Обновляем результат с информацией о Mouse API fallback
                    result.update(verification_result_after_mouse)
                    result["used_mouse_fallback"] = True
                    result["message"] = f"Клик по элементу '{description}' выполнен через Mouse API (fallback)"
        
        return result
    
    async def _close_overlays_and_banners(self) -> bool:
        """
        Закрытие overlay элементов и баннеров, но НЕ модальных окон с формами
        
        ВАЖНО: Не закрываем модальные окна с формами - они могут быть нужны для работы агента
        """
        try:
            page = self.browser.page
            if not page:
                return False
            
            # Закрываем только overlay и баннеры через JavaScript
            closed = await page.evaluate("""
            () => {
                let closed = false;
                
                // Закрываем ТОЛЬКО overlay элементы (не модальные окна с формами)
                const overlays = document.querySelectorAll('[data-qa="modal-overlay"], .modal-overlay');
                for (const overlay of overlays) {
                    try {
                        // Проверяем, не является ли это модальным окном с формой
                        const hasForm = overlay.querySelector('form') !== null;
                        const hasInputs = overlay.querySelectorAll('input, textarea, select').length > 0;
                        
                        // Закрываем только если это НЕ форма
                        if (!hasForm && !hasInputs) {
                            // Ищем кнопку закрытия
                            const closeBtn = overlay.querySelector('[aria-label*="закрыть" i], [aria-label*="close" i], button[class*="close"], .close-button');
                            if (closeBtn) {
                                closeBtn.click();
                                closed = true;
                            } else {
                                // Если нет кнопки - скрываем overlay
                                overlay.style.display = 'none';
                                closed = true;
                            }
                        }
                    } catch (e) {}
                }
                
                // Закрываем баннеры и уведомления (они всегда мешают)
                const banners = document.querySelectorAll('[data-qa*="banner"], .banner, [class*="banner"], [class*="notification"], [class*="toast"]');
                for (const banner of banners) {
                    try {
                        const closeBtn = banner.querySelector('button[aria-label*="закрыть" i], button[aria-label*="close" i], .close, button[class*="close"]');
                        if (closeBtn) {
                            closeBtn.click();
                            closed = true;
                        }
                    } catch (e) {}
                }
                
                return closed;
            }
            """)
            
            if closed:
                await asyncio.sleep(0.5)  # Ждем закрытия
            return bool(closed)
        except Exception:
            return False
    
    async def _close_modals(self) -> bool:
        """
        Закрытие ВСЕХ модальных окон (используется только при ошибках перехвата)
        
        ВАЖНО: Используется только в критических случаях, когда элемент перехватывается
        """
        try:
            page = self.browser.page
            if not page:
                return False
            
            # Закрываем модальные окна через JavaScript
            closed = await page.evaluate("""
            () => {
                let closed = false;
                
                // Закрываем модальные окна по data-qa="modal-overlay"
                const modals = document.querySelectorAll('[data-qa="modal-overlay"], .modal-overlay, [role="dialog"]');
                for (const modal of modals) {
                    try {
                        // Ищем кнопку закрытия внутри модального окна
                        const closeBtn = modal.querySelector('[aria-label*="закрыть" i], [aria-label*="close" i], button[class*="close"], .close-button');
                        if (closeBtn) {
                            closeBtn.click();
                            closed = true;
                        } else {
                            // Если нет кнопки - закрываем по ESC или клику вне окна
                            modal.style.display = 'none';
                            closed = true;
                        }
                    } catch (e) {}
                }
                
                return closed;
            }
            """)
            
            if closed:
                await asyncio.sleep(0.5)  # Ждем закрытия
            return bool(closed)
        except Exception:
            return False
    
    async def close_modals(self) -> bool:
        """Публичный доступ к закрытию модальных окон для восстановительных сценариев."""
        return await self._close_modals()
    
    async def _check_and_handle_modals(self, target_element_in_modal: bool = False, task: Optional[str] = None) -> Dict[str, Any]:
        """
        Проверка открытых модальных окон (упрощенная версия - без автоматического закрытия)
        
        Args:
            target_element_in_modal: True если целевой элемент находится в модальном окне
            task: Текущая задача (опционально)
            
        Returns:
            Словарь с информацией о найденных модальных окнах
        """
        try:
            page = self.browser.page
            if not page:
                return {"found": False, "modals": []}
            
            # Получаем информацию о видимых модальных окнах
            modals_info = await self.extractor.get_visible_modals_info()
            
            if not modals_info:
                return {"found": False, "modals": []}
            
            result = {
                "found": True,
                "modals": modals_info,
                "handled": False
            }
            
            # Просто возвращаем информацию о модальных окнах
            # Агент сам решит что с ними делать через query_dom
            if self.logger:
                modals_count = len(modals_info)
                if target_element_in_modal:
                    self.logger.info(f"   ℹ️  Обнаружено {modals_count} модальных окон, целевой элемент находится внутри модального окна")
                else:
                    self.logger.info(f"   ℹ️  Обнаружено {modals_count} модальных окон")
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.warning(f"   Ошибка при проверке модальных окон: {e}")
            return {"found": False, "modals": [], "error": str(e)}
    
    async def bring_element_into_view(
        self,
        description: Optional[str] = None,
        selector: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Прокручивает страницу до указанного элемента.
        
        Args:
            description: Описание элемента, если селектор неизвестен
            selector: Готовый CSS-селектор для прокрутки
        """
        target_selector = selector
        target_label = description or selector or "element"
        
        if not target_selector and description:
            element = await self.extractor.find_element_by_description(description)
            if element:
                target_selector = element.get("selector")
                target_label = element.get("text") or description
        
        if not target_selector:
            return {
                "success": False,
                "error": "Не удалось определить селектор для прокрутки"
            }
        
        if self.logger:
            self.logger.info(f"   Прокрутка до элемента '{target_label}'")
        
        try:
            return await self._scroll_to_element(target_selector)
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _click_with_retry(self, selector: str, description: str) -> Dict[str, Any]:
        """Клик с повторными попытками и обработкой перехватывающих элементов"""
        # Проверяем, не слишком ли общий селектор (a, button, div без классов)
        is_too_generic = selector in ['a', 'button', 'div', 'span', 'p'] and '.' not in selector and '#' not in selector and '[' not in selector
        
        # Если селектор слишком общий - используем альтернативный способ через текст
        if is_too_generic:
            if self.logger:
                self.logger.warning(f"   Селектор '{selector}' слишком общий, используем поиск по тексту")
            try:
                # Пробуем кликнуть через Playwright locator по тексту
                page = self.browser.page
                if page:
                    # Ищем элемент по тексту описания
                    locator = page.get_by_text(description, exact=False).first
                    count = await locator.count()
                    if count > 0:
                        # Проверяем видимость
                        if await locator.is_visible():
                            await locator.click(timeout=10000)  # Таймаут 10 секунд
                            return {"success": True}
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Не удалось кликнуть через текст: {e}")
        
        # ВАЖНО: Нормализуем селектор перед кликом (убираем нестабильные псевдоклассы)
        # Селекторы с :nth-child() нестабильны - если элементы динамически добавляются/удаляются,
        # индекс может измениться и клик попадет не туда
        original_selector = selector
        selector = self.normalize_selector(selector)
        if selector != original_selector and self.logger:
            self.logger.info(f"   🔧 Нормализован селектор: '{original_selector}' → '{selector}' (убраны позиционные псевдоклассы)")
        
        # Информация об ошибках уже проверяется в PageExtractor и передается агенту через контекст
        # Агент сам решает, что делать при ошибках - это соответствует ТЗ
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Стратегия клика с учетом специфики Playwright:
                # Попытка 1-2: Обычный клик (Playwright автоматически ждет видимости, прокручивает, ждет стабильности)
                # Попытка 3: Принудительный клик (force=True) для случаев когда элемент перекрыт или нестабилен
                force_click = (attempt == max_retries - 1)
                
                if self.logger and force_click:
                    self.logger.info(f"   Последняя попытка: используем принудительный клик (force=True)")
                
                # Проверяем существование элемента перед кликом
                if attempt == 0:
                    try:
                        page = self.browser.page
                        if page:
                            locator = page.locator(selector).first
                            count = await locator.count()
                            if count == 0:
                                if self.logger:
                                    self.logger.warning(f"   ⚠️  Элемент с селектором '{selector}' не найден на странице")
                                # Если нормализованный селектор не найден, пробуем оригинальный
                                if selector != original_selector:
                                    if self.logger:
                                        self.logger.info(f"   🔄 Пробуем оригинальный селектор: '{original_selector}'")
                                    selector = original_selector
                                    locator = page.locator(selector).first
                                    count = await locator.count()
                                    if count == 0:
                                        return {
                                            "success": False,
                                            "error": f"Элемент с селектором '{selector}' не найден на странице"
                                        }
                            elif count > 1:
                                # Если найдено несколько элементов - используем .first (Playwright автоматически выберет первый)
                                if self.logger:
                                    self.logger.info(f"   ℹ️  Найдено {count} элементов с селектором '{selector}', будет использован первый")
                    except Exception as check_error:
                        if self.logger:
                            self.logger.debug(f"   Не удалось проверить существование элемента: {check_error}")
                
                # Пробуем обычный клик
                result = await self.browser.click(selector, timeout=10000, force=force_click, use_mouse_fallback=False)  # Таймаут 10 секунд
                
                if result.get("success"):
                    return result
                else:
                    # Если обычный клик не сработал, пробуем через Mouse API как fallback
                    # Это особенно полезно для сайтов, которые обрабатывают клики через JavaScript обработчики событий
                    if attempt < max_retries - 1:  # Не используем fallback на последней попытке (там уже force=True)
                        if self.logger:
                            self.logger.info(f"   🔄 Обычный клик не сработал, пробуем через Mouse API (fallback)")
                        
                        mouse_result = await self.browser.click_with_mouse_events(selector, timeout=10000)
                        if mouse_result.get("success"):
                            if self.logger:
                                self.logger.info(f"   ✅ Клик через Mouse API выполнен успешно")
                            return mouse_result
                        else:
                            if self.logger:
                                self.logger.debug(f"   Mouse API тоже не сработал: {mouse_result.get('error', 'Неизвестная ошибка')}")
                    # Если fallback не помог или это последняя попытка, продолжаем с обычной логикой
                    return result
                    
            except Exception as e:
                error_str = str(e)
                # Если элемент перехватывается другим элементом
                if "intercepts pointer events" in error_str or "intercepts" in error_str.lower():
                    if attempt < max_retries - 1:
                        # Проверяем наличие модальных окон и обрабатываем их
                        try:
                            # Проверяем, находится ли элемент в модальном окне
                            element_info = await self.extractor.find_element_by_description(description)
                            element_in_modal = element_info.get("in_modal", False) if element_info else False
                            
                            # Если элемент в модальном окне - не закрываем модальное окно
                            # Вместо этого пробуем кликнуть через JavaScript или прокрутить до элемента
                            if element_in_modal:
                                if self.logger:
                                    self.logger.info(f"   Элемент находится в модальном окне, пробуем альтернативный способ клика")
                                # Пробуем кликнуть через JavaScript
                                try:
                                    page = self.browser.page
                                    if page:
                                        clicked = await page.evaluate("""
                                        (selector) => {
                                            try {
                                                const el = document.querySelector(selector);
                                                if (el) {
                                                    el.scrollIntoView({ behavior: 'instant', block: 'center' });
                                                    el.click();
                                                    return true;
                                                }
                                                return false;
                                            } catch (e) {
                                                return false;
                                            }
                                        }
                                        """, selector)
                                        if clicked:
                                            await asyncio.sleep(0.5)
                                            return {"success": True}
                                except:
                                    pass
                            else:
                                # Элемент не в модальном окне - закрываем мешающие модальные окна
                                await self._check_and_handle_modals(
                                    target_element_in_modal=False,
                                    task=self.current_task
                                )
                                await asyncio.sleep(0.5)
                        except Exception as check_error:
                            # Если не удалось проверить - просто закрываем модальные окна
                            await self._close_modals()
                            await asyncio.sleep(0.5)
                        continue
                
                # Если таймаут или элемент не найден - пробуем альтернативный способ
                if "timeout" in error_str.lower() or "timed out" in error_str.lower() or "not found" in error_str.lower():
                    if attempt < max_retries - 1:
                        if self.logger:
                            self.logger.warning(f"   Проблема при клике ({error_str[:50]}), пробуем альтернативный способ через JavaScript...")
                        # Универсальный способ клика через JavaScript: пробуем разные варианты
                        try:
                            page = self.browser.page
                            if page:
                                # Вариант 1: Обычный клик через JavaScript
                                clicked = await page.evaluate("""
                                (selector) => {
                                    try {
                                        const el = document.querySelector(selector);
                                        if (el) {
                                            // Прокручиваем до элемента
                                            el.scrollIntoView({ behavior: 'instant', block: 'center' });
                                            // Пробуем кликнуть
                                            el.click();
                                            return true;
                                        }
                                        return false;
                                    } catch (e) {
                                        return false;
                                    }
                                }
                                """, selector)
                                if clicked:
                                    await asyncio.sleep(1)
                                    return {"success": True}
                                
                                # Вариант 2: Если обычный клик не сработал, пробуем через событие
                                clicked = await page.evaluate("""
                                (selector) => {
                                    try {
                                        const el = document.querySelector(selector);
                                        if (el) {
                                            el.scrollIntoView({ behavior: 'instant', block: 'center' });
                                            // Создаем и диспатчим событие клика
                                            const clickEvent = new MouseEvent('click', {
                                                bubbles: true,
                                                cancelable: true,
                                                view: window
                                            });
                                            el.dispatchEvent(clickEvent);
                                            return true;
                                        }
                                        return false;
                                    } catch (e) {
                                        return false;
                                    }
                                }
                                """, selector)
                                if clicked:
                                    await asyncio.sleep(1)
                                    return {"success": True}
                        except Exception as js_error:
                            if self.logger:
                                self.logger.debug(f"   JavaScript клик не сработал: {js_error}")
                        continue
                
                # Если это последняя попытка - возвращаем ошибку
                if attempt == max_retries - 1:
                    return {
                        "success": False,
                        "error": str(e)
                    }
        
        return {
            "success": False,
            "error": f"Не удалось кликнуть после {max_retries} попыток"
        }
    
    async def _scroll_to_element(self, selector: str) -> Dict[str, Any]:
        """Прокрутка страницы до элемента"""
        try:
            # Используем Playwright для прокрутки
            page = self.browser.page
            if not page:
                return {"success": False, "error": "Page not available"}
            
            element = page.locator(selector).first
            count = await element.count()
            if count == 0:
                return {"success": False, "error": "Element not found"}
            
            # Проверяем видимость
            is_visible = await element.is_visible()
            if is_visible:
                return {"success": True, "alreadyVisible": True}
            
            # Прокручиваем до элемента
            await element.scroll_into_view_if_needed()
            # Ждем немного для завершения прокрутки
            await asyncio.sleep(0.5)
            
            return {"success": True, "scrolled": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _type_text(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Ввод текста"""
        text = parameters.get("text", "")
        selector = parameters.get("selector")
        description = parameters.get("element_description", "")
        
        if self.logger:
            masked_text = text[:20] + "..." if len(text) > 20 else text
            self.logger.info(f"⌨️  Ввод текста в поле '{description}': '{masked_text}'")
        
        element = None

        if not selector:
            if self.logger:
                self.logger.info("   Поиск поля по описанию...")
            try:
                if self._description_suggests_search(description):
                    element = await self.extractor.find_search_input()
                    if element and self.logger:
                        self.logger.info("   Используем поисковую строку, найденную по ключевым словам")
                if not element:
                    element = await self.extractor.find_input_field(description)
                    if element and self.logger:
                        self.logger.info("   Найдено текстовое поле через find_input_field")
                if not element:
                    element = await self.extractor.find_element_by_description(description)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Ошибка при поиске поля ввода: {e}")
                element = None

            if element and not self._is_text_input_element(element):
                if self.logger:
                    self.logger.warning("   Найденный элемент не является текстовым полем, выполняем дополнительный поиск")
                fallback = await self.extractor.find_input_field(description)
                if fallback:
                    element = fallback

            if element:
                selector = element.get("selector")
                element_text = element.get("text", "")
                element_type = element.get("type", "")
                if self.logger:
                    self.logger.info(f"   Найден селектор: {selector}")
                    self.logger.info(f"   Тип элемента: {element_type}, текст: '{element_text[:50]}...'")
            else:
                if self.logger:
                    self.logger.error(f"   Поле '{description}' не найдено на странице после всех стратегий поиска")
                return {
                    "success": False,
                    "error": f"Поле '{description}' не найдено на странице"
                }
        
        if not selector:
            return {
                "success": False,
                "error": f"Не удалось найти селектор для поля '{description}'"
            }
        
        # Проверяем, является ли поле textarea (многострочное)
        # Для textarea используем fill вместо type_text для лучшей поддержки многострочного текста
        is_textarea = False
        try:
            element_info = await self.browser.page.evaluate("""
            (selector) => {
                try {
                    const el = document.querySelector(selector);
                    if (!el) return { isTextarea: false };
                    return {
                        isTextarea: el.tagName === 'TEXTAREA',
                        tagName: el.tagName ? el.tagName.toLowerCase() : '',
                        type: el.type || ''
                    };
                } catch (e) {
                    return { isTextarea: false };
                }
            }
            """, selector)
            is_textarea = element_info.get("isTextarea", False)
        except:
            pass
        
        if is_textarea:
            if self.logger:
                self.logger.info(f"   Обнаружено многострочное поле (textarea), используем fill")
            result = await self.browser.fill(selector, text)
        else:
            result = await self.browser.type_text(selector, text)
        
        if result.get("success"):
            text_length = len(text)
            line_count = text.count('\n') + 1
            if is_textarea:
                result["message"] = f"Многострочный текст введен в поле '{description}' ({line_count} строк, {text_length} символов)"
            else:
                result["message"] = f"Текст введен в поле '{description}' ({text_length} символов)"
            if self.logger:
                self.logger.success(f"   ✓ Текст введен успешно")
        return result
    
    def _normalize_url(self, url: str) -> str:
        """Добавляет схему к URL, если модель вернула домен без http/https"""
        if not url:
            return url
        cleaned = url.strip()
        if not cleaned:
            return cleaned
        parsed = urlparse(cleaned)
        if parsed.scheme:
            return cleaned
        if cleaned.startswith("//"):
            return f"https:{cleaned}"
        return f"https://{cleaned}"

    async def _navigate(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Переход по URL"""
        url = parameters.get("url")
        search_query = parameters.get("search_query")
        
        if url:
            normalized_url = self._normalize_url(url)
            if self.logger:
                if normalized_url != (url or "").strip():
                    self.logger.info(f"🌐 Переход по URL: {normalized_url} (нормализовано из '{url}')")
                else:
                    self.logger.info(f"🌐 Переход по URL: {normalized_url}")
            
            try:
                result = await self.browser.navigate(normalized_url)
                if result.get("success"):
                    result["message"] = f"Переход на {normalized_url}"
                    result["url"] = result.get("url", normalized_url)  # Сохраняем финальный URL после редиректов
                    if self.logger:
                        self.logger.success(f"   ✓ Переход выполнен успешно")
                else:
                    # Если навигация не удалась, но страница частично загрузилась
                    current_url = await self.browser.get_current_url()
                    if current_url and current_url != "about:blank" and current_url != normalized_url:
                        # Возможно, произошел редирект или страница загрузилась частично
                        if self.logger:
                            self.logger.warning(f"   ⚠ Страница загрузилась частично: {current_url}")
                        # Возвращаем успех, но с предупреждением
                        return {
                            "success": True,
                            "message": f"Переход выполнен (частичная загрузка): {current_url}",
                            "url": current_url,
                            "warning": result.get("error", "Страница загрузилась частично")
                        }
                return result
            except Exception as e:
                error_msg = str(e)
                if self.logger:
                    self.logger.error(f"   ✗ Ошибка при переходе: {error_msg}")
                return {
                    "success": False,
                    "error": f"Ошибка навигации: {error_msg}"
                }
        elif search_query:
            if self.logger:
                self.logger.info(f"🔍 Поиск на странице: '{search_query}'")
            
            # Стратегия 1: Используем поисковую строку на сайте (если доступна)
            try:
                search_input = await self.extractor.find_search_input()
                if search_input:
                    search_selector = search_input.get("selector")
                    if search_selector:
                        if self.logger:
                            self.logger.info(f"   Найдена поисковая строка: {search_selector}")
                        
                        # Вводим запрос в поисковую строку
                        type_result = await self.browser.type_text(search_selector, search_query)
                        if type_result.get("success"):
                            # Ждем немного для автодополнения
                            await asyncio.sleep(1)
                            
                            # Пробуем найти кнопку поиска или нажать Enter
                            # Сначала ищем кнопку поиска рядом с полем ввода
                            search_button = await self.extractor.page.evaluate("""
                            (inputSelector) => {
                                try {
                                    const input = document.querySelector(inputSelector);
                                    if (!input) return null;
                                    
                                    // Ищем кнопку поиска в том же родителе
                                    let parent = input.parentElement;
                                    let depth = 0;
                                    while (parent && depth < 3) {
                                        const button = parent.querySelector('button[type="submit"], button[aria-label*="поиск" i], button[aria-label*="search" i], button[class*="search" i]');
                                        if (button && button.offsetWidth > 0 && button.offsetHeight > 0) {
                                            if (button.id) return '#' + button.id;
                                            if (button.className) {
                                                const classes = button.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                                                if (classes.length > 0) {
                                                    return 'button.' + classes.join('.');
                                                }
                                            }
                                            return 'button';
                                        }
                                        parent = parent.parentElement;
                                        depth++;
                                    }
                                    return null;
                                } catch (e) {
                                    return null;
                                }
                            }
                            """, search_selector)
                            
                            if search_button:
                                # Кликаем на кнопку поиска
                                click_result = await self.browser.click(search_button)
                                if click_result.get("success"):
                                    # Ждем загрузки результатов поиска
                                    await asyncio.sleep(2)
                                    current_url = await self.browser.get_current_url()
                                    return {
                                        "success": True,
                                        "message": f"Выполнен поиск '{search_query}' через поисковую строку",
                                        "url": current_url
                                    }
                            
                            # Если кнопка не найдена - нажимаем Enter
                            await self.extractor.page.keyboard.press("Enter")
                            await asyncio.sleep(2)
                            current_url = await self.browser.get_current_url()
                            return {
                                "success": True,
                                "message": f"Выполнен поиск '{search_query}' (нажат Enter)",
                                "url": current_url
                            }
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Не удалось использовать поисковую строку: {e}")
            
            # Стратегия 2: Улучшенный поиск элементов на странице
            try:
                # Ищем элемент по описанию (использует все улучшенные стратегии)
                element = await self.extractor.find_element_by_description(search_query)
                if element:
                    selector = element.get("selector")
                    element_type = element.get("type", "")
                    
                    if selector:
                        # Если это ссылка - переходим по href
                        if element_type == "link" or element_type == "a":
                            href = await self.extractor.page.evaluate(f"""
                            (selector) => {{
                                try {{
                                    const el = document.querySelector(selector);
                                    return el ? (el.href || el.getAttribute('href') || '') : '';
                                }} catch (e) {{
                                    return '';
                                }}
                            }}
                            """, selector)
                            if href:
                                result = await self.browser.navigate(href)
                                if result.get("success"):
                                    result["message"] = f"Найдена и открыта ссылка: {element.get('text', search_query)}"
                                return result
                        
                        # Если это кликабельный элемент - кликаем
                        click_result = await self.browser.click(selector)
                        if click_result.get("success"):
                            await asyncio.sleep(1)
                            current_url = await self.browser.get_current_url()
                            return {
                                "success": True,
                                "message": f"Найден и открыт элемент: {element.get('text', search_query)}",
                                "url": current_url
                            }
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Не удалось найти элемент: {e}")
            
            # Стратегия 3: Fallback - поиск в интерактивных элементах (старый способ)
            try:
                page_info = await self.extractor.extract_page_info(include_text=False)
                elements = page_info.get("interactive_elements", [])
                search_query_lower = search_query.lower()
                
                for element in elements:
                    if element.get("type") == "link":
                        link_text = element.get("text", "").lower()
                        if search_query_lower in link_text or any(word in link_text for word in search_query_lower.split() if len(word) >= 3):
                            href = element.get("href")
                            if href:
                                result = await self.browser.navigate(href)
                                if result.get("success"):
                                    result["message"] = f"Найдена и открыта ссылка: {element.get('text')}"
                                return result
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Не удалось найти в интерактивных элементах: {e}")
            
            # Стратегия 4: Прокрутка и повторный поиск
            try:
                if self.logger:
                    self.logger.info("   Прокрутка страницы для поиска элемента...")
                await self.browser.scroll("down", 500)
                await asyncio.sleep(1)
                
                # Повторяем поиск после прокрутки
                element = await self.extractor.find_element_by_description(search_query)
                if element:
                    selector = element.get("selector")
                    if selector:
                        href = await self.extractor.page.evaluate(f"""
                        (selector) => {{
                            try {{
                                const el = document.querySelector(selector);
                                return el ? (el.href || el.getAttribute('href') || '') : '';
                            }} catch (e) {{
                                return '';
                            }}
                        }}
                        """, selector)
                        if href:
                            result = await self.browser.navigate(href)
                            if result.get("success"):
                                result["message"] = f"Найдена ссылка после прокрутки: {element.get('text', search_query)}"
                            return result
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Не удалось найти после прокрутки: {e}")
            
            return {
                "success": False,
                "error": f"Не найдена ссылка по запросу '{search_query}'. Попробованы: поисковая строка, поиск элементов, поиск в интерактивных элементах, прокрутка."
            }
        else:
            return {
                "success": False,
                "error": "Требуется URL или поисковый запрос"
            }
    
    async def _scroll(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Прокрутка страницы
        
        Поддерживает:
        - Прокрутку в направлении (up, down, left, right)
        - Прокрутку до конкретного элемента по описанию
        """
        direction = parameters.get("direction", "down")
        amount = parameters.get("amount", 500)
        to_element_description = parameters.get("to_element")
        
        # Если указано описание элемента для прокрутки до него
        if to_element_description:
            if self.logger:
                self.logger.info(f"📍 Прокрутка до элемента: '{to_element_description}'")
            
            # Ищем элемент по описанию
            element = await self.extractor.find_element_by_description(to_element_description)
            
            if element:
                selector = element.get("selector")
                if selector:
                    # Прокручиваем до элемента
                    result = await self.browser.scroll(direction="down", amount=0, to_element=selector)
                    if result.get("success"):
                        result["message"] = f"Прокрутка до элемента '{to_element_description}' выполнена"
                        if self.logger:
                            self.logger.success(f"   ✓ Прокрутка до элемента выполнена")
                    return result
                else:
                    return {
                        "success": False,
                        "error": f"Найден элемент '{to_element_description}', но не удалось получить селектор"
                    }
            else:
                # Если элемент не найден, пробуем прокрутить вниз и поискать снова
                if self.logger:
                    self.logger.warning(f"   Элемент '{to_element_description}' не найден, прокручиваем вниз для поиска...")
                
                # Прокручиваем вниз
                result = await self.browser.scroll(direction="down", amount=amount)
                
                # Повторяем поиск после прокрутки
                await asyncio.sleep(0.5)
                element = await self.extractor.find_element_by_description(to_element_description)
                
                if element:
                    selector = element.get("selector")
                    if selector:
                        # Прокручиваем до элемента
                        result = await self.browser.scroll(direction="down", amount=0, to_element=selector)
                        if result.get("success"):
                            result["message"] = f"Элемент '{to_element_description}' найден после прокрутки, прокрутка до элемента выполнена"
                            if self.logger:
                                self.logger.success(f"   ✓ Элемент найден и прокрутка выполнена")
                        return result
                
                return {
                    "success": False,
                    "error": f"Элемент '{to_element_description}' не найден даже после прокрутки"
                }
        
        # Обычная прокрутка в направлении
        if self.logger:
            direction_emoji = {"down": "⬇️", "up": "⬆️", "left": "⬅️", "right": "➡️"}.get(direction, "⬇️")
            self.logger.info(f"{direction_emoji} Прокрутка страницы {direction} на {amount}px")
        
        result = await self.browser.scroll(direction, amount)
        if result.get("success"):
            # Добавляем информацию о результате прокрутки
            if result.get("is_at_bottom") and direction == "down":
                result["message"] = f"Прокрутка {direction} на {amount}px выполнена. Достигнут конец страницы."
                if self.logger:
                    self.logger.warning(f"   ⚠️ Достигнут конец страницы")
            elif result.get("is_at_top") and direction == "up":
                result["message"] = f"Прокрутка {direction} на {amount}px выполнена. Достигнуто начало страницы."
                if self.logger:
                    self.logger.warning(f"   ⚠️ Достигнуто начало страницы")
            else:
                result["message"] = f"Прокрутка {direction} на {amount}px выполнена"
            
            if self.logger:
                if result.get("scrolled"):
                    self.logger.success(f"   ✓ Прокрутка выполнена")
                    if result.get("can_scroll_more") is False:
                        self.logger.info(f"   ℹ️ Дальнейшая прокрутка в этом направлении невозможна")
                else:
                    self.logger.warning(f"   ⚠️ Прокрутка не изменила позицию (возможно, достигнут конец страницы)")
        return result
    
    async def _reload_page(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Перезагрузка текущей страницы"""
        if self.logger:
            self.logger.info("🔄 Перезагрузка страницы...")
        
        try:
            page = self.browser.page
            if not page:
                return {"success": False, "error": "Page not available"}
            
            current_url = page.url
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(2)  # Ждем загрузки страницы
            
            if self.logger:
                self.logger.success(f"   ✓ Страница перезагружена: {current_url}")
            
            return {
                "success": True,
                "message": f"Страница перезагружена: {current_url}",
                "url": current_url
            }
        except Exception as e:
            if self.logger:
                self.logger.error(f"   ✗ Ошибка при перезагрузке: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _wait_for_element(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Ожидание элемента"""
        selector = parameters.get("selector")
        description = parameters.get("description", "")
        timeout = parameters.get("timeout", 10000)
        
        if not selector:
            # Пытаемся найти элемент по описанию
            element = await self.extractor.find_element_by_description(description)
            if element:
                selector = element.get("selector")
        
        if selector:
            result = await self.browser.wait_for_element(selector, timeout)
            if result.get("success"):
                result["message"] = f"Элемент '{description}' появился"
            return result
        else:
            return {
                "success": False,
                "error": f"Не удалось найти селектор для элемента '{description}'"
            }
    
    async def _extract_text(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Извлечение текста с ожиданием загрузки элемента
        
        Улучшения:
        - Ожидание появления элемента перед извлечением
        - Поддержка поиска во всех элементах страницы (не только интерактивных)
        - Поддержка больших блоков текста (резюме, описания)
        - Улучшенная обработка ошибок с fallback стратегиями
        """
        selector = parameters.get("selector")
        description = parameters.get("description", "")
        timeout = parameters.get("timeout", 10000)
        
        if selector:
            # Если селектор указан, сначала ждем появления элемента
            wait_result = await self.browser.wait_for_element(selector, timeout=timeout)
            if not wait_result.get("success"):
                # Fallback: пробуем извлечь без ожидания
                if self.logger:
                    self.logger.warning(f"   Элемент не появился, пробуем извлечь напрямую...")
                result = await self.browser.get_text(selector, include_children=True)
                if result.get("success"):
                    return result
                return {
                    "success": False,
                    "error": f"Элемент с селектором '{selector}' не найден. Попробуйте прокрутить страницу или уточнить селектор.",
                    "suggestion": "Попробуйте прокрутить страницу (scroll) или использовать extract_text с описанием элемента вместо селектора."
                }
            
            result = await self.browser.get_text(selector, include_children=True)
            if result.get("success"):
                text_length = result.get("length", 0)
                result["message"] = f"Текст извлечен из '{description}' ({text_length} символов)"
                if self.logger and text_length > 0:
                    preview = result.get("text", "")[:100]
                    self.logger.info(f"   Предпросмотр текста: '{preview}...'")
            return result
        else:
            # Пытаемся найти элемент по описанию (ищет во всех элементах, включая неинтерактивные)
            if self.logger:
                self.logger.info(f"   Поиск элемента по описанию: '{description}'...")
            
            # Стратегия 1: Поиск через интерактивные элементы
            element = await self.extractor.find_element_by_description(description)
            
            # Стратегия 2: Если не нашли в интерактивных, ищем в неинтерактивных элементах
            if not element:
                if self.logger:
                    self.logger.info(f"   Элемент не найден среди интерактивных, ищем в неинтерактивных элементах...")
                
                # Ищем в неинтерактивных элементах через JavaScript
                element = await self._find_non_interactive_element(description)
            
            # Стратегия 3: Если элемент не найден, пробуем прокрутить и поискать снова
            if not element:
                if self.logger:
                    self.logger.info(f"   Элемент не найден, пробуем прокрутить страницу и поискать снова...")
                
                # Прокручиваем страницу вниз
                await self.browser.scroll(direction="down", amount=500)
                await asyncio.sleep(1)  # Ждем загрузки динамического контента
                
                # Повторяем поиск
                element = await self.extractor.find_element_by_description(description)
                if not element:
                    element = await self._find_non_interactive_element(description)
            
            # Стратегия 4: Если элемент найден, пытаемся извлечь текст
            if element:
                selector = element.get("selector")
                if selector:
                    if self.logger:
                        self.logger.info(f"   Найден селектор: {selector}")
                    
                    # Ждем появления элемента
                    wait_result = await self.browser.wait_for_element(selector, timeout=timeout)
                    if not wait_result.get("success"):
                        # Если элемент не появился, пробуем извлечь текст без ожидания
                        if self.logger:
                            self.logger.warning(f"   Элемент не появился, пробуем извлечь напрямую...")
                    
                    result = await self.browser.get_text(selector, include_children=True)
                    if result.get("success"):
                        text_length = result.get("length", 0)
                        result["message"] = f"Текст извлечен из '{description}' ({text_length} символов)"
                        if self.logger:
                            self.logger.success(f"   ✓ Текст успешно извлечен ({text_length} символов)")
                            if text_length > 0:
                                preview = result.get("text", "")[:100]
                                self.logger.info(f"   Предпросмотр: '{preview}...'")
                        return result
                    else:
                        # Стратегия 5: Если текст не извлекся, пробуем извлечь видимый текст страницы
                        if self.logger:
                            self.logger.warning(f"   Не удалось извлечь текст из элемента, пробуем извлечь видимый текст страницы...")
                        
                        page_info = await self.extractor.extract_page_info(include_text=True)
                        visible_text = page_info.get("visible_text_preview", "")
                        
                        if visible_text and len(visible_text) > 100:
                            # Ищем описание в видимом тексте
                            description_lower = description.lower()
                            visible_lower = visible_text.lower()
                            
                            if description_lower in visible_lower or any(word in visible_lower for word in description_lower.split() if len(word) > 3):
                                # Нашли релевантный текст
                                return {
                                    "success": True,
                                    "text": visible_text,
                                    "length": len(visible_text),
                                    "message": f"Текст извлечен из видимого содержимого страницы ({len(visible_text)} символов)",
                                    "note": "Текст извлечен из видимого содержимого страницы, так как элемент не найден или недоступен"
                                }
                else:
                    return {
                        "success": False,
                        "error": f"Найден элемент '{description}', но не удалось получить селектор",
                        "suggestion": "Попробуйте использовать более конкретное описание элемента или прокрутите страницу."
                    }
            
            # Стратегия 6: Если ничего не помогло, извлекаем весь видимый текст страницы как fallback
            if self.logger:
                self.logger.warning(f"   Элемент '{description}' не найден, извлекаем видимый текст страницы как fallback...")
            
            try:
                page_info = await self.extractor.extract_page_info(include_text=True)
                visible_text = page_info.get("visible_text_preview", "")
                
                if visible_text and len(visible_text) > 50:
                    return {
                        "success": True,
                        "text": visible_text,
                        "length": len(visible_text),
                        "message": f"Извлечен видимый текст страницы ({len(visible_text)} символов) - элемент '{description}' не найден",
                        "note": f"Элемент '{description}' не найден, но извлечен видимый текст страницы. Возможно, нужная информация находится в другом месте страницы."
                    }
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"   Ошибка при извлечении видимого текста: {e}")
            
            # Если все стратегии не сработали, возвращаем детальную ошибку
            return {
                "success": False,
                "error": f"Не удалось найти элемент '{description}' для извлечения текста.",
                "suggestions": [
                    "Попробуйте прокрутить страницу (scroll) и повторить попытку",
                    "Используйте более конкретное описание элемента",
                    "Проверьте, что элемент виден на странице",
                    "Попробуйте использовать другие ключевые слова для поиска"
                ],
                "alternative_action": "scroll"
            }
    
    async def _find_non_interactive_element(self, description: str) -> Optional[Dict[str, Any]]:
        """
        Поиск неинтерактивного элемента по описанию
        
        Ищет в div, section, article, main и других блочных элементах
        """
        try:
            script = """
            (description) => {
                const desc = description.toLowerCase();
                const descWords = desc.split(' ').filter(w => w.length > 2);
                
                // Ищем в блочных элементах с текстом
                const candidates = [];
                const selectors = ['div', 'section', 'article', 'main', 'aside', 'header', 'footer', 'p', 'span'];
                
                for (const selector of selectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        if (!el) continue;
                        
                        // Проверяем видимость
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            continue;
                        }
                        
                        // Проверяем размер
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) {
                            continue;
                        }
                        
                        // Получаем текст элемента
                        const text = (el.innerText || el.textContent || '').toLowerCase();
                        if (!text || text.length < 10) continue; // Пропускаем слишком короткие
                        
                        // Проверяем совпадение
                        let score = 0;
                        if (text.includes(desc)) {
                            score = 100;
                        } else {
                            // Проверяем совпадение по словам
                            for (const word of descWords) {
                                if (text.includes(word)) {
                                    score += 10;
                                }
                            }
                        }
                        
                        // Проверяем классы и id
                        const className = (el.className || '').toLowerCase();
                        const id = (el.id || '').toLowerCase();
                        for (const word of descWords) {
                            if (className.includes(word)) score += 5;
                            if (id.includes(word)) score += 8;
                        }
                        
                        if (score >= 10) {
                            // Генерируем селектор
                            let selector = '';
                            if (el.id) {
                                selector = '#' + el.id;
                            } else if (el.className && typeof el.className === 'string') {
                                const classes = el.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                                if (classes.length > 0) {
                                    selector = el.tagName.toLowerCase() + '.' + classes.join('.');
                                }
                            }
                            
                            if (!selector) {
                                selector = el.tagName.toLowerCase();
                            }
                            
                            candidates.push({
                                selector: selector,
                                text: text.substring(0, 200),
                                score: score,
                                tag: el.tagName ? el.tagName.toLowerCase() : 'div'
                            });
                        }
                    }
                }
                
                // Сортируем по score и возвращаем лучший
                if (candidates.length > 0) {
                    candidates.sort((a, b) => b.score - a.score);
                    return candidates[0];
                }
                
                return null;
            }
            """
            
            result = await self.browser.page.evaluate(script, description)
            return result
        except Exception as e:
            if self.logger:
                self.logger.warning(f"   Ошибка при поиске неинтерактивного элемента: {e}")
            return None
    
    async def _take_screenshot(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Создание скриншота с сохранением в файл и автоматическим анализом через vision API
        
        Args:
            parameters: Параметры действия, могут содержать:
                - description: Описание скриншота (опционально)
                - action: Тип действия (опционально)
                - full_page: Делать скриншот всей страницы или только видимой области (опционально, по умолчанию False)
        """
        description = parameters.get("description", "")
        action = parameters.get("action", "screenshot")
        full_page = parameters.get("full_page", False)
        
        # Создаем скриншот без сохранения в файл сначала
        result = await self.browser.take_screenshot(full_page=full_page)
        
        if result.get("success"):
            screenshot_bytes = result.get("screenshot")
            
            # Сохраняем скриншот в файл
            if screenshot_bytes:
                save_result = await self.screenshot_manager.save_screenshot(
                    screenshot_bytes, 
                    description=description, 
                    action=action
                )
                
                if save_result.get("success"):
                    screenshot_path = save_result.get("path")
                    relative_path = save_result.get("relative_path")
                    
                    if self.logger:
                        self.logger.info(f"📸 Скриншот сохранен: {relative_path}")
                    
                    result["screenshot_path"] = screenshot_path
                    result["screenshot_relative_path"] = relative_path
                    result["message"] = f"Скриншот создан и сохранен: {relative_path}"
                    
                    # АВТОМАТИЧЕСКИЙ анализ через vision API (если модель поддерживает)
                    if self.sub_agent_manager:
                        if self.logger:
                            import os
                            file_size = os.path.getsize(screenshot_path) if os.path.exists(screenshot_path) else 0
                            file_size_kb = file_size / 1024
                            self.logger.info(f"🔍 Отправляю скриншот в Vision API для анализа... (размер: {file_size_kb:.1f} KB)")
                        
                        analysis_result = await self._analyze_screenshot_with_vision(
                            screenshot_path, 
                            description
                        )
                        
                        if analysis_result.get("success"):
                            analysis_text = analysis_result.get("analysis", "")
                            analysis_length = len(analysis_text)
                            result["vision_analysis"] = analysis_text
                            if self.logger:
                                self.logger.info(f"✅ Vision анализ завершен успешно (получено {analysis_length} символов анализа)")
                                self.logger.debug(f"Vision анализ: {analysis_text[:200]}..." if len(analysis_text) > 200 else f"Vision анализ: {analysis_text}")
                        else:
                            error_msg = analysis_result.get("error", "Unknown error")
                            if "не поддерживает vision" in error_msg:
                                if self.logger:
                                    self.logger.info(f"ℹ️  Vision анализ пропущен: модель не поддерживает vision API")
                            else:
                                if self.logger:
                                    self.logger.warning(f"⚠️  Vision анализ не выполнен: {error_msg}")
                else:
                    result["save_error"] = save_result.get("error")
                    result["message"] = "Скриншот создан, но не сохранен"
            else:
                result["message"] = "Скриншот создан"
        
        return result
    
    async def _analyze_screenshot_with_vision(self, screenshot_path: str, description: str = "") -> Dict[str, Any]:
        """
        Анализ скриншота через vision API (GPT-4o поддерживает vision)
        
        Args:
            screenshot_path: Путь к файлу скриншота
            description: Описание того, что нужно найти на скриншоте
            
        Returns:
            Результат анализа
        """
        if not self.sub_agent_manager:
            return {"success": False, "error": "Sub-agent manager не доступен"}
        
        try:
            from openai import OpenAI
            from config import OPENAI_API_KEY, OPENAI_MODEL
            import base64
            import os
            
            # Проверяем, поддерживает ли модель vision
            vision_models = ["gpt-4o", "gpt-4-turbo", "gpt-4-vision-preview"]
            if OPENAI_MODEL not in vision_models:
                if self.logger:
                    self.logger.info(f"ℹ️  Vision анализ пропущен: модель {OPENAI_MODEL} не поддерживает vision API")
                return {
                    "success": False, 
                    "error": f"Модель {OPENAI_MODEL} не поддерживает vision API"
                }
            
            # Читаем скриншот и кодируем в base64
            if self.logger:
                file_size = os.path.getsize(screenshot_path) if os.path.exists(screenshot_path) else 0
                file_size_kb = file_size / 1024
                self.logger.debug(f"Читаю скриншот для vision анализа: {screenshot_path} ({file_size_kb:.1f} KB)")
            
            with open(screenshot_path, 'rb') as image_file:
                image_bytes = image_file.read()
                image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                base64_size_kb = len(image_base64) / 1024
            
            if self.logger:
                self.logger.debug(f"Изображение закодировано в base64 ({base64_size_kb:.1f} KB), отправляю в {OPENAI_MODEL}...")
            
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            # Формируем промпт для анализа
            base_prompt = description if description else "Опиши структуру страницы и найди элементы для взаимодействия."
            
            prompt = f"""Проанализируй этот скриншот веб-страницы и найди конкретные элементы для взаимодействия.

{base_prompt}

КРИТИЧЕСКИ ВАЖНО: Для каждого интерактивного элемента укажи ТОЧНЫЙ ТЕКСТ, который виден на элементе, чтобы его можно было найти в списке интерактивных элементов по тексту.

ФОРМАТ ОПИСАНИЯ ЭЛЕМЕНТОВ:
- Кнопки: "Кнопка: [точный текст на кнопке] | Расположение: [где находится]"
- Ссылки: "Ссылка: [точный текст ссылки] | Расположение: [где находится]"
- Поля ввода: "Поле: [placeholder или label] | Расположение: [где находится]"
- Элементы списков: "Элемент списка: [текст элемента] | Позиция: [первый/второй/третий и т.д.]"

Укажи:
1. Тип страницы (список элементов, детальная страница, форма, панель управления и т.д.)
2. Структура списков (если есть): сколько элементов видно, как они организованы, есть ли повторяющиеся паттерны
3. Основные интерактивные элементы: для каждого элемента укажи ТОЧНЫЙ ТЕКСТ в формате выше
4. Модальные окна (если есть) и их содержимое - укажи точный текст всех кнопок и полей
5. Формы (если есть) и их поля - укажи точный текст/placeholder всех полей и кнопок
6. Элементы списков: если видишь список (письма, вакансии, товары и т.д.) - укажи точный текст каждого элемента списка

КРИТИЧЕСКИ ВАЖНО: Указывай ТОЧНЫЙ ТЕКСТ элементов (как он виден на экране), чтобы агент мог найти эти элементы в списке интерактивных элементов по тексту и использовать их селекторы напрямую.

Примеры правильного формата:
- "Кнопка: Найти | Расположение: справа от поля поиска"
- "Поле: Поиск вакансий | Расположение: в верхней части страницы"
- "Ссылка: Откликнуться | Расположение: в карточке первой вакансии"
- "Элемент списка: Golang Developer | Позиция: первый в списке"
"""
            
            if self.logger:
                self.logger.debug(f"Отправляю запрос в Vision API ({OPENAI_MODEL})...")
            
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_base64}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=800
            )
            
            analysis = response.choices[0].message.content.strip()
            tokens_used = response.usage.total_tokens if hasattr(response, 'usage') and response.usage else None
            
            if self.logger:
                if tokens_used:
                    self.logger.debug(f"Vision API ответ получен (использовано токенов: {tokens_used})")
                else:
                    self.logger.debug(f"Vision API ответ получен")
            
            return {
                "success": True,
                "analysis": analysis,
                "tokens_used": tokens_used
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _analyze_modals_with_sub_agent(self, modals_info: list, task: str, page_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Анализ модальных окон через DOM Sub-agent для определения их релевантности
        
        Args:
            modals_info: Список информации о модальных окнах
            task: Текущая задача
            page_info: Информация о странице
            
        Returns:
            Результат анализа с рекомендациями по действиям
        """
        if not self.sub_agent_manager:
            return {
                "success": False,
                "error": "Sub-agent manager не доступен"
            }
        
        try:
            # Формируем вопрос для DOM Sub-agent
            modals_description = []
            for i, modal in enumerate(modals_info, 1):
                modal_text = modal.get("text_preview", "")[:100]
                has_form = modal.get("has_form", False)
                input_count = modal.get("input_count", 0)
                modal_desc = f"Модальное окно {i}: '{modal_text}'"
                if has_form:
                    modal_desc += f", содержит форму с {input_count} полями"
                modals_description.append(modal_desc)
            
            query = f"""На странице открыто {len(modals_info)} модальных окон:

{chr(10).join(modals_description)}

Текущая задача: {task}

Проанализируй модальные окна и определи:
1. Какие модальные окна нужны для выполнения задачи? (формы для заполнения, подтверждения действий)
2. Какие модальные окна лишние? (реклама, уведомления, которые можно закрыть)
3. Что нужно сделать с каждым модальным окном? (заполнить форму / закрыть / игнорировать)
4. В каком порядке работать с модальными окнами?

Ответь кратко и структурированно."""
            
            # Получаем контекст страницы
            from src.context.manager import ContextManager
            from config import OPENAI_MODEL
            context_manager = ContextManager(OPENAI_MODEL)
            context = context_manager.prepare_context(page_info)
            
            # Задаем вопрос DOM Sub-agent'у
            result = await self.sub_agent_manager.query_dom(query, context, page_info)
            
            if result.get("success"):
                return {
                    "success": True,
                    "analysis": result.get("answer", ""),
                    "agent": result.get("agent", "DOMSubAgent")
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Не удалось получить анализ от DOM Sub-agent")
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _query_dom(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Задать вопрос DOM Sub-agent'у о структуре страницы
        
        Args:
            parameters: Параметры с ключом 'query' (вопрос о странице)
            
        Returns:
            Результат с ответом от DOM Sub-agent'а
        """
        query = parameters.get("query", "")
        
        if not query:
            return {
                "success": False,
                "error": "Требуется вопрос (параметр 'query')"
            }
        
        if not self.sub_agent_manager:
            return {
                "success": False,
                "error": "Sub-agent manager не доступен. query_dom требует sub-агентов."
            }
        
        # Проверка на повторные вопросы - блокируем выполнение если вопрос уже был задан
        if hasattr(self, 'state_manager') and self.state_manager:
            recent_queries = self.state_manager.get_recent_query_dom_info(limit=10)
            normalized_query = self._normalize_query_for_comparison(query)
            for q in recent_queries:
                if q.get("normalized_query") == normalized_query:
                    answer = q.get("answer", "")
                    if self.logger:
                        self.logger.warning(f"⚠️  Вопрос query_dom уже был задан ранее: {query[:100]}")
                        self.logger.info(f"    Используй предыдущий ответ: {answer[:200]}")
                    # Извлекаем селектор из кэшированного ответа
                    extracted_selector = self.extract_selector_from_answer(answer)
                    
                    # Нормализуем селектор (убираем нестабильные псевдоклассы типа :nth-child())
                    if extracted_selector:
                        original_selector = extracted_selector
                        extracted_selector = self.normalize_selector(extracted_selector)
                        if extracted_selector != original_selector and self.logger:
                            self.logger.debug(f"   🔧 Нормализован селектор из кэша: '{original_selector}' → '{extracted_selector}'")
                    
                    result_dict = {
                        "success": True,
                        "answer": answer,
                        "message": f"Использован предыдущий ответ на вопрос",
                        "from_cache": True
                    }
                    
                    if extracted_selector:
                        result_dict["extracted_selector"] = extracted_selector
                        if self.logger:
                            self.logger.info(f"   ✅ Извлечен селектор из кэша: {extracted_selector}")
                    
                    return result_dict
        
        if self.logger:
            self.logger.info(f"🔍 Задаю вопрос DOM Sub-agent'у: '{query}'")
        
        try:
            # Получаем текущую информацию о странице
            page_info = await self.extractor.extract_page_info()
            
            # Получаем контекст страницы
            from src.context.manager import ContextManager
            from config import OPENAI_MODEL
            context_manager = ContextManager(OPENAI_MODEL)
            context = context_manager.prepare_context(page_info)
            
            # Задаем вопрос DOM Sub-agent'у
            result = await self.sub_agent_manager.query_dom(query, context, page_info)
            
            if result.get("success"):
                answer = result.get("answer", "")
                # Извлекаем селектор из ответа автоматически
                extracted_selector = self.extract_selector_from_answer(answer)
                
                # Нормализуем селектор (убираем нестабильные псевдоклассы типа :nth-child())
                if extracted_selector:
                    original_selector = extracted_selector
                    extracted_selector = self.normalize_selector(extracted_selector)
                    if extracted_selector != original_selector and self.logger:
                        self.logger.debug(f"   🔧 Нормализован селектор: '{original_selector}' → '{extracted_selector}'")
                
                if self.logger:
                    self.logger.info(f"📋 Ответ DOM Sub-agent'а: {answer[:200]}...")
                    if extracted_selector:
                        self.logger.info(f"   ✅ Извлечен селектор: {extracted_selector}")
                
                result_dict = {
                    "success": True,
                    "answer": answer,
                    "message": f"DOM Sub-agent ответил на вопрос"
                }
                
                # Добавляем извлеченный селектор в результат (уже нормализованный)
                if extracted_selector:
                    result_dict["extracted_selector"] = extracted_selector
                
                return result_dict
            else:
                error = result.get("error", "Неизвестная ошибка")
                if self.logger:
                    self.logger.warning(f"⚠️  Ошибка при запросе к DOM Sub-agent'у: {error}")
                return {
                    "success": False,
                    "error": f"Не удалось получить ответ от DOM Sub-agent'а: {error}"
                }
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Ошибка при выполнении query_dom: {e}")
            return {
                "success": False,
                "error": f"Ошибка при выполнении query_dom: {str(e)}"
            }
    
    def _normalize_query_for_comparison(self, query: str) -> str:
        """Нормализация вопроса для сравнения"""
        normalized = query.lower().strip()
        # Убираем лишние пробелы
        normalized = ' '.join(normalized.split())
        # Убираем знаки препинания в конце
        normalized = normalized.rstrip('?.,!;:')
        return normalized
    
    @staticmethod
    def extract_selector_from_answer(answer: str) -> Optional[str]:
        """
        Извлечение селектора из ответа query_dom
        
        Ищет селекторы в различных форматах:
        - "Селектор: #delete"
        - "Селектор: .class-name"
        - "Селектор: button.class"
        - "селектор: #id"
        - "селектор: `div.MessageListItem__root`" (с обратными кавычками)
        - "селектор div.MessageListItem__root"
        - "селектор: div.MessageListItem__content--y26Sh" (с двойными подчеркиваниями и дефисами)
        - "селектор: input[data-qa='search']" (с атрибутами)
        """
        import re
        
        if not answer:
            return None
        
        # Функция для валидации селектора
        def is_valid_selector(sel: str) -> bool:
            if not sel or len(sel) == 0:
                return False
            # Убираем лишние символы для проверки
            sel_clean = sel.strip('`').rstrip('.,;!?')
            if not sel_clean:
                return False
            # Проверяем валидность селектора
            return (sel_clean.startswith('#') or 
                    sel_clean.startswith('.') or 
                    '[' in sel_clean or 
                    '.' in sel_clean or 
                    '--' in sel_clean or  # BEM модификаторы
                    '__' in sel_clean or  # BEM элементы
                    any(tag in sel_clean.lower() for tag in [
                        'div', 'button', 'input', 'a', 'span', 'form', 
                        'select', 'textarea', 'ul', 'li', 'p', 'h1', 'h2', 
                        'h3', 'h4', 'h5', 'h6', 'img', 'svg', 'path'
                    ]))
        
        # Сначала ищем селекторы в обратных кавычках (часто используется в ответах)
        backtick_pattern = r'`([^`]+)`'
        backtick_matches = re.findall(backtick_pattern, answer)
        for match in backtick_matches:
            selector = match.strip()
            if is_valid_selector(selector):
                selector = selector.strip('`').rstrip('.,;!?')
                return selector
        
        # Ищем точный формат "Селектор: [селектор]" (может быть с обратными кавычками)
        # Улучшенные паттерны для разных форматов селекторов
        selector_patterns = [
            # Селекторы с ID или классом
            r'селектор[:\s]+`?([#.][\w-]+(?:[._-][\w-]+)*)`?',  
            # Селекторы с тегом и классом (включая BEM с __ и --)
            r'селектор[:\s]+`?([\w]+(?:\.[\w-]+(?:__[\w-]+)?(?:--[\w-]+)?)+)`?',  
            # Селекторы с атрибутами
            r'селектор[:\s]+`?([\w]+\[[^\]]+\])`?',  
            # Селекторы с тегом и ID
            r'селектор[:\s]+`?([\w]+#[\w-]+)`?',  
            # Простые теги
            r'селектор[:\s]+`?([\w]+)`?',  
            # Селекторы с пробелами (например, "div .class")
            r'селектор[:\s]+`?([\w]+(?:\s+[.#][\w-]+)+)`?',
        ]
        
        # Ищем в оригинальном тексте (с учетом регистра для селекторов)
        for pattern in selector_patterns:
            matches = re.findall(pattern, answer, re.IGNORECASE)
            if matches:
                selector = matches[0].strip()
                # Убираем обратные кавычки если есть
                selector = selector.strip('`')
                # Очищаем от лишних символов в конце
                selector = selector.rstrip('.,;!?')
                if is_valid_selector(selector):
                    return selector
        
        # Если не нашли через паттерны, ищем более гибко
        # Ищем после слова "селектор" до конца предложения или до следующего слова
        # Улучшенный паттерн для селекторов с BEM нотацией и дефисами
        # ВАЖНО: Учитываем псевдоклассы типа :nth-child(), :nth-of-type() и т.д.
        flexible_patterns = [
            # Селекторы с псевдоклассами (nth-child, nth-of-type и т.д.)
            r'селектор[:\s]+`?([^\s,\.;!?\n`]+(?:[._-][^\s,\.;!?\n`]+)*(?::nth-[^,\s\.;!?\n`]+)?)`?',
            # Общий паттерн без псевдоклассов
            r'селектор[:\s]+`?([^\s,\.;!?\n`]+(?:[._-][^\s,\.;!?\n`]+)*)`?',  # Общий паттерн
            r'селектор[:\s]+([#.][^\s,\.;!?\n`]+)',  # Начинается с # или .
            r'селектор[:\s]+([\w]+\[[^\]]+\])',  # С атрибутами
        ]
        
        for pattern in flexible_patterns:
            selector_match = re.search(pattern, answer, re.IGNORECASE)
            if selector_match:
                selector = selector_match.group(1).strip()
                # Убираем обратные кавычки если есть
                selector = selector.strip('`')
                selector = selector.rstrip('.,;!?')
                if is_valid_selector(selector):
                    return selector
        
        # Последняя попытка: ищем любой текст в обратных кавычках после слова "селектор"
        last_resort = re.search(r'селектор[:\s]+`([^`]+)`', answer, re.IGNORECASE)
        if last_resort:
            selector = last_resort.group(1).strip().rstrip('.,;!?')
            if is_valid_selector(selector):
                return selector
        
        return None
    
    @staticmethod
    def normalize_selector(selector: str) -> str:
        """
        Нормализация селектора: убирает нестабильные псевдоклассы типа :nth-child()
        
        Проблема: селекторы с :nth-child() нестабильны - если элементы динамически 
        добавляются/удаляются, индекс может измениться и клик попадет не туда.
        
        Решение: убираем :nth-child() и другие позиционные псевдоклассы, оставляя базовый селектор.
        Playwright locator.first автоматически выберет первый элемент.
        
        Args:
            selector: Исходный селектор
            
        Returns:
            Нормализованный селектор без позиционных псевдоклассов
        """
        if not selector:
            return selector
        
        import re
        
        # Убираем позиционные псевдоклассы которые могут быть нестабильными
        # :nth-child(), :nth-of-type(), :first-child, :last-child и т.д.
        # Но сохраняем :hover, :focus, :disabled и другие функциональные псевдоклассы
        positional_pseudos = [
            r':nth-child\([^)]+\)',
            r':nth-of-type\([^)]+\)',
            r':nth-last-child\([^)]+\)',
            r':nth-last-of-type\([^)]+\)',
            r':first-child',
            r':last-child',
            r':first-of-type',
            r':last-of-type',
        ]
        
        normalized = selector
        for pattern in positional_pseudos:
            normalized = re.sub(pattern, '', normalized)
        
        # Убираем двойные пробелы и лишние двоеточия
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        normalized = re.sub(r':+', ':', normalized)
        
        return normalized
    
    async def _search_on_page(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Поиск через поисковую строку на странице"""
        query = parameters.get("query", "")
        
        if not query:
            return {
                "success": False,
                "error": "Требуется поисковый запрос"
            }
        
        if self.logger:
            self.logger.info(f"🔍 Поиск через поисковую строку: '{query}'")
        
        try:
            # Ищем поисковую строку на странице
            search_input = await self.extractor.find_search_input()
            if not search_input:
                return {
                    "success": False,
                    "error": "Поисковая строка не найдена на странице"
                }
            
            search_selector = search_input.get("selector")
            if not search_selector:
                return {
                    "success": False,
                    "error": "Не удалось получить селектор поисковой строки"
                }
            
            if self.logger:
                self.logger.info(f"   Найдена поисковая строка: {search_selector}")
            
            # Вводим запрос в поисковую строку
            type_result = await self.browser.type_text(search_selector, query)
            if not type_result.get("success"):
                return {
                    "success": False,
                    "error": f"Не удалось ввести текст в поисковую строку: {type_result.get('error')}"
                }
            
            # Ждем немного для автодополнения
            await asyncio.sleep(1)
            
            # Пробуем найти кнопку поиска или нажать Enter
            search_button = await self.extractor.page.evaluate("""
            (inputSelector) => {
                try {
                    const input = document.querySelector(inputSelector);
                    if (!input) return null;
                    
                    // Ищем кнопку поиска в том же родителе
                    let parent = input.parentElement;
                    let depth = 0;
                    while (parent && depth < 3) {
                        const button = parent.querySelector('button[type="submit"], button[aria-label*="поиск" i], button[aria-label*="search" i], button[class*="search" i]');
                        if (button && button.offsetWidth > 0 && button.offsetHeight > 0) {
                            if (button.id) return '#' + button.id;
                            if (button.className) {
                                const classes = button.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                                if (classes.length > 0) {
                                    return 'button.' + classes.join('.');
                                }
                            }
                            return 'button';
                        }
                        parent = parent.parentElement;
                        depth++;
                    }
                    return null;
                } catch (e) {
                    return null;
                }
            }
            """, search_selector)
            
            if search_button:
                # Кликаем на кнопку поиска
                click_result = await self.browser.click(search_button)
                if click_result.get("success"):
                    # Ждем загрузки результатов поиска
                    await asyncio.sleep(2)
                    current_url = await self.browser.get_current_url()
                    if self.logger:
                        self.logger.success(f"   ✓ Поиск выполнен успешно")
                    return {
                        "success": True,
                        "message": f"Выполнен поиск '{query}' через поисковую строку",
                        "url": current_url
                    }
            
            # Если кнопка не найдена - нажимаем Enter
            await self.extractor.page.keyboard.press("Enter")
            await asyncio.sleep(2)
            current_url = await self.browser.get_current_url()
            if self.logger:
                self.logger.success(f"   ✓ Поиск выполнен (нажат Enter)")
            return {
                "success": True,
                "message": f"Выполнен поиск '{query}' (нажат Enter)",
                "url": current_url
            }
        except Exception as e:
            error_msg = str(e)
            if self.logger:
                self.logger.error(f"   ✗ Ошибка при поиске: {error_msg}")
            return {
                "success": False,
                "error": f"Ошибка поиска: {error_msg}"
            }
    
    async def _verify_action_result(
        self,
        action_type: str,
        description: str,
        page_state_before: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Проверка результата действия после его выполнения
        
        Проверяет, что действие действительно привело к ожидаемому результату:
        - Изменилась ли страница (URL, заголовок, DOM)
        - Появились ли новые элементы (модальные окна, формы)
        - Соответствует ли результат ожидаемому для данного типа действия
        
        Args:
            action_type: Тип действия ("click", "navigate", etc.)
            description: Описание элемента/действия
            page_state_before: Состояние страницы до действия
            
        Returns:
            Результат проверки с информацией об изменениях
        """
        result = {
            "action_verified": False,
            "page_changed_detected": False,
            "modal_detected": False,
            "form_detected": False
        }
        
        try:
            # Получаем текущее состояние страницы
            page_state_after = await self.extractor.get_page_state_hash()
            current_url = await self.browser.get_current_url()
            
            # Проверяем появление модальных окон и форм
            visible_modals = page_state_after.get('visible_modal_count', 0)
            visible_forms = page_state_after.get('visible_form_count', 0)
            
            if visible_modals > 0:
                modals_info = page_state_after.get('modals', [])
                if self.logger:
                    self.logger.info(f"   🎯 Обнаружено модальное окно после действия!")
                    for modal in modals_info[:2]:
                        if modal.get('has_form'):
                            self.logger.info(f"      Модальное окно содержит форму с {modal.get('input_count', 0)} полями")
                
                result["modal_detected"] = True
                result["modal_info"] = {
                    "count": visible_modals,
                    "has_form": any(m.get('has_form') for m in modals_info),
                    "input_count": sum(m.get('input_count', 0) for m in modals_info)
                }
            
            if visible_forms > 0:
                if self.logger:
                    self.logger.info(f"   📝 Обнаружена форма после действия!")
                result["form_detected"] = True
                result["form_count"] = visible_forms
            
            # Сравниваем состояние страницы до и после действия
            if page_state_before:
                dom_hash_before = page_state_before.get('dom_hash', '')
                dom_hash_after = page_state_after.get('dom_hash', '')
                
                if dom_hash_before != dom_hash_after:
                    result["page_changed_detected"] = True
                    result["dom_changed"] = True
                
                # Проверяем изменение количества элементов
                interactive_before = page_state_before.get('interactive_count', 0)
                interactive_after = page_state_after.get('interactive_count', 0)
                
                if interactive_after > interactive_before:
                    result["new_interactive_elements"] = interactive_after - interactive_before
            
            # Определяем, было ли действие успешным на основе изменений
            # Действие считается успешным, если:
            # 1. Появилось модальное окно или форма (для кликов на кнопки открытия форм)
            # 2. Изменился DOM (для любых действий, которые должны изменить страницу)
            # 3. Изменился URL (для навигации)
            
            if result.get("modal_detected") or result.get("form_detected") or result.get("page_changed_detected"):
                result["action_verified"] = True
                if self.logger:
                    self.logger.info(f"   ✓ Результат действия подтвержден: страница изменилась или появились новые элементы")
            else:
                # Если действие не привело к изменениям - это может быть проблемой
                if self.logger:
                    self.logger.warning(f"   ⚠️  Действие выполнено, но страница не изменилась. Возможно, действие не сработало как ожидалось.")
                result["action_verified"] = False
                result["warning"] = "Действие выполнено, но страница не изменилась. Возможно, действие не привело к ожидаемому результату."
            
        except Exception as e:
            if self.logger:
                self.logger.warning(f"   Не удалось проверить результат действия: {e}")
            result["error"] = str(e)
        
        return result

