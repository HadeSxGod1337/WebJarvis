"""Валидация действий перед выполнением"""
from typing import Dict, Any, Optional, Tuple


class ActionValidator:
    """Валидатор действий"""
    
    @staticmethod
    def validate_action(action_name: str, parameters: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Валидация действия перед выполнением
        
        Args:
            action_name: Название действия
            parameters: Параметры действия
            
        Returns:
            Кортеж (валидно ли действие, сообщение об ошибке если есть)
        """
        if action_name == "click_element":
            if not parameters.get("description") and not parameters.get("selector"):
                return False, "Требуется описание элемента или селектор"
        
        elif action_name == "type_text":
            if not parameters.get("text"):
                return False, "Требуется текст для ввода"
            if not parameters.get("element_description") and not parameters.get("selector"):
                return False, "Требуется описание поля ввода или селектор"
        
        elif action_name == "navigate":
            if not parameters.get("url") and not parameters.get("search_query"):
                return False, "Требуется URL или поисковый запрос"
        
        elif action_name == "scroll":
            direction = parameters.get("direction")
            if direction not in ["down", "up", "left", "right"]:
                return False, f"Неверное направление прокрутки: {direction}"
        
        elif action_name == "wait_for_element":
            if not parameters.get("description") and not parameters.get("selector"):
                return False, "Требуется описание элемента или селектор"
        
        elif action_name == "extract_text":
            if not parameters.get("description") and not parameters.get("selector"):
                return False, "Требуется описание элемента или селектор"
        
        return True, None

