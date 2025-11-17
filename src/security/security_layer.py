"""Security Layer для проверки деструктивных действий"""
from typing import Dict, Any, Optional, Callable, Tuple


class SecurityLayer:
    """Слой безопасности для проверки деструктивных действий"""
    
    def __init__(self, user_confirmation_callback: Optional[Callable[[str], bool]] = None):
        """
        Инициализация слоя безопасности
        
        Args:
            user_confirmation_callback: Функция для запроса подтверждения у пользователя
        """
        self.user_confirmation_callback = user_confirmation_callback
        self.destructive_keywords = {
            "payment": ["оплат", "купит", "заказ", "checkout", "payment", "buy", "purchase", "pay"],
            "deletion": ["удал", "delete", "remove", "уничтож", "destroy"],
            "sending": ["отправ", "send", "submit", "публикац", "publish"],
            "modification": ["измен", "change", "edit", "modify", "update"],
            "confirmation": ["подтверд", "confirm", "accept", "agree"]
        }
    
    def is_destructive_action(self, action_name: str, parameters: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Проверка, является ли действие деструктивным
        
        Args:
            action_name: Название действия
            parameters: Параметры действия
            
        Returns:
            Кортеж (является ли деструктивным, категория опасности)
        """
        # Проверяем действия, которые могут быть деструктивными
        if action_name == "click_element":
            description = parameters.get("description", "").lower()
            selector = parameters.get("selector", "").lower()
            text_to_check = f"{description} {selector}"
            
            # Проверка на оплату
            if any(keyword in text_to_check for keyword in self.destructive_keywords["payment"]):
                return True, "payment"
            
            # Проверка на удаление
            if any(keyword in text_to_check for keyword in self.destructive_keywords["deletion"]):
                return True, "deletion"
            
            # Проверка на отправку важных данных
            if any(keyword in text_to_check for keyword in self.destructive_keywords["sending"]):
                return True, "sending"
        
        elif action_name == "type_text":
            # Ввод текста в поля, связанные с оплатой или подтверждением
            element_desc = parameters.get("element_description", "").lower()
            text = parameters.get("text", "").lower()
            
            # Проверка на ввод данных для оплаты
            if any(keyword in element_desc for keyword in ["card", "cvv", "cvc", "карт", "cvc", "cvv"]):
                return True, "payment"
            
            # Проверка на подтверждение
            if any(keyword in element_desc for keyword in self.destructive_keywords["confirmation"]):
                if any(keyword in text for keyword in ["yes", "да", "подтверждаю", "согласен"]):
                    return True, "confirmation"
        
        return False, ""
    
    async def check_action(self, action_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Проверка действия на безопасность
        
        Args:
            action_name: Название действия
            parameters: Параметры действия
            
        Returns:
            Результат проверки с разрешением или запросом подтверждения
        """
        is_destructive, category = self.is_destructive_action(action_name, parameters)
        
        if not is_destructive:
            return {
                "allowed": True,
                "requires_confirmation": False
            }
        
        # Требуется подтверждение
        if self.user_confirmation_callback:
            action_description = self._format_action_description(action_name, parameters, category)
            confirmed = self.user_confirmation_callback(action_description)
            
            return {
                "allowed": confirmed,
                "requires_confirmation": True,
                "confirmed": confirmed,
                "category": category
            }
        else:
            # Если нет callback, разрешаем с предупреждением
            return {
                "allowed": True,
                "requires_confirmation": True,
                "warning": f"Деструктивное действие обнаружено: {category}",
                "category": category
            }
    
    def _format_action_description(self, action_name: str, parameters: Dict[str, Any], category: str) -> str:
        """Форматирование описания действия для пользователя"""
        descriptions = {
            "payment": "оплата или покупка",
            "deletion": "удаление данных",
            "sending": "отправка данных",
            "confirmation": "подтверждение действия"
        }
        
        category_desc = descriptions.get(category, "потенциально опасное действие")
        
        if action_name == "click_element":
            element = parameters.get("description", "элемент")
            return f"Попытка выполнить {category_desc}: клик по '{element}'. Продолжить?"
        elif action_name == "type_text":
            field = parameters.get("element_description", "поле")
            return f"Попытка выполнить {category_desc}: ввод данных в '{field}'. Продолжить?"
        else:
            return f"Попытка выполнить {category_desc}. Продолжить?"
    
    def log_action(self, action_name: str, parameters: Dict[str, Any], result: Dict[str, Any]):
        """
        Логирование действия для аудита
        
        Args:
            action_name: Название действия
            parameters: Параметры действия
            result: Результат выполнения
        """
        # В будущем можно добавить запись в файл или базу данных
        is_destructive, category = self.is_destructive_action(action_name, parameters)
        if is_destructive:
            print(f"[SECURITY] Деструктивное действие выполнено: {action_name} ({category})")

