"""Базовые тесты для агента"""
import pytest
import asyncio
from src.browser.controller import BrowserController
from src.browser.page_extractor import PageExtractor
from src.context.manager import ContextManager
from src.actions.action_executor import ActionExecutor


@pytest.mark.asyncio
async def test_browser_controller():
    """Тест инициализации браузера"""
    controller = BrowserController()
    try:
        await controller.start()
        assert controller.page is not None
        assert controller.context is not None
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_page_extractor():
    """Тест извлечения информации со страницы"""
    controller = BrowserController()
    try:
        await controller.start()
        await controller.navigate("https://example.com")
        
        extractor = PageExtractor(controller.page)
        page_info = await extractor.extract_page_info()
        
        assert "url" in page_info
        assert "title" in page_info
        assert "interactive_elements" in page_info
        assert isinstance(page_info["interactive_elements"], list)
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_context_manager():
    """Тест управления контекстом"""
    manager = ContextManager()
    manager.set_task("Тестовая задача")
    
    # Создаем тестовую информацию о странице
    page_info = {
        "url": "https://example.com",
        "title": "Example Domain",
        "interactive_elements": [
            {"type": "link", "selector": "a", "text": "More information..."}
        ],
        "visible_text_preview": "Example Domain"
    }
    
    context = manager.prepare_context(page_info)
    assert len(context) > 0
    assert "Example Domain" in context


@pytest.mark.asyncio
async def test_action_executor():
    """Тест выполнения действий"""
    controller = BrowserController()
    try:
        await controller.start()
        await controller.navigate("https://example.com")
        
        extractor = PageExtractor(controller.page)
        executor = ActionExecutor(controller, extractor)
        
        # Тест прокрутки
        result = await executor.execute_action("scroll", {"direction": "down", "amount": 100})
        assert result.get("success") is True
    finally:
        await controller.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

