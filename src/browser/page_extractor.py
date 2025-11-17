"""Извлечение структурированной информации со страницы"""
from typing import List, Dict, Any, Optional
from playwright.async_api import Page
import json
import asyncio
import re
import time


class PageExtractor:
    """Извлечение релевантной информации со страницы для AI-агента"""
    
    def __init__(self, page: Page, logger=None):
        self.page = page
        self.logger = logger
        # Кэш для результатов поиска элементов (на время одной итерации)
        self._element_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._current_url: Optional[str] = None
        # Кэш для результатов extract_page_info (на короткое время)
        self._page_info_cache: Optional[Dict[str, Any]] = None
        self._page_info_cache_url: Optional[str] = None
        self._page_info_cache_time: float = 0.0
    
    async def extract_page_info(self, include_text: bool = True, use_cache: bool = True, cache_ttl: float = 0.5) -> Dict[str, Any]:
        """
        Извлечение структурированной информации о странице с кэшированием
        
        Args:
            include_text: Включать ли текстовое содержимое элементов
            use_cache: Использовать ли кэш (по умолчанию True)
            cache_ttl: Время жизни кэша в секундах (по умолчанию 0.5 сек)
            
        Returns:
            Словарь с информацией о странице
        """
        current_url = self.page.url
        current_time = time.time()
        
        # Проверяем кэш
        if use_cache and self._page_info_cache and self._page_info_cache_url == current_url:
            if current_time - self._page_info_cache_time < cache_ttl:
                return self._page_info_cache.copy()
        
        # Очищаем кэш при смене URL
        if current_url != self._current_url:
            self._element_cache.clear()
            self._current_url = current_url
            self._page_info_cache = None
        
        # Извлекаем различные типы информации
        interactive_elements = await self._extract_interactive_elements(include_text)
        visible_text = await self._extract_visible_text() if include_text else ""
        page_metadata = await self._extract_metadata()
        
        result = {
            "url": current_url,
            "title": page_metadata.get("title", ""),
            "interactive_elements": interactive_elements,
            "visible_text_preview": visible_text[:1000] if include_text else "",  # Первые 1000 символов для лучшего понимания контекста
            "metadata": page_metadata
        }
        
        # Сохраняем в кэш
        if use_cache:
            self._page_info_cache = result.copy()
            self._page_info_cache_url = current_url
            self._page_info_cache_time = current_time
        
        return result
    
    async def _extract_interactive_elements(self, include_text: bool = True) -> List[Dict[str, Any]]:
        """Извлечение интерактивных элементов (кнопки, ссылки, формы, поля ввода)"""
        script = """
        () => {
            const elements = [];
            
            // Функция для проверки видимости элемента
            function isVisible(element) {
                if (!element) return false;
                try {
                    const style = window.getComputedStyle(element);
                    return style.display !== 'none' && 
                           style.visibility !== 'hidden' && 
                           style.opacity !== '0' &&
                           element.offsetWidth > 0 && 
                           element.offsetHeight > 0;
                } catch (e) {
                    return false;
                }
            }
            
            // Функция для получения текста элемента - УЛУЧШЕННАЯ ВЕРСИЯ
            function getElementText(element) {
                // Проверка на null/undefined
                if (!element) return '';
                
                // Сначала пробуем aria-label (самый надежный)
                const ariaLabel = element.getAttribute('aria-label');
                if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
                
                // Для кнопок и ссылок берем текст напрямую
                if (element.tagName === 'BUTTON' || element.tagName === 'A') {
                    const text = element.innerText?.trim() || element.textContent?.trim() || '';
                    if (text) return text;
                }
                
                // Для input берем placeholder, label или value
                if (element.tagName === 'INPUT') {
                    const placeholder = element.placeholder || '';
                    const value = element.value || '';
                    const label = element.labels && element.labels[0] ? (element.labels[0].textContent?.trim() || '') : '';
                    return placeholder || label || value || '';
                }
                
                // Для других элементов берем title, aria-label или текст
                const title = element.getAttribute('title');
                if (title && title.trim()) return title.trim();
                
                // Пробуем получить текст из элемента и его детей
                const text = element.innerText?.trim() || element.textContent?.trim() || '';
                // Берем первые 150 символов для лучшего описания
                return text.substring(0, 150);
            }
            
            // Функция для получения селектора элемента
            function getSelector(element) {
                if (!element) return 'unknown';
                if (element.id) return '#' + element.id;
                if (element.className && typeof element.className === 'string') {
                    const classes = element.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                    if (classes.length > 0) {
                        const tagName = element.tagName ? element.tagName.toLowerCase() : 'div';
                        return tagName + '.' + classes.join('.');
                    }
                }
                return element.tagName ? element.tagName.toLowerCase() : 'div';
            }
            
            // Функция для получения информации о родительском контейнере (карточка, блок)
            function getParentContainerInfo(element) {
                if (!element) return null;
                try {
                    let parent = element.parentElement;
                    let depth = 0;
                    while (parent && depth < 10) {
                        const tagName = parent.tagName?.toLowerCase() || '';
                        const className = (parent.className || '').toLowerCase();
                        const id = (parent.id || '').toLowerCase();
                        const role = parent.getAttribute('role') || '';
                        
                        // Проверяем, является ли родитель контейнером (карточка, блок, item)
                        if (className.includes('card') || 
                            className.includes('item') || 
                            className.includes('block') ||
                            className.includes('container') ||
                            id.includes('card') ||
                            id.includes('item') ||
                            role === 'article' ||
                            role === 'region') {
                            const containerText = (parent.innerText || parent.textContent || '').trim().substring(0, 100);
                            return {
                                selector: getSelector(parent),
                                text_preview: containerText,
                                tag: tagName
                            };
                        }
                        parent = parent.parentElement;
                        depth++;
                    }
                } catch (e) {
                    return null;
                }
                return null;
            }
            
            // Функция для проверки, находится ли элемент в модальном окне
            function isInModal(element) {
                if (!element) return false;
                try {
                    let parent = element.parentElement;
                    let depth = 0;
                    while (parent && depth < 20) {
                        const tagName = parent.tagName?.toLowerCase();
                        const role = parent.getAttribute('role');
                        const id = parent.id?.toLowerCase() || '';
                        const className = parent.className?.toLowerCase() || '';
                        const ariaModal = parent.getAttribute('aria-modal');
                        const dataQa = parent.getAttribute('data-qa')?.toLowerCase() || '';
                        const dataTestid = parent.getAttribute('data-testid')?.toLowerCase() || '';
                        
                        // Проверяем модальные окна по различным признакам (расширенный список)
                        if (role === 'dialog' || 
                            tagName === 'dialog' ||
                            ariaModal === 'true' ||
                            className.includes('modal') || 
                            className.includes('dialog') ||
                            className.includes('popup') ||
                            className.includes('overlay') ||
                            className.includes('backdrop') ||
                            id.includes('modal') || 
                            id.includes('dialog') ||
                            id.includes('popup') ||
                            dataQa.includes('modal') ||
                            dataQa.includes('dialog') ||
                            dataTestid.includes('modal') ||
                            dataTestid.includes('dialog') ||
                            // Проверка через z-index (модальные окна обычно имеют высокий z-index)
                            (parseInt(window.getComputedStyle(parent).zIndex) > 1000 && 
                             parent.offsetWidth > 100 && parent.offsetHeight > 100)) {
                            return true;
                        }
                        parent = parent.parentElement;
                        depth++;
                    }
                    return false;
                } catch (e) {
                    return false;
                }
            }
            
            // Функция для проверки, находится ли элемент в форме
            function isInForm(element) {
                if (!element) return false;
                try {
                    let parent = element.parentElement;
                    let depth = 0;
                    while (parent && depth < 10) {
                        const tagName = parent.tagName?.toLowerCase();
                        const role = parent.getAttribute('role');
                        
                        if (tagName === 'form' || role === 'form') {
                            return true;
                        }
                        parent = parent.parentElement;
                        depth++;
                    }
                    return false;
                } catch (e) {
                    return false;
                }
            }
            
            // Извлекаем кнопки - УЛУЧШЕННАЯ ВЕРСИЯ (включая невидимые, но важные)
            document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]').forEach(btn => {
                try {
                    if (btn) {
                        const text = getElementText(btn);
                        // Включаем даже если не видим, но есть текст или aria-label
                        if (isVisible(btn) || text || btn.getAttribute('aria-label') || btn.getAttribute('title')) {
                            const parentContainer = getParentContainerInfo(btn);
                            elements.push({
                                type: 'button',
                                selector: getSelector(btn),
                                text: text,
                                tag: btn.tagName ? btn.tagName.toLowerCase() : 'button',
                                id: btn.id || null,
                                classes: Array.from(btn.classList || []).slice(0, 3),
                                aria_label: btn.getAttribute('aria-label') || null,
                                visible: isVisible(btn),
                                in_modal: isInModal(btn),
                                in_form: isInForm(btn),
                                parent_container: parentContainer
                            });
                        }
                    }
                } catch (e) {
                    // Игнорируем ошибки при обработке элементов
                }
            });
            
            // Извлекаем ссылки - УЛУЧШЕННАЯ ВЕРСИЯ
            document.querySelectorAll('a[href]').forEach(link => {
                try {
                    if (link) {
                        const text = getElementText(link);
                        // Включаем даже если не видим, но есть текст или href содержит важную информацию
                        const href = link.href || '';
                        // Проверяем важность ссылки по наличию текста или структуре URL
                        // НЕ используем конкретные пути - это универсальная проверка важности
                        const isImportant = text.length > 0 || href.length > 0;
                        if (isVisible(link) || isImportant) {
                            const parentContainer = getParentContainerInfo(link);
                            elements.push({
                                type: 'link',
                                selector: getSelector(link),
                                text: text || href.split('/').pop() || 'ссылка',
                                href: href,
                                tag: 'a',
                                id: link.id || null,
                                classes: Array.from(link.classList || []).slice(0, 3),
                                visible: isVisible(link),
                                in_modal: isInModal(link),
                                in_form: isInForm(link),
                                parent_container: parentContainer
                            });
                        }
                    }
                } catch (e) {
                    // Игнорируем ошибки при обработке элементов
                }
            });
            
            // Извлекаем поля ввода
            document.querySelectorAll('input, textarea, select').forEach(input => {
                try {
                    if (input && isVisible(input)) {
                        elements.push({
                            type: 'input',
                            selector: getSelector(input),
                            input_type: input.type || (input.tagName ? input.tagName.toLowerCase() : 'input'),
                            placeholder: input.placeholder || null,
                            name: input.name || null,
                            label: input.labels && input.labels[0] ? (input.labels[0].textContent?.trim() || null) : null,
                            tag: input.tagName ? input.tagName.toLowerCase() : 'input',
                            id: input.id || null,
                            classes: Array.from(input.classList || []).slice(0, 3),
                            in_modal: isInModal(input),
                            in_form: isInForm(input)
                        });
                    }
                } catch (e) {
                    // Игнорируем ошибки при обработке элементов
                }
            });
            
            // Извлекаем важные элементы с data-атрибутами или aria-атрибутами
            document.querySelectorAll('[data-testid], [data-qa], [aria-label], [role]').forEach(el => {
                try {
                    if (el && isVisible(el) && !elements.some(e => e.selector === getSelector(el))) {
                        const role = el.getAttribute('role');
                        if (role && ['navigation', 'search', 'main', 'form'].includes(role)) {
                            elements.push({
                                type: 'landmark',
                                selector: getSelector(el),
                                role: role,
                                text: getElementText(el),
                                tag: el.tagName ? el.tagName.toLowerCase() : 'div',
                                id: el.id || null,
                                classes: Array.from(el.classList || []).slice(0, 3)
                            });
                        }
                    }
                } catch (e) {
                    // Игнорируем ошибки при обработке элементов
                }
            });
            
            // Извлекаем карточки/блоки с кликабельными областями (для ресторанов, товаров и т.д.)
            document.querySelectorAll('[class*="card" i], [class*="item" i], [class*="product" i], [data-qa*="card" i], [data-testid*="card" i]').forEach(card => {
                try {
                    if (card && isVisible(card)) {
                        // Проверяем, является ли карточка кликабельной
                        const isClickable = card.onclick || 
                                          card.getAttribute('onclick') ||
                                          card.style.cursor === 'pointer' ||
                                          card.tagName === 'A' ||
                                          card.getAttribute('role') === 'button' ||
                                          card.getAttribute('role') === 'link' ||
                                          card.closest('a') !== null;
                        
                        if (isClickable) {
                            const text = getElementText(card);
                            // Добавляем только если есть текст или изображение
                            if (text || card.querySelector('img')) {
                                const existing = elements.find(e => e.selector === getSelector(card));
                                if (!existing) {
                                    elements.push({
                                        type: 'card',
                                        selector: getSelector(card),
                                        text: text,
                                        tag: card.tagName ? card.tagName.toLowerCase() : 'div',
                                        id: card.id || null,
                                        classes: Array.from(card.classList || []).slice(0, 3),
                                        href: card.href || (card.closest('a') ? card.closest('a').href : null)
                                    });
                                }
                            }
                        }
                    }
                } catch (e) {
                    // Игнорируем ошибки при обработке элементов
                }
            });
            
            // Извлекаем элементы с обработчиками событий (onclick, data-action и т.д.)
            document.querySelectorAll('[onclick], [data-action], [data-click], [data-onclick]').forEach(el => {
                try {
                    if (el && isVisible(el) && !elements.some(e => e.selector === getSelector(el))) {
                        const text = getElementText(el);
                        // Добавляем только если есть текст или это важный элемент
                        if (text || el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button') {
                            elements.push({
                                type: 'interactive',
                                selector: getSelector(el),
                                text: text,
                                tag: el.tagName ? el.tagName.toLowerCase() : 'div',
                                id: el.id || null,
                                classes: Array.from(el.classList || []).slice(0, 3),
                                has_handler: true
                            });
                        }
                    }
                } catch (e) {
                    // Игнорируем ошибки при обработке элементов
                }
            });
            
            // УНИВЕРСАЛЬНОЕ извлечение элементов списков (БЕЗ хардкодинга конкретных селекторов)
            // Ищем контейнеры списков с role="list" или role="listbox"
            document.querySelectorAll('[role="list"], [role="listbox"]').forEach(listContainer => {
                try {
                    if (listContainer && isVisible(listContainer)) {
                        // Ищем элементы списка внутри контейнера
                        const listItems = listContainer.querySelectorAll('[role="listitem"], [role="option"]');
                        
                        listItems.forEach((listItem, index) => {
                            try {
                                if (listItem && isVisible(listItem)) {
                                    const itemText = getElementText(listItem);
                                    const parentContainer = getParentContainerInfo(listItem);
                                    
                                    // Извлекаем кликабельные элементы внутри listitem
                                    const clickableElements = [];
                                    
                                    // Ссылки внутри элемента списка
                                    listItem.querySelectorAll('a[href]').forEach(link => {
                                        if (isVisible(link)) {
                                            const linkText = getElementText(link);
                                            clickableElements.push({
                                                type: 'link',
                                                selector: getSelector(link),
                                                text: linkText || link.href,
                                                href: link.href
                                            });
                                        }
                                    });
                                    
                                    // Кнопки внутри элемента списка
                                    listItem.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]').forEach(btn => {
                                        if (isVisible(btn)) {
                                            const btnText = getElementText(btn);
                                            clickableElements.push({
                                                type: 'button',
                                                selector: getSelector(btn),
                                                text: btnText
                                            });
                                        }
                                    });
                                    
                                    // Проверяем, является ли сам элемент списка кликабельным
                                    const isItemClickable = listItem.onclick || 
                                                          listItem.getAttribute('onclick') ||
                                                          listItem.style.cursor === 'pointer' ||
                                                          listItem.tagName === 'A' ||
                                                          listItem.closest('a') !== null ||
                                                          listItem.getAttribute('role') === 'button' ||
                                                          listItem.getAttribute('role') === 'link';
                                    
                                    // Добавляем элемент списка, если он видим и имеет текст или кликабельные элементы
                                    if (itemText || clickableElements.length > 0 || isItemClickable) {
                                        const existing = elements.find(e => e.selector === getSelector(listItem));
                                        if (!existing) {
                                            elements.push({
                                                type: 'list_item',
                                                selector: getSelector(listItem),
                                                text: itemText,
                                                tag: listItem.tagName ? listItem.tagName.toLowerCase() : 'div',
                                                id: listItem.id || null,
                                                classes: Array.from(listItem.classList || []).slice(0, 3),
                                                visible: isVisible(listItem),
                                                in_modal: isInModal(listItem),
                                                in_form: isInForm(listItem),
                                                parent_container: parentContainer,
                                                clickable_elements: clickableElements.length > 0 ? clickableElements : null,
                                                is_clickable: isItemClickable,
                                                list_index: index
                                            });
                                        }
                                    }
                                }
                            } catch (e) {
                                // Игнорируем ошибки при обработке элементов списка
                            }
                        });
                    }
                } catch (e) {
                    // Игнорируем ошибки при обработке контейнеров списков
                }
            });
            
            // Также ищем элементы с role="listitem" и role="option" вне явных контейнеров списков
            // (на случай если структура списка не использует role="list")
            document.querySelectorAll('[role="listitem"], [role="option"]').forEach(listItem => {
                try {
                    // Пропускаем если уже обработан как часть контейнера списка
                    if (listItem.closest('[role="list"], [role="listbox"]')) {
                        return;
                    }
                    
                    if (listItem && isVisible(listItem)) {
                        const itemText = getElementText(listItem);
                        const parentContainer = getParentContainerInfo(listItem);
                        
                        // Извлекаем кликабельные элементы внутри
                        const clickableElements = [];
                        listItem.querySelectorAll('a[href], button, [role="button"]').forEach(el => {
                            if (isVisible(el)) {
                                const elText = getElementText(el);
                                clickableElements.push({
                                    type: el.tagName === 'A' ? 'link' : 'button',
                                    selector: getSelector(el),
                                    text: elText
                                });
                            }
                        });
                        
                        const isItemClickable = listItem.onclick || 
                                              listItem.getAttribute('onclick') ||
                                              listItem.style.cursor === 'pointer' ||
                                              listItem.tagName === 'A' ||
                                              listItem.closest('a') !== null;
                        
                        if (itemText || clickableElements.length > 0 || isItemClickable) {
                            const existing = elements.find(e => e.selector === getSelector(listItem));
                            if (!existing) {
                                elements.push({
                                    type: 'list_item',
                                    selector: getSelector(listItem),
                                    text: itemText,
                                    tag: listItem.tagName ? listItem.tagName.toLowerCase() : 'div',
                                    id: listItem.id || null,
                                    classes: Array.from(listItem.classList || []).slice(0, 3),
                                    visible: isVisible(listItem),
                                    in_modal: isInModal(listItem),
                                    in_form: isInForm(listItem),
                                    parent_container: parentContainer,
                                    clickable_elements: clickableElements.length > 0 ? clickableElements : null,
                                    is_clickable: isItemClickable
                                });
                            }
                        }
                    }
                } catch (e) {
                    // Игнорируем ошибки
                }
            });
            
            // УНИВЕРСАЛЬНЫЙ поиск элементов списков по структуре (для сайтов без role="listitem")
            // Ищем повторяющиеся элементы с похожими классами и структурой
            // БЕЗ хардкода конкретных слов - используем только структурный анализ
            try {
                // Оптимизированный поиск: ищем только потенциальные элементы списков
                // Элементы списка обычно имеют одинаковые классы и структуру
                const itemGroups = new Map();
                
                // Ищем элементы, которые могут быть элементами списка (div, li, tr, article, section)
                // Ограничиваем поиск для производительности
                const candidateTags = ['div', 'li', 'tr', 'article', 'section'];
                const allCandidates = [];
                
                candidateTags.forEach(tag => {
                    try {
                        const elements = document.querySelectorAll(tag);
                        elements.forEach(el => {
                            if (el && isVisible(el) && el.offsetWidth >= 50 && el.offsetHeight >= 30) {
                                allCandidates.push(el);
                            }
                        });
                    } catch (e) {
                        // Игнорируем ошибки
                    }
                });
                
                // Группируем элементы по похожим селекторам (элементы списка обычно имеют одинаковые классы)
                allCandidates.forEach(item => {
                    try {
                        const selector = getSelector(item);
                        const baseSelector = selector.split(':')[0]; // Убираем :nth-child и т.д.
                        
                        if (!itemGroups.has(baseSelector)) {
                            itemGroups.set(baseSelector, []);
                        }
                        itemGroups.get(baseSelector).push(item);
                    } catch (e) {
                        // Игнорируем ошибки
                    }
                });
                
                // Обрабатываем группы с 3+ элементами (вероятно это список)
                itemGroups.forEach((items, baseSelector) => {
                    if (items.length >= 3) {
                        // Проверяем, что элементы действительно похожи по структуре
                        const firstItem = items[0];
                        const firstItemStructure = {
                            tag: firstItem.tagName,
                            hasLink: firstItem.querySelector('a[href]') !== null,
                            hasButton: firstItem.querySelector('button, [role="button"]') !== null,
                            textLength: getElementText(firstItem).length
                        };
                        
                        // Проверяем, что большинство элементов имеют похожую структуру
                        let similarCount = 0;
                        items.forEach(item => {
                            const structure = {
                                tag: item.tagName,
                                hasLink: item.querySelector('a[href]') !== null,
                                hasButton: item.querySelector('button, [role="button"]') !== null,
                                textLength: getElementText(item).length
                            };
                            
                            if (structure.tag === firstItemStructure.tag &&
                                structure.hasLink === firstItemStructure.hasLink &&
                                structure.hasButton === firstItemStructure.hasButton &&
                                Math.abs(structure.textLength - firstItemStructure.textLength) < 100) {
                                similarCount++;
                            }
                        });
                        
                        // Если большинство элементов похожи - это список
                        if (similarCount >= Math.max(3, items.length * 0.7)) {
                            items.forEach((item, index) => {
                                try {
                                    if (item && isVisible(item)) {
                                        const itemText = getElementText(item);
                                        const parentContainer = getParentContainerInfo(item);
                                        
                                        // Извлекаем кликабельные элементы внутри
                                        const clickableElements = [];
                                        item.querySelectorAll('a[href], button, [role="button"]').forEach(el => {
                                            if (isVisible(el)) {
                                                const elText = getElementText(el);
                                                clickableElements.push({
                                                    type: el.tagName === 'A' ? 'link' : 'button',
                                                    selector: getSelector(el),
                                                    text: elText,
                                                    href: el.href || null
                                                });
                                            }
                                        });
                                        
                                        const isItemClickable = item.onclick || 
                                                              item.getAttribute('onclick') ||
                                                              item.style.cursor === 'pointer' ||
                                                              item.tagName === 'A' ||
                                                              item.closest('a') !== null ||
                                                              clickableElements.length > 0;
                                        
                                        // Добавляем только если есть текст или кликабельные элементы
                                        if ((itemText && itemText.length > 5) || clickableElements.length > 0 || isItemClickable) {
                                            const existing = elements.find(e => e.selector === getSelector(item));
                                            if (!existing) {
                                                elements.push({
                                                    type: 'list_item',
                                                    selector: getSelector(item),
                                                    text: itemText,
                                                    tag: item.tagName ? item.tagName.toLowerCase() : 'div',
                                                    id: item.id || null,
                                                    classes: Array.from(item.classList || []).slice(0, 3),
                                                    visible: isVisible(item),
                                                    in_modal: isInModal(item),
                                                    in_form: isInForm(item),
                                                    parent_container: parentContainer,
                                                    clickable_elements: clickableElements.length > 0 ? clickableElements : null,
                                                    is_clickable: isItemClickable,
                                                    list_index: index
                                                });
                                            }
                                        }
                                    }
                                } catch (e) {
                                    // Игнорируем ошибки
                                }
                            });
                        }
                    }
                });
            } catch (e) {
                // Игнорируем ошибки при поиске по структуре
            }
            
            return elements;
        }
        """
        
        try:
            elements = await self.page.evaluate(script)
            # Проверка на ошибки в результате
            if not isinstance(elements, list):
                elements = []
        except Exception as e:
            # В случае ошибки возвращаем пустой список
            elements = []
        
        # Приоритизация элементов
        prioritized = self._prioritize_elements(elements)
        
        return prioritized
    
    def _prioritize_elements(self, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Приоритизация элементов по важности
        
        НЕ ограничиваем количество элементов здесь - это делает TokenOptimizer
        Только сортируем по приоритету для лучшей обработки в оптимизаторе
        
        Приоритет:
        1. Элементы в модальных окнах (ВЫСОКИЙ ПРИОРИТЕТ!)
        2. Элементы в формах
        3. Элементы с id
        4. Элементы с уникальными классами
        5. Элементы с текстом
        6. Остальные элементы
        """
        def get_priority(element: Dict[str, Any]) -> int:
            priority = 0
            # КРИТИЧЕСКИ ВАЖНО: Элементы в модальных окнах имеют максимальный приоритет
            if element.get('in_modal'):
                priority += 1000  # Максимальный приоритет для элементов в модальных окнах
            # Элементы в формах также имеют высокий приоритет
            if element.get('in_form'):
                priority += 500
            if element.get('id'):
                priority += 100
            if element.get('classes') and len(element.get('classes', [])) > 0:
                priority += 50
            if element.get('text') and len(element.get('text', '')) > 0:
                priority += 25
            if element.get('type') == 'button':
                priority += 10
            if element.get('type') == 'input':
                priority += 10
            return priority
        
        # Только сортируем, НЕ ограничиваем количество
        # Оптимизатор токенов сам решит, сколько элементов включить
        sorted_elements = sorted(elements, key=get_priority, reverse=True)
        return sorted_elements
    
    async def _extract_visible_text(self) -> str:
        """
        Извлечение видимого текста со страницы
        
        Улучшения:
        - Улучшенная фильтрация скрытых элементов
        - Сохранение структуры текста (переносы строк)
        - Приоритет основному контенту (main, article, section)
        """
        script = """
        () => {
            // Проверка на наличие body
            if (!document.body) return '';
            
            // Функция для проверки видимости элемента
            function isVisible(el) {
                if (!el) return false;
                try {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && 
                           style.visibility !== 'hidden' && 
                           style.opacity !== '0' &&
                           el.offsetWidth > 0 && 
                           el.offsetHeight > 0;
                } catch (e) {
                    return false;
                }
            }
            
            // Удаляем скрытые элементы (создаем копию списка, чтобы не модифицировать во время итерации)
            const hidden = Array.from(document.querySelectorAll('script, style, noscript, [hidden], [style*="display: none"]'));
            hidden.forEach(el => {
                try {
                    if (!isVisible(el)) {
                        el.remove();
                    }
                } catch (e) {
                    // Игнорируем ошибки при удалении
                }
            });
            
            // Пробуем сначала получить текст из основного контента (main, article, section)
            const mainContent = document.querySelector('main, article, [role="main"]');
            if (mainContent && isVisible(mainContent)) {
                const mainText = mainContent.innerText || mainContent.textContent || '';
                if (mainText.trim().length > 100) {
                    // Если основной контент достаточно большой, используем его
                    return mainText.trim();
                }
            }
            
            // Если основного контента нет или он слишком короткий, берем весь body
            // Используем innerText для получения видимого текста с сохранением структуры
            const bodyText = document.body.innerText || document.body.textContent || '';
            
            // Очищаем от лишних пробелов, но сохраняем переносы строк
            return bodyText.trim();
        }
        """
        return await self.page.evaluate(script)
    
    async def _extract_metadata(self) -> Dict[str, Any]:
        """Извлечение метаданных страницы"""
        script = """
        () => {
            return {
                title: document.title,
                url: window.location.href,
                description: document.querySelector('meta[name="description"]')?.content || null,
                viewport: {
                    width: window.innerWidth,
                    height: window.innerHeight
                }
            };
        }
        """
        return await self.page.evaluate(script)
    
    async def get_page_state_hash(self) -> Dict[str, Any]:
        """
        Получение хеша состояния страницы для обнаружения изменений
        
        Returns:
            Словарь с информацией о состоянии страницы (DOM-хеш, количество элементов, модальные окна)
        """
        script = """
        () => {
            // Простой хеш DOM-дерева (количество элементов по типам)
            const interactiveCount = document.querySelectorAll('button, a, input, textarea, select, [role="button"], [role="link"]').length;
            const modalCount = document.querySelectorAll('[role="dialog"], .modal, [class*="modal" i], [class*="dialog" i], [data-qa*="modal" i]').length;
            const formCount = document.querySelectorAll('form, [role="form"]').length;
            
            // Проверяем наличие видимых модальных окон
            const visibleModals = Array.from(document.querySelectorAll('[role="dialog"], .modal, [class*="modal" i], [class*="dialog" i], [data-qa*="modal" i]')).filter(el => {
                try {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && 
                           style.visibility !== 'hidden' && 
                           style.opacity !== '0' &&
                           el.offsetWidth > 0 && 
                           el.offsetHeight > 0;
                } catch (e) {
                    return false;
                }
            }).length;
            
            // Проверяем наличие видимых форм
            const visibleForms = Array.from(document.querySelectorAll('form, [role="form"]')).filter(el => {
                try {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && 
                           style.visibility !== 'hidden' && 
                           style.opacity !== '0' &&
                           el.offsetWidth > 0 && 
                           el.offsetHeight > 0;
                } catch (e) {
                    return false;
                }
            }).length;
            
            // Получаем детальную информацию о видимых модальных окнах
            const modals = [];
            // Расширенный список селекторов для поиска модальных окон
            const modalSelectors = [
                '[role="dialog"]',
                'dialog',
                '[aria-modal="true"]',
                '.modal',
                '[class*="modal" i]',
                '[class*="dialog" i]',
                '[class*="popup" i]',
                '[data-qa*="modal" i]',
                '[data-qa*="dialog" i]',
                '[data-testid*="modal" i]',
                '[data-testid*="dialog" i]'
            ];
            
            const allModals = new Set();
            modalSelectors.forEach(selector => {
                try {
                    document.querySelectorAll(selector).forEach(el => allModals.add(el));
                } catch (e) {}
            });
            
            allModals.forEach(modal => {
                try {
                    const style = window.getComputedStyle(modal);
                    const isVisible = style.display !== 'none' && 
                                     style.visibility !== 'hidden' && 
                                     style.opacity !== '0' &&
                                     modal.offsetWidth > 0 && 
                                     modal.offsetHeight > 0;
                    if (isVisible) {
                        const modalText = modal.innerText?.trim() || modal.textContent?.trim() || '';
                        
                        // Ищем кнопки закрытия
                        const closeButtons = [];
                        const closeSelectors = [
                            'button[aria-label*="закрыть" i]',
                            'button[aria-label*="close" i]',
                            'button[class*="close" i]',
                            '.close-button',
                            '[data-qa*="close" i]',
                            '[data-testid*="close" i]',
                            'button:has(svg[class*="close"])',
                            'button:has(.close-icon)'
                        ];
                        
                        closeSelectors.forEach(sel => {
                            try {
                                const btn = modal.querySelector(sel);
                                if (btn && btn.offsetWidth > 0 && btn.offsetHeight > 0) {
                                    const btnText = btn.innerText?.trim() || btn.getAttribute('aria-label') || '';
                                    if (!closeButtons.some(b => b.selector === getSelector(btn))) {
                                        closeButtons.push({
                                            selector: getSelector(btn),
                                            text: btnText || 'закрыть',
                                            visible: true
                                        });
                                    }
                                }
                            } catch (e) {}
                        });
                        
                        // Получаем все интерактивные элементы внутри модального окна
                        const buttons = Array.from(modal.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]'))
                            .filter(btn => {
                                try {
                                    const btnStyle = window.getComputedStyle(btn);
                                    return btnStyle.display !== 'none' && 
                                           btnStyle.visibility !== 'hidden' && 
                                           btn.offsetWidth > 0 && 
                                           btn.offsetHeight > 0;
                                } catch (e) { return false; }
                            })
                            .slice(0, 10) // Ограничиваем количество
                            .map(btn => ({
                                selector: getSelector(btn),
                                text: getElementText(btn),
                                type: btn.tagName?.toLowerCase() || 'button'
                            }));
                        
                        modals.push({
                            visible: true,
                            text_preview: modalText.substring(0, 200),
                            has_form: modal.querySelector('form') !== null,
                            input_count: modal.querySelectorAll('input, textarea, select').length,
                            close_buttons: closeButtons,
                            buttons: buttons,
                            z_index: parseInt(style.zIndex) || 0
                        });
                    }
                } catch (e) {
                    // Игнорируем ошибки
                }
            });
            
            return {
                interactive_count: interactiveCount,
                modal_count: modalCount,
                visible_modal_count: visibleModals,
                form_count: formCount,
                visible_form_count: visibleForms,
                modals: modals,
                // Простой хеш на основе количества элементов
                dom_hash: `${interactiveCount}-${visibleModals}-${visibleForms}`
            };
        }
        """
        return await self.page.evaluate(script)
    
    async def find_element_by_description(self, description: str) -> Optional[Dict[str, Any]]:
        """
        Поиск элемента по текстовому описанию (использует Playwright locator API как в browser-use)
        
        Args:
            description: Описание элемента (например, "кнопка Войти", "список вакансий", "поле email")
            
        Returns:
            Информация о найденном элементе или None
        """
        # Проверяем кэш
        cache_key = description.lower().strip()
        if cache_key in self._element_cache:
            return self._element_cache[cache_key]
        
        result = await self._find_element_by_description_impl(description)
        
        # Сохраняем в кэш
        self._element_cache[cache_key] = result
        return result
    
    async def _find_element_by_description_impl(self, description: str) -> Optional[Dict[str, Any]]:
        """
        Реализация поиска элемента по описанию
        
        Args:
            description: Описание элемента
            
        Returns:
            Информация о найденном элементе или None
        """
        if self.logger:
            self.logger.info(f"   🔍 Поиск элемента: '{description}'")
        
        # Стратегия 0: Улучшенный поиск - сначала пробуем найти по ключевым словам
        # Извлекаем ключевые слова из описания (убираем стоп-слова)
        description_lower = description.lower()
        stop_words = {'кнопка', 'ссылка', 'поле', 'элемент', 'найти', 'на', 'в', 'и', 'или', 'для', 'с', 'по', 'к', 'от', 'до'}
        key_words = [w for w in description_lower.split() if w not in stop_words and len(w) > 2]
        
        # Стратегия 1: Используем Playwright get_by_text() с fuzzy matching - самый надежный способ
        if self.logger:
            self.logger.info(f"   Стратегия 1: Поиск через get_by_text (описание: '{description}', ключевые слова: {key_words})")
        try:
            # Сначала пробуем точное совпадение
            locator = self.page.get_by_text(description, exact=False)
            count = await locator.count()
            
            # Если не нашли точное совпадение - пробуем по ключевым словам
            if count == 0 and key_words:
                if self.logger:
                    self.logger.info(f"   Стратегия 1.0: Поиск по ключевым словам: {key_words}")
                # Пробуем найти элемент, содержащий хотя бы одно ключевое слово
                for key_word in key_words:
                    try:
                        key_locator = self.page.get_by_text(key_word, exact=False)
                        key_count = await key_locator.count()
                        if key_count > 0:
                            # Проверяем элементы, содержащие это ключевое слово
                            for i in range(min(key_count, 15)):
                                try:
                                    element_locator = key_locator.nth(i)
                                    if await element_locator.is_visible():
                                        element_text = (await element_locator.text_content() or "").lower()
                                        # Проверяем, содержит ли элемент несколько ключевых слов
                                        matched_words = sum(1 for kw in key_words if kw in element_text)
                                        if matched_words >= max(1, len(key_words) // 2):  # Хотя бы половина ключевых слов
                                            selector = await self._get_selector_for_locator(element_locator)
                                            tag_name = await element_locator.evaluate("el => el.tagName.toLowerCase()") or "unknown"
                                            if self.logger:
                                                self.logger.success(f"   ✓ Найден элемент по ключевому слову '{key_word}' (стратегия 1.0): {selector}, текст: '{element_text[:50]}...'")
                                            return {
                                                "selector": selector,
                                                "text": element_text,
                                                "type": tag_name,
                                                "search_text": description
                                            }
                                except:
                                    continue
                    except:
                        continue
            
            if count > 0:
                # Ищем первый видимый элемент не из footer/header
                for i in range(min(count, 20)):
                    try:
                        element_locator = locator.nth(i)
                        is_visible = await element_locator.is_visible()
                        
                        if is_visible:
                            # Быстрая проверка footer/header через evaluate
                            is_in_footer_header = await element_locator.evaluate("""
                            (el) => {
                                let parent = el.parentElement;
                                let depth = 0;
                                while (parent && depth < 10) {
                                    const tagName = parent.tagName?.toLowerCase();
                                    const id = parent.id?.toLowerCase() || '';
                                    const className = parent.className?.toLowerCase() || '';
                                    if (tagName === 'footer' || tagName === 'header' || 
                                        id.includes('footer') || id.includes('header') ||
                                        className.includes('footer') || className.includes('header')) {
                                        return true;
                                    }
                                    parent = parent.parentElement;
                                    depth++;
                                }
                                return false;
                            }
                            """)
                            
                            if not is_in_footer_header:
                                # Проверяем, находится ли элемент в основном контенте (main, article, section, или не в aside/sidebar)
                                is_in_main_content = await element_locator.evaluate("""
                                (el) => {
                                    let parent = el.parentElement;
                                    let depth = 0;
                                    while (parent && depth < 15) {
                                        const tagName = parent.tagName?.toLowerCase();
                                        const id = parent.id?.toLowerCase() || '';
                                        const className = parent.className?.toLowerCase() || '';
                                        if (tagName === 'main' || tagName === 'article' || tagName === 'section' || 
                                            id.includes('content') || id.includes('main')) {
                                            return true;
                                        }
                                        if (tagName === 'aside' || id.includes('sidebar') || className.includes('sidebar')) {
                                            return false;
                                        }
                                        parent = parent.parentElement;
                                        depth++;
                                    }
                                    return true; // По умолчанию считаем, что в основном контенте
                                }
                                """)
                                
                                if is_in_main_content:
                                    selector = await self._get_selector_for_locator(element_locator)
                                    text = await element_locator.text_content() or ""
                                    tag_name = await element_locator.evaluate("el => el.tagName.toLowerCase()") or "unknown"
                                    # Сохраняем текст для альтернативного клика, если селектор слишком общий
                                    if self.logger:
                                        self.logger.success(f"   ✓ Найден элемент в основном контенте (стратегия 1): {selector}, текст: '{text[:50]}...'")
                                    return {
                                        "selector": selector,
                                        "text": text,
                                        "type": tag_name,
                                        "search_text": description  # Сохраняем текст для поиска
                                    }
                    except:
                        continue
                
                # Если не нашли не из footer/header - возвращаем первый видимый
                for i in range(min(count, 10)):
                    try:
                        element_locator = locator.nth(i)
                        if await element_locator.is_visible():
                            selector = await self._get_selector_for_locator(element_locator)
                            text = await element_locator.text_content() or ""
                            tag_name = await element_locator.evaluate("el => el.tagName.toLowerCase()") or "unknown"
                            if self.logger:
                                self.logger.success(f"   ✓ Найден элемент (стратегия 1, fallback): {selector}, текст: '{text[:50]}...'")
                            return {
                                "selector": selector,
                                "text": text,
                                "type": tag_name,
                                "search_text": description
                            }
                    except:
                        continue
            
            # Если точное совпадение не найдено - пробуем fuzzy matching по словам
            if self.logger:
                self.logger.info("   Стратегия 1.1: Fuzzy matching по словам")
            description_words = description.lower().split()
            if len(description_words) > 1:
                # Пробуем найти элемент, содержащий все ключевые слова
                for word in description_words:
                    if len(word) < 3:  # Пропускаем короткие слова
                        continue
                    try:
                        word_locator = self.page.get_by_text(word, exact=False)
                        word_count = await word_locator.count()
                        if word_count > 0:
                            # Проверяем элементы, содержащие это слово
                            for i in range(min(word_count, 10)):
                                try:
                                    element_locator = word_locator.nth(i)
                                    if await element_locator.is_visible():
                                        element_text = (await element_locator.text_content() or "").lower()
                                        # Проверяем, содержит ли элемент все ключевые слова
                                        if all(word in element_text for word in description_words if len(word) >= 3):
                                            selector = await self._get_selector_for_locator(element_locator)
                                            tag_name = await element_locator.evaluate("el => el.tagName.toLowerCase()") or "unknown"
                                            if self.logger:
                                                self.logger.success(f"   ✓ Найден элемент (fuzzy matching): {selector}, текст: '{element_text[:50]}...'")
                                            return {
                                                "selector": selector,
                                                "text": element_text,
                                                "type": tag_name,
                                                "search_text": description
                                            }
                                except:
                                    continue
                    except:
                        continue
        except Exception as e:
            pass
        
        # Стратегия 2: Ищем по role (для кнопок, ссылок) - как в browser-use
        if self.logger:
            self.logger.info("   Стратегия 2: Поиск по role (button, link)")
        try:
            # Пробуем разные роли
            for role in ["button", "link"]:
                try:
                    role_locator = self.page.get_by_role(role, name=description, exact=False)
                    count = await role_locator.count()
                    if count > 0:
                        for i in range(min(count, 15)):
                            try:
                                element_locator = role_locator.nth(i)
                                if await element_locator.is_visible():
                                    selector = await self._get_selector_for_locator(element_locator)
                                    text = await element_locator.text_content() or ""
                                    if self.logger:
                                        self.logger.success(f"   ✓ Найден элемент (стратегия 2, role={role}): {selector}, текст: '{text[:50]}...'")
                                    return {
                                        "selector": selector,
                                        "text": text,
                                        "type": role,
                                        "search_text": description
                                    }
                            except:
                                continue
                    
                    # Если не нашли по полному описанию - пробуем по ключевым словам
                    if count == 0 and key_words:
                        for key_word in key_words:
                            try:
                                key_role_locator = self.page.get_by_role(role, name=key_word, exact=False)
                                key_count = await key_role_locator.count()
                                if key_count > 0:
                                    for i in range(min(key_count, 10)):
                                        try:
                                            element_locator = key_role_locator.nth(i)
                                            if await element_locator.is_visible():
                                                element_text = (await element_locator.text_content() or "").lower()
                                                # Проверяем, содержит ли элемент другие ключевые слова
                                                matched_words = sum(1 for kw in key_words if kw in element_text)
                                                if matched_words >= max(1, len(key_words) // 2):
                                                    selector = await self._get_selector_for_locator(element_locator)
                                                    if self.logger:
                                                        self.logger.success(f"   ✓ Найден элемент (стратегия 2, role={role}, ключевое слово '{key_word}'): {selector}, текст: '{element_text[:50]}...'")
                                                    return {
                                                        "selector": selector,
                                                        "text": element_text,
                                                        "type": role,
                                                        "search_text": description
                                                    }
                                        except:
                                            continue
                            except:
                                continue
                except:
                    continue
        except:
            pass
        
        # Стратегия 3: Ищем по aria-label
        if self.logger:
            self.logger.info("   Стратегия 3: Поиск по aria-label")
        try:
            aria_locator = self.page.locator(f'[aria-label*="{description}"]')
            count = await aria_locator.count()
            if count > 0:
                for i in range(min(count, 10)):
                    try:
                        element_locator = aria_locator.nth(i)
                        if await element_locator.is_visible():
                            selector = await self._get_selector_for_locator(element_locator)
                            text = await element_locator.get_attribute("aria-label") or ""
                            tag_name = await element_locator.evaluate("el => el.tagName.toLowerCase()") or "unknown"
                            if self.logger:
                                self.logger.success(f"   ✓ Найден элемент (стратегия 3): {selector}, aria-label: '{text[:50]}...'")
                            return {
                                "selector": selector,
                                "text": text,
                                "type": tag_name,
                                "search_text": description
                            }
                    except:
                        continue
        except:
            pass
        
        # Стратегия 4: Поиск по изображениям и alt-тексту (для карточек ресторанов, товаров и т.д.)
        if self.logger:
            self.logger.info("   Стратегия 4: Поиск по изображениям и alt-тексту")
        try:
            # Ищем изображения с alt-текстом, содержащим описание
            img_locator = self.page.locator(f'img[alt*="{description}"]')
            img_count = await img_locator.count()
            if img_count > 0:
                for i in range(min(img_count, 10)):
                    try:
                        img_element = img_locator.nth(i)
                        if await img_element.is_visible():
                            # Ищем родительский кликабельный элемент (карточку)
                            clickable_parent = await img_element.evaluate("""
                            (img) => {
                                let parent = img.parentElement;
                                let depth = 0;
                                while (parent && depth < 5) {
                                    // Проверяем, является ли родитель кликабельным
                                    if (parent.tagName === 'A' || parent.tagName === 'BUTTON' || 
                                        parent.onclick || parent.getAttribute('onclick') ||
                                        parent.style.cursor === 'pointer' ||
                                        parent.classList.contains('card') || parent.classList.contains('item') ||
                                        parent.getAttribute('role') === 'button' || parent.getAttribute('role') === 'link') {
                                        return parent;
                                    }
                                    parent = parent.parentElement;
                                    depth++;
                                }
                                return img.parentElement; // Возвращаем ближайшего родителя
                            }
                            """)
                            if clickable_parent:
                                selector = await self._get_selector_for_locator(img_element)
                                alt_text = await img_element.get_attribute("alt") or ""
                                if self.logger:
                                    self.logger.success(f"   ✓ Найден элемент (стратегия 4): {selector}, alt: '{alt_text[:50]}...'")
                                return {
                                    "selector": selector,
                                    "text": alt_text,
                                    "type": "image_link",
                                    "search_text": description
                                }
                    except:
                        continue
        except:
            pass
        
        # Стратегия 5: Поиск в динамически загружаемых элементах (после прокрутки)
        # Прокручиваем страницу вниз и ищем элемент
        if self.logger:
            self.logger.info("   Стратегия 5: Поиск в динамических элементах (после прокрутки)")
        try:
            # Прокручиваем немного вниз
            await self.page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(0.5)  # Ждем загрузки динамического контента
            
            # Повторяем поиск после прокрутки
            scroll_locator = self.page.get_by_text(description, exact=False)
            scroll_count = await scroll_locator.count()
            if scroll_count > 0:
                for i in range(min(scroll_count, 5)):
                    try:
                        element_locator = scroll_locator.nth(i)
                        if await element_locator.is_visible():
                            selector = await self._get_selector_for_locator(element_locator)
                            text = await element_locator.text_content() or ""
                            tag_name = await element_locator.evaluate("el => el.tagName.toLowerCase()") or "unknown"
                            if self.logger:
                                self.logger.success(f"   ✓ Найден элемент (стратегия 5): {selector}, текст: '{text[:50]}...'")
                            return {
                                "selector": selector,
                                "text": text,
                                "type": tag_name,
                                "search_text": description
                            }
                    except:
                        continue
        except:
            pass
        
        # Стратегия 6: Fallback - используем поиск в интерактивных элементах
        # Используем уже извлеченные элементы (если они есть в кэше)
        if self.logger:
            self.logger.info("   Стратегия 6: Поиск в интерактивных элементах")
        interactive_elements = await self._extract_interactive_elements(include_text=False)
        description_lower = description.lower()
        
        # Ищем точное или частичное совпадение - УЛУЧШЕННАЯ ЛОГИКА
        matches = []
        
        for element in interactive_elements:
            element_text = element.get('text', '').lower()
            score = 0
            
            # Точное совпадение - максимальный приоритет
            if description_lower == element_text:
                score = 100
            # Описание содержится в тексте элемента
            elif description_lower in element_text:
                score = 50 + len(description_lower) / len(element_text) * 30
            # Текст элемента содержится в описании
            elif element_text in description_lower and len(element_text) > 3:
                score = 30
            # Частичное совпадение по ключевым словам
            else:
                desc_words = set(description_lower.split())
                elem_words = set(element_text.split())
                common_words = desc_words & elem_words
                if len(common_words) > 0 and len(desc_words) > 0:
                    score = len(common_words) / len(desc_words) * 20
            
            if score > 20:  # Снижен порог для большего количества вариантов
                matches.append({
                    "element": element,
                    "score": score
                })
        
        # Сортируем по score и приоритету (элементы в основном контенте выше)
        matches.sort(key=lambda x: x["score"], reverse=True)
        
        # Если есть несколько совпадений - логируем все для отладки
        if len(matches) > 1 and self.logger:
            self.logger.warning(f"   ⚠️ Найдено {len(matches)} элементов с текстом '{description}':")
            for i, match in enumerate(matches[:5], 1):
                elem = match["element"]
                self.logger.info(f"      {i}. Score: {match['score']:.1f}, Текст: '{elem.get('text', '')[:50]}...', Селектор: {elem.get('selector', 'N/A')}")
        
        # Возвращаем лучший матч
        if matches:
            best_match = matches[0]["element"]
            best_score = matches[0]["score"]
            if self.logger:
                self.logger.success(f"   ✓ Найден элемент (стратегия 6): {best_match.get('selector')}, текст: '{best_match.get('text', '')[:50]}...', score: {best_score:.1f}")
            best_match["search_text"] = description
            # Добавляем информацию о других вариантах для контекста
            if len(matches) > 1:
                best_match["alternative_matches"] = len(matches) - 1
            return best_match
        
        # Стратегия 7: Fallback - используем старый JavaScript поиск (только если ничего не нашли)
        # Ограничиваем количество проверяемых элементов для производительности
        if self.logger:
            self.logger.info("   Стратегия 7: JavaScript поиск (fallback)")
        result = await self.find_element_by_description_old(description)
        if result and self.logger:
            self.logger.success(f"   ✓ Найден элемент (стратегия 7): {result.get('selector')}, текст: '{result.get('text', '')[:50]}...'")
        elif self.logger:
            self.logger.warning(f"   ✗ Элемент '{description}' не найден ни одной стратегией")
        return result
        
    async def _get_selector_for_locator(self, locator) -> str:
        """Генерация селектора для Playwright locator"""
        try:
            selector = await locator.evaluate("""
            (el) => {
                if (el.id) return '#' + el.id;
                if (el.className && typeof el.className === 'string') {
                    const classes = el.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                    if (classes.length > 0) {
                        return el.tagName.toLowerCase() + '.' + classes.join('.');
                    }
                }
                // Если нет id и классов - пытаемся создать более специфичный селектор
                // Используем data-атрибуты или текст для уникальности
                const tagName = el.tagName ? el.tagName.toLowerCase() : 'div';
                const dataQa = el.getAttribute('data-qa');
                const dataTestid = el.getAttribute('data-testid');
                const ariaLabel = el.getAttribute('aria-label');
                const href = el.getAttribute('href');
                
                if (dataQa) return `[data-qa="${dataQa}"]`;
                if (dataTestid) return `[data-testid="${dataTestid}"]`;
                if (ariaLabel) return `${tagName}[aria-label="${ariaLabel}"]`;
                if (href && tagName === 'a') return `a[href="${href}"]`;
                
                // Если ничего не помогло - возвращаем tagName, но это плохой селектор
                return tagName;
            }
            """)
            return selector
        except:
            # Fallback - используем Playwright locator как селектор
            return str(locator)
    
    async def find_element_by_description_old(self, description: str) -> Optional[Dict[str, Any]]:
        """
        Старый метод поиска (fallback) - если не нашли через Playwright locator
        """
        # Если не нашли в интерактивных - ищем в релевантных элементах (оптимизированно)
        script = """
        (description) => {
            const desc = description.toLowerCase();
            const descWords = desc.split(' ').filter(w => w.length > 2);
            
            // ОПТИМИЗАЦИЯ: Используем умные селекторы вместо querySelectorAll('*')
            // Ищем только в релевантных элементах для производительности
            // УНИВЕРСАЛЬНО: Используем только стандартные HTML5 семантические элементы и атрибуты
            const relevantSelectors = [
                // Семантические HTML5 элементы (универсальные)
                'main', 'article', 'section', 'aside', 'nav', 'header', 'footer',
                // Списки (стандартные HTML элементы)
                'ul', 'ol', 'dl', '[role="list"]',
                // Таблицы (стандартные HTML элементы)
                'table', '[role="table"]',
                // Элементы с data-атрибутами (стандарт для тестирования и разметки)
                '[data-testid]', '[data-qa]', '[data-id]', '[data-test]',
                // Элементы с aria-атрибутами (стандарт доступности)
                '[aria-label]', '[aria-labelledby]', '[aria-describedby]',
                // Элементы с role атрибутами (стандарт ARIA)
                '[role="article"]', '[role="region"]', '[role="contentinfo"]',
                '[role="main"]', '[role="complementary"]', '[role="navigation"]'
            ];
            
            // Функция для быстрой проверки видимости (без getComputedStyle)
            function isQuickVisible(el) {
                if (!el) return false;
                try {
                    // Быстрая проверка без дорогого getComputedStyle
                    return el.offsetWidth > 0 && el.offsetHeight > 0 && 
                           el.style.display !== 'none' && 
                           el.style.visibility !== 'hidden';
                } catch (e) {
                    return false;
                }
            }
            
            // Функция для полной проверки видимости (с getComputedStyle)
            function isFullyVisible(el) {
                if (!el) return false;
                try {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
                    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
                    
                    // Проверка стилей
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    
                    // Проверка размеров
                    if (rect.width === 0 || rect.height === 0) {
                        return false;
                    }
                    
                    // Проверка попадания в viewport (хотя бы частично)
                    const isInViewport = rect.top < viewportHeight && rect.bottom > 0 && 
                                        rect.left < viewportWidth && rect.right > 0;
                    
                    return isInViewport;
                } catch (e) {
                    return false;
                }
            }
            
            // Функция для проверки, находится ли элемент в footer/header (штраф)
            function isInFooterOrHeader(el) {
                if (!el) return false;
                try {
                    let parent = el.parentElement;
                    let depth = 0;
                    while (parent && depth < 10) {
                        const tagName = parent.tagName?.toLowerCase();
                        const id = parent.id?.toLowerCase() || '';
                        const className = parent.className?.toLowerCase() || '';
                        
                        if (tagName === 'footer' || tagName === 'header' || 
                            id.includes('footer') || id.includes('header') ||
                            className.includes('footer') || className.includes('header')) {
                            return true;
                        }
                        parent = parent.parentElement;
                        depth++;
                    }
                    return false;
                } catch (e) {
                    return false;
                }
            }
            
            // Функция для проверки, находится ли элемент в основном контенте (бонус)
            function isInMainContent(el) {
                if (!el) return false;
                try {
                    let parent = el.parentElement;
                    let depth = 0;
                    while (parent && depth < 10) {
                        const tagName = parent.tagName?.toLowerCase();
                        const role = parent.getAttribute('role');
                        const id = parent.id?.toLowerCase() || '';
                        const className = parent.className?.toLowerCase() || '';
                        
                        if (tagName === 'main' || role === 'main' || 
                            id.includes('main') || id.includes('content') ||
                            className.includes('main') || className.includes('content')) {
                            return true;
                        }
                        parent = parent.parentElement;
                        depth++;
                    }
                    return false;
                } catch (e) {
                    return false;
                }
            }
            
            // Функция для генерации селектора
            function getSelector(el) {
                if (!el) return 'unknown';
                if (el.id) return '#' + el.id;
                if (el.className && typeof el.className === 'string') {
                    const classes = el.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                    if (classes.length > 0) {
                        const tagName = el.tagName ? el.tagName.toLowerCase() : 'div';
                        return tagName + '.' + classes.join('.');
                    }
                }
                return el.tagName ? el.tagName.toLowerCase() : 'div';
            }
            
            // Функция для получения текста элемента (оптимизированно)
            function getElementText(el) {
                if (!el) return '';
                try {
                    // Используем textContent вместо innerText для производительности
                    // innerText требует layout calculation, textContent быстрее
                    const text = el.textContent?.trim() || '';
                    return text.substring(0, 200);
                } catch (e) {
                    return '';
                }
            }
            
            const candidates = [];
            const maxCandidates = 30; // Ограничиваем количество кандидатов (уменьшено с 50)
            let checkedCount = 0;
            const maxCheckCount = 300; // Максимум элементов для проверки (уменьшено с 500 для производительности)
            
            // Ищем в релевантных селекторах
            for (const selector of relevantSelectors) {
                if (candidates.length >= maxCandidates || checkedCount >= maxCheckCount) break;
                
                try {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        if (checkedCount >= maxCheckCount) break;
                        checkedCount++;
                        
                        // Быстрая проверка видимости
                        if (!isQuickVisible(el)) continue;
                        
                        // Быстрая проверка текста без полного извлечения
                        const text = el.textContent?.toLowerCase() || '';
                        const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
                        const title = (el.getAttribute('title') || '').toLowerCase();
                        const className = (el.className || '').toLowerCase();
                        const id = (el.id || '').toLowerCase();
                        
                        // Быстрая предварительная проверка
                        let hasMatch = false;
                        if (text.includes(desc) || ariaLabel.includes(desc) || title.includes(desc)) {
                            hasMatch = true;
                        } else {
                            // Проверяем только первые слова для производительности
                            for (let i = 0; i < Math.min(descWords.length, 3); i++) {
                                const word = descWords[i];
                                if (text.includes(word) || className.includes(word) || id.includes(word)) {
                                    hasMatch = true;
                                    break;
                                }
                            }
                        }
                        
                        if (!hasMatch) continue;
                        
                        // Только если есть совпадение - вычисляем полный score
                        let score = 0;
                        
                        // Точное совпадение описания
                        if (text.includes(desc) || ariaLabel.includes(desc) || title.includes(desc)) {
                            score += 100;
                            // Ранний выход для точного совпадения
                            const best = {
                                type: el.tagName ? el.tagName.toLowerCase() : 'div',
                                selector: getSelector(el),
                                text: getElementText(el),
                                id: el.id || null,
                                classes: Array.from(el.classList || []).slice(0, 3),
                                score: score
                            };
                            return best;
                        }
                        
                        // Совпадение по словам
                        for (const word of descWords) {
                            if (text.includes(word)) score += 10;
                            if (className.includes(word)) score += 5;
                            if (id.includes(word)) score += 8;
                        }
                        
                        // Бонус за релевантные HTML элементы (универсально, без привязки к языку)
                        // Используем только стандартные HTML теги и атрибуты
                        if (el.tagName === 'UL' || el.tagName === 'OL' || el.tagName === 'DL') {
                            score += 10; // Списки - универсальная структура
                        }
                        if (el.getAttribute('role') === 'list') {
                            score += 10; // ARIA role для списков
                        }
                        // Бонус за элементы с релевантными data-атрибутами
                        if (el.hasAttribute('data-testid') || el.hasAttribute('data-qa')) {
                            score += 5; // Элементы с тестовыми атрибутами часто важны
                        }
                        
                        // ШТРАФ за элементы в footer/header (часто невидимы или нерелевантны)
                        const inFooterHeader = isInFooterOrHeader(el);
                        if (inFooterHeader) {
                            score -= 30; // Сильный штраф
                        }
                        
                        // БОНУС за элементы в основном контенте
                        const inMainContent = isInMainContent(el);
                        if (inMainContent) {
                            score += 20; // Бонус за релевантность
                        }
                        
                        // Проверка полной видимости (только для финального выбора)
                        const fullyVisible = isFullyVisible(el);
                        if (!fullyVisible) {
                            score -= 15; // Штраф за невидимые элементы
                        }
                        
                        if (score >= 10) {
                            candidates.push({
                                score: score,
                                text: getElementText(el),
                                selector: getSelector(el),
                                tag: el.tagName ? el.tagName.toLowerCase() : 'div',
                                id: el.id || null,
                                classes: Array.from(el.classList || []).slice(0, 3),
                                fullyVisible: fullyVisible,
                                inMainContent: inMainContent,
                                inFooterHeader: inFooterHeader
                            });
                        }
                    }
                } catch (e) {
                    // Игнорируем ошибки селекторов
                    continue;
                }
            }
            
            // Если не нашли в релевантных селекторах - пробуем ограниченный поиск по всему DOM
            if (candidates.length === 0 && checkedCount < maxCheckCount) {
                // Ищем только элементы с текстом или атрибутами (более эффективно)
                const textElements = document.querySelectorAll('*');
                const remainingChecks = maxCheckCount - checkedCount;
                
                for (let i = 0; i < Math.min(textElements.length, remainingChecks); i++) {
                    const el = textElements[i];
                    checkedCount++;
                    
                    if (!isQuickVisible(el)) continue;
                    
                    // Быстрая проверка без полного извлечения текста
                    const text = el.textContent?.toLowerCase() || '';
                    if (text.length === 0) continue; // Пропускаем элементы без текста
                    
                    // Проверяем только первые слова описания
                    let hasMatch = false;
                    for (let j = 0; j < Math.min(descWords.length, 2); j++) {
                        if (text.includes(descWords[j])) {
                            hasMatch = true;
                            break;
                        }
                    }
                    
                    if (!hasMatch) continue;
                    
                    // Вычисляем score только для подходящих элементов
                    let score = 0;
                    if (text.includes(desc)) {
                        score += 100;
                    }
                    for (const word of descWords) {
                        if (text.includes(word)) score += 10;
                    }
                    
                    // Проверка видимости и расположения
                    const fullyVisible = isFullyVisible(el);
                    const inFooterHeader = isInFooterOrHeader(el);
                    const inMainContent = isInMainContent(el);
                    
                    // Штрафы и бонусы
                    if (inFooterHeader) score -= 30;
                    if (inMainContent) score += 20;
                    if (!fullyVisible) score -= 15;
                    
                    if (score >= 10) {
                        candidates.push({
                            score: score,
                            text: getElementText(el),
                            selector: getSelector(el),
                            tag: el.tagName ? el.tagName.toLowerCase() : 'div',
                            id: el.id || null,
                            classes: Array.from(el.classList || []).slice(0, 3),
                            fullyVisible: fullyVisible,
                            inMainContent: inMainContent,
                            inFooterHeader: inFooterHeader
                        });
                    }
                }
            }
            
            // Сортируем и возвращаем лучший результат с проверкой видимости
            if (candidates.length > 0) {
                // Сортируем по score
                candidates.sort((a, b) => {
                    // Сначала по score
                    if (b.score !== a.score) return b.score - a.score;
                    // Затем приоритет видимым элементам
                    if (a.fullyVisible !== b.fullyVisible) return a.fullyVisible ? 1 : -1;
                    // Затем приоритет элементам в основном контенте
                    if (a.inMainContent !== b.inMainContent) return a.inMainContent ? 1 : -1;
                    // Штраф элементам в footer/header
                    if (a.inFooterHeader !== b.inFooterHeader) return a.inFooterHeader ? -1 : 1;
                    return 0;
                });
                
                // Возвращаем лучший видимый элемент, если есть
                for (const candidate of candidates) {
                    if (candidate.fullyVisible && !candidate.inFooterHeader) {
                        return {
                            type: candidate.tag,
                            selector: candidate.selector,
                            text: candidate.text,
                            id: candidate.id,
                            classes: candidate.classes,
                            score: candidate.score
                        };
                    }
                }
                
                // Если нет видимых - возвращаем лучший по score
                return {
                    type: candidates[0].tag,
                    selector: candidates[0].selector,
                    text: candidates[0].text,
                    id: candidates[0].id,
                    classes: candidates[0].classes,
                    score: candidates[0].score
                };
            }
            
            return null;
        }
        """
        
        try:
            result = await self.page.evaluate(script, description)
            if result:
                result["search_text"] = description
            return result
        except Exception as e:
            # В случае ошибки возвращаем None
            return None
    
    async def get_element_selector(self, description: str) -> Optional[str]:
        """
        Получение селектора элемента по описанию
        
        Args:
            description: Описание элемента
            
        Returns:
            CSS селектор или None
        """
        element = await self.find_element_by_description(description)
        return element.get('selector') if element else None
    
    async def get_visible_modals_info(self) -> List[Dict[str, Any]]:
        """
        Получение детальной информации о видимых модальных окнах
        
        Returns:
            Список словарей с информацией о видимых модальных окнах
        """
        script = """
        () => {
            // Функция для генерации селектора
            function getSelector(el) {
                if (!el) return 'unknown';
                if (el.id) return '#' + el.id;
                if (el.className && typeof el.className === 'string') {
                    const classes = el.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                    if (classes.length > 0) {
                        const tagName = el.tagName ? el.tagName.toLowerCase() : 'div';
                        return tagName + '.' + classes.join('.');
                    }
                }
                return el.tagName ? el.tagName.toLowerCase() : 'div';
            }
            
            // Функция для получения текста элемента
            function getElementText(element) {
                if (!element) return '';
                const ariaLabel = element.getAttribute('aria-label');
                if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
                const text = element.innerText?.trim() || element.textContent?.trim() || '';
                return text.substring(0, 150);
            }
            
            const modals = [];
            const modalSelectors = [
                '[role="dialog"]',
                'dialog',
                '[aria-modal="true"]',
                '.modal',
                '[class*="modal" i]',
                '[class*="dialog" i]',
                '[class*="popup" i]',
                '[data-qa*="modal" i]',
                '[data-qa*="dialog" i]',
                '[data-testid*="modal" i]',
                '[data-testid*="dialog" i]'
            ];
            
            const allModals = new Set();
            modalSelectors.forEach(selector => {
                try {
                    document.querySelectorAll(selector).forEach(el => allModals.add(el));
                } catch (e) {}
            });
            
            allModals.forEach(modal => {
                try {
                    const style = window.getComputedStyle(modal);
                    const isVisible = style.display !== 'none' && 
                                     style.visibility !== 'hidden' && 
                                     style.opacity !== '0' &&
                                     modal.offsetWidth > 0 && 
                                     modal.offsetHeight > 0;
                    if (isVisible) {
                        const modalText = modal.innerText?.trim() || modal.textContent?.trim() || '';
                        
                        // Ищем кнопки закрытия
                        const closeButtons = [];
                        const closeSelectors = [
                            'button[aria-label*="закрыть" i]',
                            'button[aria-label*="close" i]',
                            'button[class*="close" i]',
                            '.close-button',
                            '[data-qa*="close" i]',
                            '[data-testid*="close" i]'
                        ];
                        
                        closeSelectors.forEach(sel => {
                            try {
                                const btn = modal.querySelector(sel);
                                if (btn && btn.offsetWidth > 0 && btn.offsetHeight > 0) {
                                    const btnText = btn.innerText?.trim() || btn.getAttribute('aria-label') || '';
                                    if (!closeButtons.some(b => b.selector === getSelector(btn))) {
                                        closeButtons.push({
                                            selector: getSelector(btn),
                                            text: btnText || 'закрыть',
                                            visible: true
                                        });
                                    }
                                }
                            } catch (e) {}
                        });
                        
                        modals.push({
                            selector: getSelector(modal),
                            text_preview: modalText.substring(0, 200),
                            has_form: modal.querySelector('form') !== null,
                            input_count: modal.querySelectorAll('input, textarea, select').length,
                            close_buttons: closeButtons,
                            z_index: parseInt(style.zIndex) || 0
                        });
                    }
                } catch (e) {}
            });
            
            return modals;
        }
        """
        try:
            return await self.page.evaluate(script)
        except Exception as e:
            return []
    
    async def _extract_page_structure(self) -> Dict[str, Any]:
        """
        Извлечение структуры страницы для понимания местоположения
        
        Returns:
            Словарь с информацией о структуре страницы
        """
        script = """
        () => {
            const structure = {
                breadcrumbs: [],
                current_section: null,
                visible_modals: [],
                page_focus: null
            };
            
            // Извлекаем breadcrumbs
            const breadcrumbSelectors = [
                '[role="navigation"][aria-label*="breadcrumb" i]',
                '.breadcrumb',
                '[class*="breadcrumb" i]',
                'nav[aria-label*="breadcrumb" i]',
                'ol[class*="breadcrumb" i]',
                'ul[class*="breadcrumb" i]'
            ];
            
            for (const selector of breadcrumbSelectors) {
                try {
                    const breadcrumb = document.querySelector(selector);
                    if (breadcrumb) {
                        const links = Array.from(breadcrumb.querySelectorAll('a, [role="link"]'))
                            .filter(link => {
                                try {
                                    const style = window.getComputedStyle(link);
                                    return style.display !== 'none' && 
                                           style.visibility !== 'hidden' &&
                                           link.offsetWidth > 0;
                                } catch (e) { return false; }
                            })
                            .slice(0, 10)
                            .map(link => ({
                                text: (link.innerText || link.textContent || '').trim(),
                                href: link.href || link.getAttribute('href') || ''
                            }));
                        if (links.length > 0) {
                            structure.breadcrumbs = links;
                            break;
                        }
                    }
                } catch (e) {}
            }
            
            // Определяем текущую секцию страницы
            const mainContent = document.querySelector('main, article, [role="main"]');
            if (mainContent) {
                const style = window.getComputedStyle(mainContent);
                if (style.display !== 'none' && mainContent.offsetWidth > 0) {
                    const mainText = (mainContent.innerText || mainContent.textContent || '').trim();
                    structure.current_section = {
                        type: mainContent.tagName?.toLowerCase() || 'main',
                        text_preview: mainText.substring(0, 300)
                    };
                }
            }
            
            // Информация о видимых модальных окнах (упрощенная версия)
            const modalSelectors = [
                '[role="dialog"]',
                'dialog',
                '[aria-modal="true"]',
                '.modal',
                '[class*="modal" i]',
                '[class*="dialog" i]'
            ];
            
            const allModals = new Set();
            modalSelectors.forEach(selector => {
                try {
                    document.querySelectorAll(selector).forEach(el => allModals.add(el));
                } catch (e) {}
            });
            
            allModals.forEach(modal => {
                try {
                    const style = window.getComputedStyle(modal);
                    const isVisible = style.display !== 'none' && 
                                     style.visibility !== 'hidden' && 
                                     style.opacity !== '0' &&
                                     modal.offsetWidth > 0 && 
                                     modal.offsetHeight > 0;
                    if (isVisible) {
                        const modalText = modal.innerText?.trim() || modal.textContent?.trim() || '';
                        structure.visible_modals.push({
                            text_preview: modalText.substring(0, 150),
                            has_form: modal.querySelector('form') !== null,
                            input_count: modal.querySelectorAll('input, textarea, select').length
                        });
                    }
                } catch (e) {}
            });
            
            // Определяем фокус страницы (активный элемент или область)
            const activeElement = document.activeElement;
            if (activeElement && activeElement !== document.body) {
                const activeText = (activeElement.innerText || activeElement.textContent || '').trim();
                structure.page_focus = {
                    tag: activeElement.tagName?.toLowerCase() || 'unknown',
                    text_preview: activeText.substring(0, 100)
                };
            }
            
            return structure;
        }
        """
        try:
            return await self.page.evaluate(script)
        except Exception as e:
            return {
                "breadcrumbs": [],
                "current_section": None,
                "visible_modals": [],
                "page_focus": None
            }
    
    async def _extract_location_context(self) -> Dict[str, Any]:
        """
        Извлечение контекста местоположения для агента
        
        Returns:
            Словарь с контекстной информацией о местоположении
        """
        structure = await self._extract_page_structure()
        page_state = await self.get_page_state_hash()
        
        # Формируем краткое описание текущего состояния
        location_description = []
        
        # Добавляем информацию о breadcrumbs
        if structure.get("breadcrumbs"):
            breadcrumbs_text = " > ".join([b.get("text", "") for b in structure["breadcrumbs"]])
            location_description.append(f"Навигация: {breadcrumbs_text}")
        
        # Добавляем информацию о текущей секции
        if structure.get("current_section"):
            section = structure["current_section"]
            location_description.append(f"Текущая секция: {section.get('type', 'main')}")
        
        # Добавляем информацию о модальных окнах
        visible_modals_count = page_state.get("visible_modal_count", 0)
        if visible_modals_count > 0:
            modals_info = page_state.get("modals", [])
            modal_descriptions = []
            for modal in modals_info[:2]:  # Максимум 2 модальных окна
                desc = "модальное окно"
                if modal.get("has_form"):
                    desc += " с формой"
                if modal.get("input_count", 0) > 0:
                    desc += f" ({modal.get('input_count')} полей)"
                modal_descriptions.append(desc)
            location_description.append(f"Открыто модальных окон: {visible_modals_count} ({', '.join(modal_descriptions)})")
        
        return {
            "structure": structure,
            "description": " | ".join(location_description) if location_description else "Основная страница",
            "visible_modals_count": visible_modals_count,
            "has_forms": page_state.get("visible_form_count", 0) > 0
        }
    
    async def find_search_input(self) -> Optional[Dict[str, Any]]:
        """
        Поиск поисковой строки на странице
        
        Returns:
            Информация о найденной поисковой строке или None
        """
        try:
            # Стратегия 1: Ищем по стандартным атрибутам поиска
            search_selectors = [
                'input[type="search"]',
                'input[name*="search" i]',
                'input[id*="search" i]',
                'input[placeholder*="поиск" i]',
                'input[placeholder*="search" i]',
                'input[aria-label*="поиск" i]',
                'input[aria-label*="search" i]',
                'input[class*="search" i]',
                'input[data-qa*="search" i]',
                'input[data-testid*="search" i]'
            ]
            
            for selector in search_selectors:
                try:
                    locator = self.page.locator(selector).first
                    count = await locator.count()
                    if count > 0:
                        is_visible = await locator.is_visible()
                        if not is_visible:
                            continue
                        is_text_input = await locator.evaluate("""
                        (el) => {
                            if (!el) return false;
                            const tag = el.tagName ? el.tagName.toLowerCase() : '';
                            if (tag === 'textarea') return true;
                            if (tag !== 'input') return false;
                            const type = (el.type || '').toLowerCase();
                            const allowed = ['', 'text', 'search', 'email', 'url', 'tel', 'number', 'password'];
                            return allowed.includes(type);
                        }
                        """)
                        if not is_text_input:
                            continue
                        element_selector = await locator.evaluate("""
                        (el) => {
                            if (el.id) return '#' + el.id;
                            if (el.className && typeof el.className === 'string') {
                                const classes = el.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                                if (classes.length > 0) {
                                    return el.tagName.toLowerCase() + '.' + classes.join('.');
                                }
                            }
                            return el.tagName.toLowerCase();
                        }
                        """)
                        placeholder = await locator.get_attribute("placeholder") or ""
                        return {
                            "selector": element_selector,
                            "type": "search_input",
                            "placeholder": placeholder
                        }
                except:
                    continue
            
            # Стратегия 2: Ищем по тексту "поиск" или "search" рядом с input
            try:
                search_text_locator = self.page.get_by_text("поиск", exact=False)
                search_count = await search_text_locator.count()
                if search_count == 0:
                    search_text_locator = self.page.get_by_text("search", exact=False)
                    search_count = await search_text_locator.count()
                
                if search_count > 0:
                    for i in range(min(search_count, 5)):
                        try:
                            text_element = search_text_locator.nth(i)
                            if await text_element.is_visible():
                                # Ищем ближайший input
                                input_element = await text_element.evaluate("""
                                (el) => {
                                    // Ищем input в том же родителе или рядом
                                    let parent = el.parentElement;
                                    let depth = 0;
                                    while (parent && depth < 3) {
                                        const input = parent.querySelector('input[type="text"], input[type="search"], input:not([type])');
                                        if (input) {
                                            return input;
                                        }
                                        parent = parent.parentElement;
                                        depth++;
                                    }
                                    return null;
                                }
                                """)
                                if input_element:
                                    # Получаем селектор для найденного input
                                    input_selector = await self.page.evaluate("""
                                    (el) => {
                                        if (el.id) return '#' + el.id;
                                        if (el.className && typeof el.className === 'string') {
                                            const classes = el.className.split(' ').filter(c => c && !c.startsWith('_')).slice(0, 2);
                                            if (classes.length > 0) {
                                                return el.tagName.toLowerCase() + '.' + classes.join('.');
                                            }
                                        }
                                        return el.tagName.toLowerCase();
                                    }
                                    """, input_element)
                                    if input_selector:
                                        is_valid = await self.page.evaluate("""
                                        (selector) => {
                                            try {
                                                const el = document.querySelector(selector);
                                                if (!el) return false;
                                                const tag = el.tagName ? el.tagName.toLowerCase() : '';
                                                if (tag === 'textarea') return true;
                                                if (tag !== 'input') return false;
                                                const type = (el.type || '').toLowerCase();
                                                const allowed = ['', 'text', 'search', 'email', 'url', 'tel', 'number', 'password'];
                                                return allowed.includes(type);
                                            } catch (e) {
                                                return false;
                                            }
                                        }
                                        """, input_selector)
                                        if is_valid:
                                            return {
                                                "selector": input_selector,
                                                "type": "search_input",
                                                "placeholder": ""
                                            }
                        except:
                            continue
            except:
                pass
            
            return None
        except Exception:
            return None

    async def find_clickable_ancestor(self, selector: str) -> Optional[str]:
        """
        Поиск ближайшего кликабельного родителя для элемента по селектору
        """
        script = """
        (selector) => {
            function getSelector(el) {
                if (!el) return null;
                if (el.id) return '#' + el.id;
                if (el.className && typeof el.className === 'string') {
                    const classes = el.className.split(' ')
                        .filter(c => c && !c.startsWith('_'))
                        .slice(0, 3);
                    if (classes.length > 0) {
                        return el.tagName.toLowerCase() + '.' + classes.join('.');
                    }
                }
                if (el.tagName) return el.tagName.toLowerCase();
                return null;
            }

            function isClickable(el) {
                if (!el) return false;
                const tag = el.tagName ? el.tagName.toLowerCase() : '';
                const role = (el.getAttribute && el.getAttribute('role')) ? el.getAttribute('role').toLowerCase() : '';
                if (['button', 'a', 'label'].includes(tag)) return true;
                if (tag === 'input') {
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    return type === 'button' || type === 'submit' || type === 'image' || !type;
                }
                if (role && ['button', 'link', 'menuitem'].includes(role)) return true;
                if (typeof el.onclick === 'function') return true;
                if (el.getAttribute && el.getAttribute('onclick')) return true;
                if (el.tabIndex !== undefined && el.tabIndex >= 0) return true;
                if (el.className && /button|btn|action/i.test(el.className)) return true;
                return false;
            }

            try {
                let element = document.querySelector(selector);
                while (element && element !== document.body) {
                    if (isClickable(element)) {
                        return getSelector(element);
                    }
                    element = element.parentElement;
                }
            } catch (e) {
                return null;
            }
            return null;
        }
        """
        try:
            return await self.page.evaluate(script, selector)
        except Exception:
            return None

    async def find_clickable_descendant(self, selector: str) -> Optional[str]:
        """
        Универсальный поиск кликабельного элемента: сначала проверяет кликабельность контейнера,
        затем ищет кликабельный потомок. Работает для любых ситуаций без хардкода.
        
        Args:
            selector: CSS селектор контейнера
            
        Returns:
            Селектор кликабельного элемента (контейнера или потомка) или None
        """
        script = """
        (selector) => {
            function getSelector(el) {
                if (!el) return null;
                if (el.id) return '#' + el.id;
                if (el.className && typeof el.className === 'string') {
                    const classes = el.className.split(' ')
                        .filter(c => c && c.length > 0 && !c.startsWith('_'))
                        .slice(0, 3);
                    if (classes.length > 0) {
                        return el.tagName.toLowerCase() + '.' + classes.join('.');
                    }
                }
                if (el.tagName) return el.tagName.toLowerCase();
                return null;
            }

            function isClickable(el) {
                if (!el) return false;
                
                // Проверяем видимость элемента
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                    return false;
                }
                
                const tag = el.tagName ? el.tagName.toLowerCase() : '';
                const role = (el.getAttribute && el.getAttribute('role')) ? el.getAttribute('role').toLowerCase() : '';
                
                // Стандартные кликабельные теги
                if (['button', 'a', 'label'].includes(tag)) return true;
                if (tag === 'input') {
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    return type === 'button' || type === 'submit' || type === 'image' || !type;
                }
                
                // Проверяем role атрибут
                if (role && ['button', 'link', 'menuitem', 'tab'].includes(role)) return true;
                
                // Проверяем обработчики событий (включая динамически добавленные)
                if (typeof el.onclick === 'function') return true;
                if (el.getAttribute && el.getAttribute('onclick')) return true;
                
                // Проверяем tabindex (элементы с tabindex >= 0 обычно интерактивны)
                if (el.tabIndex !== undefined && el.tabIndex >= 0) return true;
                
                // Проверяем классы на наличие индикаторов кликабельности
                if (el.className && /button|btn|action|link|click|interactive|selectable|clickable/i.test(el.className)) return true;
                
                // Проверяем cursor: pointer (важный индикатор кликабельности)
                if (style.cursor === 'pointer') return true;
                
                // Проверяем наличие обработчиков событий через getEventListeners (если доступно)
                // Это помогает найти элементы с динамически добавленными обработчиками
                try {
                    if (window.getEventListeners) {
                        const listeners = window.getEventListeners(el);
                        if (listeners && (listeners.click || listeners.mousedown || listeners.mouseup)) {
                            return true;
                        }
                    }
                } catch (e) {}
                
                // Проверяем наличие data-атрибутов, указывающих на интерактивность
                if (el.getAttribute) {
                    const dataAttrs = ['data-action', 'data-click', 'data-interactive', 'data-selectable'];
                    for (const attr of dataAttrs) {
                        if (el.getAttribute(attr)) return true;
                    }
                }
                
                return false;
            }

            function shouldExcludeElement(el) {
                // Исключаем элементы которые явно не являются основными кликабельными элементами
                if (!el || !el.className) return false;
                
                const className = el.className.toString().toLowerCase();
                const tag = el.tagName ? el.tagName.toLowerCase() : '';
                
                // Исключаем элементы с классами указывающими на вспомогательные элементы
                const excludePatterns = [
                    'avatar', 'icon', 'image', 'img', 'thumbnail', 'thumb',
                    'badge', 'label', 'tag', 'chip', 'marker', 'indicator',
                    'arrow', 'chevron', 'caret', 'dropdown', 'menu',
                    'close', 'cancel', 'delete', 'remove', 'edit', 'settings'
                ];
                
                for (const pattern of excludePatterns) {
                    if (className.includes(pattern)) {
                        return true;
                    }
                }
                
                // Исключаем маленькие изображения и иконки
                if (tag === 'img' || tag === 'svg' || tag === 'i') {
                    const rect = el.getBoundingClientRect();
                    const area = rect.width * rect.height;
                    if (area < 2000) { // Маленькие изображения/иконки
                        return true;
                    }
                }
                
                return false;
            }

            function findClickableElement(el) {
                if (!el) return null;
                
                // ШАГ 1: Сначала проверяем сам контейнер на кликабельность
                // Это важно для случаев когда контейнер сам по себе кликабелен (например, через делегирование событий)
                const containerStyle = window.getComputedStyle(el);
                const containerHasPointer = containerStyle.cursor === 'pointer';
                const containerHasHandlers = typeof el.onclick === 'function' || el.getAttribute('onclick');
                
                // Проверяем обработчики событий через getEventListeners (если доступно)
                let containerHasEventListeners = false;
                try {
                    if (window.getEventListeners) {
                        const listeners = window.getEventListeners(el);
                        if (listeners && (listeners.click || listeners.mousedown || listeners.mouseup)) {
                            containerHasEventListeners = true;
                        }
                    }
                } catch (e) {}
                
                // Если контейнер имеет cursor: pointer или обработчики событий - он кликабелен через делегирование
                // ПРИОРИТЕТ: используем сам контейнер для list_item, card и подобных элементов
                if (containerHasPointer || containerHasHandlers || containerHasEventListeners || isClickable(el)) {
                    const containerSelector = getSelector(el);
                    if (containerSelector) {
                        return containerSelector;
                    }
                }
                
                // ШАГ 2: Ищем среди прямых потомков, но ПРИОРИТИЗИРУЕМ элементы с большой областью клика
                // АГРЕССИВНО исключаем маленькие элементы типа avatar, icon - они могут иметь другую функциональность
                const children = Array.from(el.children || []);
                let bestChild = null;
                let bestChildSize = 0;
                
                for (const child of children) {
                    if (isClickable(child) && !shouldExcludeElement(child)) {
                        const rect = child.getBoundingClientRect();
                        const area = rect.width * rect.height;
                        
                        // Приоритет элементам с большей областью клика (это обычно основной кликабельный элемент)
                        // Увеличиваем минимальный размер до 2000px² для более надежного исключения маленьких элементов
                        if (area > 2000 && area > bestChildSize) {
                            bestChild = child;
                            bestChildSize = area;
                        }
                    }
                }
                
                if (bestChild) {
                    const childSelector = getSelector(bestChild);
                    if (childSelector) {
                        return childSelector;
                    }
                }
                
                // ШАГ 3: Если не нашли большой элемент среди прямых потомков, ищем среди всех потомков
                // Но также приоритизируем элементы с большой областью клика и исключаем вспомогательные элементы
                const candidates = el.querySelectorAll('a, button, [role="button"], [role="link"], [onclick], [tabindex], [data-action], [data-click]');
                let bestCandidate = null;
                let bestCandidateSize = 0;
                
                for (const candidate of candidates) {
                    if (isClickable(candidate) && el.contains(candidate) && !shouldExcludeElement(candidate)) {
                        const rect = candidate.getBoundingClientRect();
                        const area = rect.width * rect.height;
                        
                        // Приоритет элементам с большей областью клика
                        // Увеличиваем минимальный размер до 2000px²
                        if (area > 2000 && area > bestCandidateSize) {
                            bestCandidate = candidate;
                            bestCandidateSize = area;
                        }
                    }
                }
                
                if (bestCandidate) {
                    const candidateSelector = getSelector(bestCandidate);
                    if (candidateSelector) {
                        return candidateSelector;
                    }
                }
                
                // ШАГ 4: Если ничего не найдено, но контейнер имеет cursor: pointer или обработчики событий,
                // возвращаем селектор контейнера (возможно клик обрабатывается через делегирование)
                if (containerHasPointer || containerHasHandlers || containerHasEventListeners) {
                    const containerSelector = getSelector(el);
                    if (containerSelector) {
                        return containerSelector;
                    }
                }
                
                // ШАГ 5: Последняя попытка - берем первый кликабельный элемент БЕЗ исключений
                // НО только если он достаточно большой (минимум 1500px²)
                for (const child of children) {
                    if (isClickable(child)) {
                        const rect = child.getBoundingClientRect();
                        const area = rect.width * rect.height;
                        if (area > 1500) { // Минимум для fallback
                            const childSelector = getSelector(child);
                            if (childSelector) {
                                return childSelector;
                            }
                        }
                    }
                }
                
                // ШАГ 6: Если ничего не найдено, возвращаем сам контейнер (лучше попробовать кликнуть по контейнеру чем ничего)
                const containerSelector = getSelector(el);
                if (containerSelector) {
                    return containerSelector;
                }
                
                return null;
            }

            try {
                const element = document.querySelector(selector);
                if (!element) return null;
                
                return findClickableElement(element);
            } catch (e) {
                return null;
            }
        }
        """
        try:
            return await self.page.evaluate(script, selector)
        except Exception:
            return None

    @staticmethod
    def _extract_input_keywords(description: str) -> List[str]:
        """Выделение значимых ключевых слов из описания поля"""
        if not description:
            return []
        words = re.findall(r'\w+', description.lower())
        stop_words = {
            "поле", "input", "ввод", "text", "текст", "field", "для", "searchbox",
            "search", "поиск", "найти", "query", "строка", "строка_поиска"
        }
        return [w for w in words if len(w) > 2 and w not in stop_words]

    async def find_input_field(self, description: str) -> Optional[Dict[str, Any]]:
        """
        Поиск текстового поля (input/textarea) по описанию

        Args:
            description: Описание поля ввода
        """
        try:
            page_info = await self.extract_page_info(include_text=False)
        except Exception:
            return None

        elements = page_info.get("interactive_elements", [])
        if not elements:
            return None

        keywords = self._extract_input_keywords(description or "")
        description_lower = (description or "").lower()
        matches_search = any(word in description_lower for word in ["поиск", "search", "query"])

        best_element = None
        best_score = 0

        def is_text_like(elem: Dict[str, Any]) -> bool:
            if not elem:
                return False
            elem_type = (elem.get("type") or "").lower()
            tag = (elem.get("tag") or "").lower()
            role = (elem.get("role") or "").lower()
            input_type = (elem.get("input_type") or "").lower()

            disallowed_inputs = {
                "checkbox", "radio", "submit", "button", "file", "range",
                "color", "date", "datetime-local", "month", "week", "time",
                "hidden"
            }
            if tag == "input" and input_type in disallowed_inputs:
                return False
            if tag == "input" and input_type.startswith("magritte-checkbox"):
                return False
            if tag == "input":
                return True
            if tag == "textarea":
                return True
            if role in {"textbox", "searchbox", "combobox"}:
                return True
            return False

        for element in elements:
            elem_type = (element.get("type") or "").lower()
            tag = (element.get("tag") or "").lower()
            role = (element.get("role") or "").lower()

            is_input = elem_type in {"input", "textarea", "search_input"} or \
                tag in {"input", "textarea"} or \
                role in {"textbox", "searchbox", "combobox"}

            if not is_input or not is_text_like(element):
                continue

            selector = element.get("selector")
            if not selector:
                continue

            score = 0
            text_parts = [
                element.get("text", ""),
                element.get("placeholder", ""),
                element.get("label", ""),
                element.get("name", ""),
                element.get("aria_label", "")
            ]
            text_blob = " ".join([part for part in text_parts if part]).lower()

            if matches_search and ("search" in elem_type or role in {"searchbox"}):
                score += 5

            if not keywords:
                if element.get("placeholder"):
                    score += 2
                if element.get("label"):
                    score += 2
            else:
                for kw in keywords:
                    if kw and kw in text_blob:
                        score += 4

            # Дополнительные бонусы
            if element.get("in_form"):
                score += 1
            if element.get("visible", True):
                score += 1

            if score > best_score:
                best_element = element
                best_score = score

        return best_element
