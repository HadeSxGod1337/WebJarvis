"""Управление persistent sessions браузера"""
from pathlib import Path
from typing import Optional
from config import SESSION_DIR
import os


class SessionManager:
    """Менеджер сессий для сохранения состояния браузера"""
    
    def __init__(self):
        self.session_dir = SESSION_DIR
        self.session_dir.mkdir(parents=True, exist_ok=True)
    
    def get_session_path(self, session_name: str) -> Path:
        """
        Получение пути к директории сессии
        
        Args:
            session_name: Имя сессии
            
        Returns:
            Путь к директории сессии
        """
        return self.session_dir / session_name
    
    def create_session(self, session_name: str) -> Path:
        """
        Создание новой сессии
        
        Args:
            session_name: Имя сессии
            
        Returns:
            Путь к директории сессии
        """
        session_path = self.get_session_path(session_name)
        session_path.mkdir(parents=True, exist_ok=True)
        return session_path
    
    def session_exists(self, session_name: str) -> bool:
        """
        Проверка существования сессии
        
        Args:
            session_name: Имя сессии
            
        Returns:
            True если сессия существует
        """
        session_path = self.get_session_path(session_name)
        return session_path.exists() and session_path.is_dir()
    
    def list_sessions(self) -> list[str]:
        """
        Получение списка всех сессий
        
        Returns:
            Список имен сессий
        """
        if not self.session_dir.exists():
            return []
        
        sessions = []
        for item in self.session_dir.iterdir():
            if item.is_dir():
                sessions.append(item.name)
        
        return sessions
    
    def delete_session(self, session_name: str) -> bool:
        """
        Удаление сессии
        
        Args:
            session_name: Имя сессии
            
        Returns:
            True если сессия удалена успешно
        """
        session_path = self.get_session_path(session_name)
        if session_path.exists():
            import shutil
            shutil.rmtree(session_path)
            return True
        return False
    
    def get_user_data_dir(self, session_name: str) -> Optional[str]:
        """
        Получение пути к user data directory для Playwright persistent context
        
        Args:
            session_name: Имя сессии
            
        Returns:
            Путь к user data directory или None
        """
        if self.session_exists(session_name):
            return str(self.get_session_path(session_name))
        return None

