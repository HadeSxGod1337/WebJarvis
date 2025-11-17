"""Менеджер для управления скриншотами"""
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from config import PROJECT_ROOT


class ScreenshotManager:
    """Менеджер для сохранения и управления скриншотами"""
    
    def __init__(self):
        """Инициализация менеджера скриншотов"""
        self.screenshots_dir = PROJECT_ROOT / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)
    
    def generate_screenshot_path(self, description: Optional[str] = None, action: Optional[str] = None) -> Path:
        """
        Генерация пути для сохранения скриншота
        
        Args:
            description: Описание скриншота (опционально)
            action: Тип действия, при котором сделан скриншот (опционально)
            
        Returns:
            Путь к файлу скриншота
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        
        # Формируем имя файла
        filename_parts = [timestamp]
        if action:
            filename_parts.append(action)
        if description:
            # Очищаем описание от недопустимых символов для имени файла
            safe_description = "".join(c for c in description[:30] if c.isalnum() or c in (' ', '-', '_')).strip()
            safe_description = safe_description.replace(' ', '_')
            if safe_description:
                filename_parts.append(safe_description)
        
        filename = "_".join(filename_parts) + ".png"
        return self.screenshots_dir / filename
    
    async def save_screenshot(self, screenshot_bytes: bytes, description: Optional[str] = None, action: Optional[str] = None) -> Dict[str, Any]:
        """
        Сохранение скриншота в файл
        
        Args:
            screenshot_bytes: Байты скриншота
            description: Описание скриншота (опционально)
            action: Тип действия (опционально)
            
        Returns:
            Результат операции с путем к файлу
        """
        try:
            screenshot_path = self.generate_screenshot_path(description, action)
            
            # Сохраняем скриншот
            with open(screenshot_path, 'wb') as f:
                f.write(screenshot_bytes)
            
            return {
                "success": True,
                "path": str(screenshot_path),
                "relative_path": str(screenshot_path.relative_to(PROJECT_ROOT))
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_screenshots_dir(self) -> Path:
        """Получение директории для скриншотов"""
        return self.screenshots_dir

