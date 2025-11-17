"""Контроллер браузера на базе Playwright"""
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from typing import Optional, Dict, Any
import asyncio
from config import BROWSER_TYPE, HEADLESS, BROWSER_TIMEOUT


class BrowserController:
    """Контроллер для управления браузером через Playwright"""
    
    def __init__(self, user_data_dir: Optional[str] = None):
        """
        Инициализация контроллера браузера
        
        Args:
            user_data_dir: Путь к директории пользовательских данных для persistent session
        """
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.user_data_dir = user_data_dir
        
    async def start(self):
        """Запуск браузера"""
        self.playwright = await async_playwright().start()
        
        # Выбор типа браузера
        browser_launcher = {
            "chromium": self.playwright.chromium,
            "firefox": self.playwright.firefox,
            "webkit": self.playwright.webkit
        }.get(BROWSER_TYPE, self.playwright.chromium)
        
        # Параметры запуска
        launch_options = {
            "headless": HEADLESS
        }
        
        # Если указана директория для persistent session
        if self.user_data_dir:
            # Для persistent context используем launch_persistent_context
            # который возвращает BrowserContext напрямую
            self.context = await browser_launcher.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=HEADLESS,
                viewport={"width": 1280, "height": 720}
            )
            # Для persistent context получаем страницы напрямую
            pages = self.context.pages
            if pages:
                self.page = pages[0]
            else:
                self.page = await self.context.new_page()
            self.browser = None  # При persistent context browser не используется напрямую
        else:
            self.browser = await browser_launcher.launch(**launch_options)
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
            self.page = await self.context.new_page()
        
        # Настройка таймаутов
        self.page.set_default_timeout(BROWSER_TIMEOUT)
        self.page.set_default_navigation_timeout(BROWSER_TIMEOUT)
        
    async def close(self):
        """Закрытие браузера"""
        try:
            if self.user_data_dir:
                # Для persistent context - просто закрываем context
                # Playwright автоматически сохранит данные в user_data_dir
                if self.context:
                    await self.context.close()
                # Даем время на сохранение данных
                import asyncio
                await asyncio.sleep(0.5)
            else:
                # Для обычного контекста закрываем страницу и context
                if self.page:
                    await self.page.close()
                if self.context:
                    await self.context.close()
                if self.browser:
                    await self.browser.close()
            
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            # Игнорируем ошибки при закрытии (браузер может быть уже закрыт)
            pass
            
    async def navigate(self, url: str, wait_for_content: bool = True, max_retries: int = 3) -> Dict[str, Any]:
        """
        Переход по URL с ожиданием загрузки контента и retry логикой
        
        Args:
            url: URL для перехода
            wait_for_content: Ждать ли полной загрузки контента (по умолчанию True)
            max_retries: Максимальное количество попыток при ошибке
            
        Returns:
            Результат операции
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Используем domcontentloaded вместо networkidle - быстрее и надежнее
                # networkidle часто не достигается на динамических сайтах
                wait_until = "domcontentloaded"
                
                # Увеличиваем таймаут для сложных страниц
                navigation_timeout = BROWSER_TIMEOUT * (attempt + 1)  # Увеличиваем таймаут с каждой попыткой
                
                response = await self.page.goto(
                    url, 
                    wait_until=wait_until, 
                    timeout=navigation_timeout
                )
                
                # Адаптивное ожидание для рендеринга динамического контента
                if wait_for_content:
                    # Ждем готовности DOM
                    try:
                        await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except:
                        pass  # Игнорируем ошибку, если уже загружено
                    
                    # Пытаемся дождаться network idle для лучшего обнаружения загрузки
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=3000)
                    except:
                        # Если network idle не достигнут - используем адаптивное ожидание
                        # Базовое ожидание зависит от попытки
                        base_delay = 2.0 if attempt == 0 else 3.0
                        await asyncio.sleep(base_delay)
                        
                        # Проверяем наличие интерактивных элементов
                        try:
                            element_count = await self.page.evaluate("() => document.querySelectorAll('button, a, input, select, textarea').length")
                            if element_count == 0 and attempt < max_retries - 1:
                                # Если элементов нет - увеличиваем ожидание
                                await asyncio.sleep(2.0)
                        except:
                            pass
                
                # Проверяем, что страница действительно загрузилась
                current_url = self.page.url
                if current_url and current_url != "about:blank":
                    return {
                        "success": True,
                        "url": current_url,
                        "status": response.status if response else None
                    }
                else:
                    # Страница не загрузилась полностью
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)  # Ждем перед повтором
                        continue
                    else:
                        return {
                            "success": False,
                            "error": "Страница не загрузилась полностью после нескольких попыток"
                        }
                        
            except Exception as e:
                last_error = str(e)
                error_msg_lower = last_error.lower()
                
                # Если это таймаут или ошибка сети - пробуем еще раз
                if ("timeout" in error_msg_lower or 
                    "network" in error_msg_lower or 
                    "navigation" in error_msg_lower):
                    if attempt < max_retries - 1:
                        wait_time = 2 * (attempt + 1)  # Экспоненциальная задержка
                        await asyncio.sleep(wait_time)
                        continue
                else:
                    # Другие ошибки - возвращаем сразу
                    return {
                        "success": False,
                        "error": last_error
                    }
        
        # Если все попытки не удались
        return {
            "success": False,
            "error": f"Не удалось загрузить страницу после {max_retries} попыток: {last_error}"
        }
    
    async def click(self, selector: str, timeout: Optional[int] = None, force: bool = False, trial: bool = False, use_mouse_fallback: bool = False) -> Dict[str, Any]:
        """
        Клик по элементу с использованием возможностей Playwright
        
        Playwright автоматически:
        - Ждет видимости элемента
        - Прокручивает до элемента
        - Ждет стабильности элемента (если не force)
        - Ждет отсутствия анимаций
        - Обрабатывает навигацию после клика
        
        Args:
            selector: CSS селектор элемента
            timeout: Таймаут ожидания элемента
            force: Принудительный клик (игнорирует проверки видимости и действия)
            trial: Режим проверки (не выполняет клик, только проверяет возможность)
            use_mouse_fallback: Если True, при неудаче обычного клика пробует Mouse API
            
        Returns:
            Результат операции
        """
        try:
            timeout = timeout or BROWSER_TIMEOUT
            
            # Используем locator для более гибкого управления кликом
            # Locator автоматически ждет появления элемента в DOM
            locator = self.page.locator(selector).first
            
            # Проверяем наличие элемента
            count = await locator.count()
            if count == 0:
                return {
                    "success": False,
                    "error": f"Элемент с селектором '{selector}' не найден на странице"
                }
            
            # Пробуем клик с учетом опций Playwright
            # Playwright автоматически:
            # - Ждет видимости элемента (если не force)
            # - Прокручивает до элемента
            # - Ждет стабильности элемента (если не force)
            # - Ждет отсутствия анимаций (если не force)
            # - Ждет завершения навигации после клика (если не no_wait_after)
            try:
                await locator.click(
                    timeout=timeout,
                    force=force,
                    trial=trial,
                    # no_wait_after=False - ждем завершения навигации после клика (по умолчанию)
                    # position=None - клик по центру элемента (по умолчанию)
                )
                
                return {"success": True}
            except Exception as click_error:
                # Если обычный клик не сработал и включен fallback - пробуем Mouse API
                if use_mouse_fallback and not trial:
                    error_str = str(click_error).lower()
                    # Пробуем Mouse API для определенных типов ошибок
                    # которые могут быть решены через реальные события мыши
                    should_try_mouse = (
                        "not clickable" in error_str or
                        "not actionable" in error_str or
                        "intercepts pointer events" in error_str or
                        "intercepts" in error_str
                    )
                    
                    if should_try_mouse:
                        # Пробуем клик через Mouse API как fallback
                        mouse_result = await self.click_with_mouse_events(selector, timeout)
                        if mouse_result.get("success"):
                            return {"success": True, "used_mouse_fallback": True}
                        # Если Mouse API тоже не сработал, возвращаем исходную ошибку
                
                # Пробрасываем исходную ошибку для обработки ниже
                raise click_error
                
        except Exception as e:
            error_str = str(e)
            # Детализируем ошибки Playwright для лучшей диагностики
            if "timeout" in error_str.lower() or "timed out" in error_str.lower():
                return {
                    "success": False,
                    "error": f"Таймаут ожидания элемента '{selector}': элемент не появился или не стал кликабельным за {timeout}мс"
                }
            elif "intercepts pointer events" in error_str.lower() or "intercepts" in error_str.lower():
                return {
                    "success": False,
                    "error": f"Элемент '{selector}' перехватывается другим элементом (overlay, modal, другой элемент). Попробуйте force=True или use_mouse_fallback=True"
                }
            elif "not visible" in error_str.lower() or "not attached" in error_str.lower():
                return {
                    "success": False,
                    "error": f"Элемент '{selector}' не виден или не прикреплен к DOM"
                }
            elif "not clickable" in error_str.lower() or "not actionable" in error_str.lower():
                return {
                    "success": False,
                    "error": f"Элемент '{selector}' не кликабелен (может быть disabled, readonly, или перекрыт). Попробуйте use_mouse_fallback=True"
                }
            else:
                return {
                    "success": False,
                    "error": f"Ошибка клика по элементу '{selector}': {error_str}"
                }
    
    async def click_with_mouse_events(self, selector: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Клик через реальные события мыши (Mouse API)
        Используется как fallback когда обычный locator.click() не работает
        
        Этот метод эмулирует реальные события мыши (mousedown, mouseup, click),
        что может быть необходимо для некоторых сайтов, которые обрабатывают клики
        через JavaScript обработчики событий, не реагирующие на программные клики.
        
        Args:
            selector: CSS селектор элемента
            timeout: Таймаут ожидания элемента
            
        Returns:
            Результат операции
        """
        try:
            timeout = timeout or BROWSER_TIMEOUT
            
            # Используем locator для получения элемента
            locator = self.page.locator(selector).first
            
            # Проверяем наличие элемента
            count = await locator.count()
            if count == 0:
                return {
                    "success": False,
                    "error": f"Элемент с селектором '{selector}' не найден на странице"
                }
            
            # Ждем видимости элемента
            await locator.wait_for(state="visible", timeout=timeout)
            
            # Получаем bounding box элемента (координаты и размеры)
            bounding_box = await locator.bounding_box()
            if not bounding_box:
                return {
                    "success": False,
                    "error": f"Не удалось получить координаты элемента '{selector}'"
                }
            
            # Вычисляем центр элемента для клика
            x = bounding_box["x"] + bounding_box["width"] / 2
            y = bounding_box["y"] + bounding_box["height"] / 2
            
            # Прокручиваем до элемента если нужно
            await locator.scroll_into_view_if_needed(timeout=timeout)
            
            # Используем Mouse API для эмуляции реального клика мыши
            # page.mouse.click() генерирует события mousedown, mouseup и click
            await self.page.mouse.click(x, y)
            
            return {"success": True}
        except Exception as e:
            error_str = str(e)
            # Детализируем ошибки для лучшей диагностики
            if "timeout" in error_str.lower() or "timed out" in error_str.lower():
                return {
                    "success": False,
                    "error": f"Таймаут ожидания элемента '{selector}' для клика через Mouse API: элемент не появился или не стал видимым за {timeout}мс"
                }
            elif "not visible" in error_str.lower() or "not attached" in error_str.lower():
                return {
                    "success": False,
                    "error": f"Элемент '{selector}' не виден или не прикреплен к DOM для клика через Mouse API"
                }
            else:
                return {
                    "success": False,
                    "error": f"Ошибка клика через Mouse API по элементу '{selector}': {error_str}"
                }
    
    async def _fill_element(self, selector: str, text: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Базовый helper для ввода текста через Playwright fill
        """
        try:
            timeout = timeout or BROWSER_TIMEOUT
            await self.page.fill(selector, text, timeout=timeout)
            return {"success": True}
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def type_text(self, selector: str, text: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Ввод текста в элемент (обычные input поля)
        """
        return await self._fill_element(selector, text, timeout)

    async def fill(self, selector: str, text: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Явный fill для textarea/многострочных полей
        """
        return await self._fill_element(selector, text, timeout)
    
    async def scroll(self, direction: str = "down", amount: int = 500, to_element: Optional[str] = None) -> Dict[str, Any]:
        """
        Прокрутка страницы или прокручиваемого контейнера
        
        Улучшения:
        - Автоматически находит прокручиваемый контейнер внутри страницы (для SPA типа Яндекс Почты)
        - Использует встроенные методы Playwright для более надежной прокрутки
        - Поддерживает прокрутку до конкретного элемента
        - Проверяет результат прокрутки (изменение позиции, новые элементы)
        
        Args:
            direction: Направление прокрутки ("up", "down", "left", "right")
            amount: Количество пикселей для прокрутки
            to_element: Селектор элемента для прокрутки до него (опционально)
            
        Returns:
            Результат операции с информацией об изменениях
        """
        try:
            # Если указан элемент для прокрутки до него
            if to_element:
                try:
                    # Используем Playwright locator для прокрутки до элемента
                    locator = self.page.locator(to_element).first
                    await locator.scroll_into_view_if_needed()
                    
                    await asyncio.sleep(0.5)  # Задержка для завершения прокрутки
                    
                    # Получаем новую позицию прокрутки
                    scroll_after = await self.page.evaluate("""
                        () => {
                            return {
                                x: window.scrollX || window.pageXOffset,
                                y: window.scrollY || window.pageYOffset
                            };
                        }
                    """)
                    
                    return {
                        "success": True,
                        "scroll_position": {"x": scroll_after["x"], "y": scroll_after["y"]},
                        "scroll_type": "to_element",
                        "scrolled": True,
                        "element": to_element,
                        "message": f"Прокрутка до элемента '{to_element}' выполнена"
                    }
                except Exception as e:
                    # Если не удалось прокрутить до элемента - продолжаем с обычной прокруткой
                    pass
            
            # Находим прокручиваемый контейнер (для SPA типа Яндекс Почты)
            scroll_container_info = await self.page.evaluate("""
                () => {
                    // Ищем прокручиваемый контейнер внутри страницы
                    // Проверяем элементы с overflow: auto/scroll и достаточным scrollHeight
                    const allElements = document.querySelectorAll('*');
                    let bestContainer = null;
                    let maxScrollHeight = 0;
                    let maxScrollableArea = 0;
                    
                    for (const el of allElements) {
                        try {
                            const style = window.getComputedStyle(el);
                            const hasOverflow = style.overflow === 'auto' || style.overflow === 'scroll' || 
                                               style.overflowY === 'auto' || style.overflowY === 'scroll' ||
                                               style.overflow === 'hidden'; // hidden тоже может быть прокручиваемым
                            
                            // Проверяем, можно ли прокручивать элемент
                            const scrollable = el.scrollHeight > el.clientHeight;
                            const scrollableArea = scrollable ? (el.scrollHeight - el.clientHeight) : 0;
                            
                            if (scrollable && scrollableArea > maxScrollableArea) {
                                maxScrollableArea = scrollableArea;
                                maxScrollHeight = el.scrollHeight;
                                bestContainer = el;
                            }
                        } catch (e) {
                            // Игнорируем ошибки
                        }
                    }
                    
                    // Также проверяем элементы с большим scrollHeight даже без явного overflow
                    // (для виртуальных скроллеров и динамических списков)
                    if (!bestContainer || maxScrollableArea < 500) {
                        for (const el of allElements) {
                            try {
                                const scrollableArea = el.scrollHeight > el.clientHeight ? (el.scrollHeight - el.clientHeight) : 0;
                                // Ищем элементы с большой прокручиваемой областью
                                if (scrollableArea > maxScrollableArea && scrollableArea > 500) {
                                    const style = window.getComputedStyle(el);
                                    // Проверяем, что элемент видим и имеет размеры
                                    if (el.offsetWidth > 100 && el.offsetHeight > 100 && 
                                        style.display !== 'none' && style.visibility !== 'hidden') {
                                        maxScrollableArea = scrollableArea;
                                        maxScrollHeight = el.scrollHeight;
                                        bestContainer = el;
                                    }
                                }
                            } catch (e) {
                                // Игнорируем ошибки
                            }
                        }
                    }
                    
                    // Если нашли контейнер с достаточной прокручиваемой областью - используем его
                    const windowScrollHeight = document.documentElement.scrollHeight;
                    const windowScrollableArea = windowScrollHeight > window.innerHeight ? (windowScrollHeight - window.innerHeight) : 0;
                    
                    if (bestContainer && maxScrollableArea > Math.max(500, windowScrollableArea * 0.3)) {
                        return {
                            found: true,
                            selector: bestContainer.id ? `#${bestContainer.id}` : 
                                     bestContainer.className ? `.${bestContainer.className.split(' ')[0]}` : null,
                            scrollHeight: bestContainer.scrollHeight,
                            clientHeight: bestContainer.clientHeight,
                            scrollTop: bestContainer.scrollTop,
                            scrollLeft: bestContainer.scrollLeft,
                            scrollableArea: maxScrollableArea
                        };
                    }
                    
                    // Иначе используем window
                    return {
                        found: false,
                        scrollHeight: document.documentElement.scrollHeight,
                        clientHeight: window.innerHeight,
                        scrollTop: window.scrollY || window.pageYOffset,
                        scrollLeft: window.scrollX || window.pageXOffset,
                        scrollableArea: windowScrollableArea
                    };
                }
            """)
            
            # Получаем текущую позицию прокрутки до прокрутки
            scroll_before = scroll_container_info
            
            # Определяем направление прокрутки
            delta_x = 0
            delta_y = 0
            
            if direction == "down":
                delta_y = amount
            elif direction == "up":
                delta_y = -amount
            elif direction == "right":
                delta_x = amount
            elif direction == "left":
                delta_x = -amount
            
            # Прокручиваем: сначала пробуем через wheel (работает для контейнеров)
            try:
                await self.page.mouse.wheel(delta_x, delta_y)
                await asyncio.sleep(0.5)
                
                # Проверяем результат прокрутки
                scroll_after = await self.page.evaluate("""
                    () => {
                        // Проверяем window
                        const windowScroll = {
                            x: window.scrollX || window.pageXOffset,
                            y: window.scrollY || window.pageYOffset,
                            scrollHeight: document.documentElement.scrollHeight,
                            clientHeight: window.innerHeight
                        };
                        
                        // Ищем прокручиваемый контейнер снова (улучшенный поиск)
                        const allElements = document.querySelectorAll('*');
                        let bestContainer = null;
                        let maxScrollableArea = 0;
                        
                        for (const el of allElements) {
                            try {
                                const scrollableArea = el.scrollHeight > el.clientHeight ? (el.scrollHeight - el.clientHeight) : 0;
                                
                                if (scrollableArea > maxScrollableArea && scrollableArea > 500) {
                                    const style = window.getComputedStyle(el);
                                    if (el.offsetWidth > 100 && el.offsetHeight > 100 && 
                                        style.display !== 'none' && style.visibility !== 'hidden') {
                                        maxScrollableArea = scrollableArea;
                                        bestContainer = el;
                                    }
                                }
                            } catch (e) {
                                // Игнорируем ошибки
                            }
                        }
                        
                        const windowScrollableArea = windowScroll.scrollHeight > windowScroll.clientHeight ? 
                                                   (windowScroll.scrollHeight - windowScroll.clientHeight) : 0;
                        
                        if (bestContainer && maxScrollableArea > Math.max(500, windowScrollableArea * 0.3)) {
                            return {
                                found: true,
                                scrollHeight: bestContainer.scrollHeight,
                                clientHeight: bestContainer.clientHeight,
                                scrollTop: bestContainer.scrollTop,
                                scrollLeft: bestContainer.scrollLeft,
                                windowScroll: windowScroll
                            };
                        }
                        
                        return {
                            found: false,
                            scrollTop: windowScroll.y,
                            scrollLeft: windowScroll.x,
                            scrollHeight: windowScroll.scrollHeight,
                            clientHeight: windowScroll.clientHeight,
                            windowScroll: windowScroll
                        };
                    }
                """)
                
                # Определяем, произошла ли прокрутка
                if scroll_container_info.get("found"):
                    scrolled = (scroll_after.get("scrollTop", 0) != scroll_container_info.get("scrollTop", 0) or
                               scroll_after.get("scrollLeft", 0) != scroll_container_info.get("scrollLeft", 0))
                    scroll_type = "container"
                else:
                    scrolled = (scroll_after.get("windowScroll", {}).get("y", 0) != scroll_container_info.get("scrollTop", 0) or
                               scroll_after.get("windowScroll", {}).get("x", 0) != scroll_container_info.get("scrollLeft", 0))
                    scroll_type = "window"
                
                # Проверяем, достигли ли мы конца
                if scroll_container_info.get("found"):
                    is_at_bottom = scroll_after.get("scrollTop", 0) >= scroll_after.get("scrollHeight", 0) - scroll_after.get("clientHeight", 0) - 10
                    is_at_top = scroll_after.get("scrollTop", 0) <= 10
                else:
                    window_scroll = scroll_after.get("windowScroll", {})
                    is_at_bottom = window_scroll.get("y", 0) >= window_scroll.get("scrollHeight", 0) - window_scroll.get("clientHeight", 0) - 10
                    is_at_top = window_scroll.get("y", 0) <= 10
                
                return {
                    "success": True,
                    "scroll_position": {
                        "x": scroll_after.get("scrollLeft", 0) if scroll_container_info.get("found") else scroll_after.get("windowScroll", {}).get("x", 0),
                        "y": scroll_after.get("scrollTop", 0) if scroll_container_info.get("found") else scroll_after.get("windowScroll", {}).get("y", 0)
                    },
                    "scroll_type": scroll_type,
                    "scrolled": scrolled,
                    "direction": direction,
                    "amount": amount,
                    "is_at_bottom": is_at_bottom,
                    "is_at_top": is_at_top,
                    "can_scroll_more": not (is_at_bottom and direction == "down") and not (is_at_top and direction == "up"),
                    "message": f"Прокрутка {direction} на {amount}px выполнена ({scroll_type})" if scrolled else "Прокрутка не изменила позицию (возможно, достигнут конец)"
                }
            except Exception as e:
                # Fallback: прокручиваем через JavaScript напрямую в контейнер
                scroll_result = await self.page.evaluate("""
                    ([direction, amount]) => {
                        // Находим прокручиваемый контейнер (улучшенный поиск)
                        const allElements = document.querySelectorAll('*');
                        let bestContainer = null;
                        let maxScrollableArea = 0;
                        
                        for (const el of allElements) {
                            try {
                                const style = window.getComputedStyle(el);
                                const scrollableArea = el.scrollHeight > el.clientHeight ? (el.scrollHeight - el.clientHeight) : 0;
                                
                                if (scrollableArea > maxScrollableArea && scrollableArea > 500) {
                                    // Проверяем, что элемент видим и имеет размеры
                                    if (el.offsetWidth > 100 && el.offsetHeight > 100 && 
                                        style.display !== 'none' && style.visibility !== 'hidden') {
                                        maxScrollableArea = scrollableArea;
                                        bestContainer = el;
                                    }
                                }
                            } catch (e) {
                                // Игнорируем ошибки
                            }
                        }
                        
                        const windowScrollableArea = document.documentElement.scrollHeight > window.innerHeight ? 
                                                   (document.documentElement.scrollHeight - window.innerHeight) : 0;
                        
                        const container = bestContainer && maxScrollableArea > Math.max(500, windowScrollableArea * 0.3)
                                       ? bestContainer : document.documentElement;
                        
                        const currentScroll = {
                            x: container === document.documentElement ? (window.scrollX || window.pageXOffset) : container.scrollLeft,
                            y: container === document.documentElement ? (window.scrollY || window.pageYOffset) : container.scrollTop
                        };
                        
                        let newX = currentScroll.x;
                        let newY = currentScroll.y;
                        const maxY = container === document.documentElement 
                                   ? (document.documentElement.scrollHeight - window.innerHeight)
                                   : (container.scrollHeight - container.clientHeight);
                        const maxX = container === document.documentElement
                                   ? (document.documentElement.scrollWidth - window.innerWidth)
                                   : (container.scrollWidth - container.clientWidth);
                        
                        if (direction === 'down') {
                            newY = Math.min(currentScroll.y + amount, maxY);
                        } else if (direction === 'up') {
                            newY = Math.max(currentScroll.y - amount, 0);
                        } else if (direction === 'right') {
                            newX = Math.min(currentScroll.x + amount, maxX);
                        } else if (direction === 'left') {
                            newX = Math.max(currentScroll.x - amount, 0);
                        }
                        
                        if (container === document.documentElement) {
                            window.scrollTo(newX, newY);
                        } else {
                            container.scrollTop = newY;
                            container.scrollLeft = newX;
                        }
                        
                        // Проверяем реальную позицию после прокрутки
                        // scrollTop/scrollLeft устанавливаются синхронно, но проверяем для надежности
                        const actualScroll = {
                            x: container === document.documentElement ? (window.scrollX || window.pageXOffset) : container.scrollLeft,
                            y: container === document.documentElement ? (window.scrollY || window.pageYOffset) : container.scrollTop
                        };
                        
                        return {
                            success: true,
                            scrolled: actualScroll.x !== currentScroll.x || actualScroll.y !== currentScroll.y,
                            scrollPosition: { x: actualScroll.x, y: actualScroll.y },
                            scrollType: container === document.documentElement ? 'window' : 'container'
                        };
                    }
                """, [direction, amount])
                
                await asyncio.sleep(0.5)
                
                if scroll_result.get("success"):
                    scroll_after = scroll_result.get("scrollPosition", {})
                    scrolled = scroll_result.get("scrolled", False)
                    scroll_type = scroll_result.get("scrollType", "window")
                    
                    return {
                        "success": True,
                        "scroll_position": scroll_after,
                        "scroll_type": scroll_type,
                        "scrolled": scrolled,
                        "direction": direction,
                        "amount": amount,
                        "message": f"Прокрутка {direction} на {amount}px выполнена ({scroll_type}, через JavaScript fallback)" if scrolled else "Прокрутка не изменила позицию"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Не удалось выполнить прокрутку: {str(e)}"
                    }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def wait_for_element(self, selector: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Ожидание появления элемента
        
        Args:
            selector: CSS селектор элемента
            timeout: Таймаут ожидания
            
        Returns:
            Результат операции
        """
        try:
            timeout = timeout or BROWSER_TIMEOUT
            await self.page.wait_for_selector(selector, timeout=timeout)
            return {"success": True}
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_text(self, selector: str, include_children: bool = True) -> Dict[str, Any]:
        """
        Получение текста элемента с поддержкой больших блоков и неинтерактивных элементов
        
        Args:
            selector: CSS селектор элемента
            include_children: Включать ли текст дочерних элементов (по умолчанию True)
            
        Returns:
            Результат с текстом элемента
        """
        try:
            # Используем innerText для получения видимого текста (включая форматирование)
            # или textContent для получения всего текста включая скрытый
            # В Playwright Python аргументы передаются как отдельные параметры
            text = await self.page.evaluate("""
            ([selector, includeChildren]) => {
                try {
                    const element = document.querySelector(selector);
                    if (!element) return null;
                    
                    // Для больших блоков текста используем innerText (видимый текст)
                    // Это лучше работает для резюме, описаний и т.д.
                    if (includeChildren) {
                        // Получаем весь видимый текст элемента и его детей
                        return element.innerText || element.textContent || '';
                    } else {
                        // Только текст самого элемента без детей
                        return element.textContent || '';
                    }
                } catch (e) {
                    return null;
                }
            }
            """, [selector, include_children])
            
            if text is None:
                return {
                    "success": False,
                    "error": f"Элемент с селектором '{selector}' не найден"
                }
            
            # Очищаем текст от лишних пробелов, но сохраняем структуру
            text = text.strip() if text else ""
            
            return {
                "success": True,
                "text": text,
                "length": len(text)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_current_url(self) -> str:
        """Получение текущего URL"""
        return self.page.url if self.page else ""
    
    async def get_title(self) -> str:
        """Получение заголовка страницы"""
        return await self.page.title() if self.page else ""
    
    async def take_screenshot(self, path: Optional[str] = None, full_page: bool = True) -> Dict[str, Any]:
        """
        Создание скриншота страницы
        
        Args:
            path: Путь для сохранения скриншота (опционально)
            full_page: Делать скриншот всей страницы (True) или только видимой области (False)
            
        Returns:
            Результат операции
        """
        try:
            screenshot_bytes = await self.page.screenshot(path=path, full_page=full_page)
            return {
                "success": True,
                "screenshot": screenshot_bytes if not path else None,
                "path": path
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def evaluate(self, script: str, *args) -> Any:
        """
        Выполнение JavaScript на странице
        
        Args:
            script: JavaScript код для выполнения
            *args: Аргументы для передачи в скрипт
            
        Returns:
            Результат выполнения скрипта
        """
        if not self.page:
            return None
        if args:
            return await self.page.evaluate(script, *args)
        return await self.page.evaluate(script)
    
    async def check_element_visibility(self, selector: str) -> Dict[str, Any]:
        """
        Проверка видимости элемента
        
        Args:
            selector: CSS селектор элемента
            
        Returns:
            Результат проверки видимости
        """
        script = """
        (selector) => {
            try {
                const element = document.querySelector(selector);
                if (!element) {
                    return {visible: false, error: 'Element not found'};
                }
                
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
                const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                
                // Проверка стилей
                const isDisplayed = style.display !== 'none' && 
                                  style.visibility !== 'hidden' && 
                                  style.opacity !== '0';
                
                // Проверка размеров
                const hasSize = rect.width > 0 && rect.height > 0;
                
                // Проверка попадания в viewport
                const isInViewport = rect.top < viewportHeight && rect.bottom > 0 && 
                                    rect.left < viewportWidth && rect.right > 0;
                
                return {
                    visible: isDisplayed && hasSize && isInViewport,
                    isDisplayed: isDisplayed,
                    hasSize: hasSize,
                    isInViewport: isInViewport,
                    rect: {
                        top: rect.top,
                        bottom: rect.bottom,
                        left: rect.left,
                        right: rect.right,
                        width: rect.width,
                        height: rect.height
                    }
                };
            } catch (e) {
                return {visible: false, error: e.message};
            }
        }
        """
        try:
            result = await self.page.evaluate(script, selector)
            return result if isinstance(result, dict) else {"visible": False, "error": "Unknown error"}
        except Exception as e:
            return {"visible": False, "error": str(e)}

