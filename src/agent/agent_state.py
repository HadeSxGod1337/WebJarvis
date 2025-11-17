"""Управление состоянием агента"""
import json
from typing import Dict, Any, List, Optional
from enum import Enum


class AgentState(Enum):
    """Состояния агента"""
    IDLE = "idle"
    OBSERVING = "observing"
    DECIDING = "deciding"
    ACTING = "acting"
    REFLECTING = "reflecting"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    ERROR = "error"


class AgentStateManager:
    """Менеджер состояния агента"""
    
    def __init__(self):
        self.state = AgentState.IDLE
        self.current_task: Optional[str] = None
        self.action_history: List[Dict[str, Any]] = []
        self.visited_urls: List[str] = []  # История посещенных URL
        self.query_dom_history: List[Dict[str, Any]] = []  # История заданных вопросов query_dom
        self.iteration_count = 0
        self.last_error: Optional[str] = None
    
    def set_state(self, state: AgentState):
        """Установка состояния"""
        self.state = state
    
    def set_task(self, task: str):
        """Установка текущей задачи"""
        self.current_task = task
        self.state = AgentState.OBSERVING
        self.action_history = []
        self.visited_urls = []
        self.query_dom_history = []
        self.iteration_count = 0
        self.last_error = None
    
    def add_visited_url(self, url: str):
        """Добавление URL в историю посещений"""
        # Нормализуем URL (убираем параметры запроса для сравнения)
        normalized_url = url.split('?')[0].split('#')[0].rstrip('/')
        if normalized_url not in self.visited_urls:
            self.visited_urls.append(normalized_url)
    
    def is_url_visited(self, url: str) -> bool:
        """Проверка, был ли URL уже посещен"""
        normalized_url = url.split('?')[0].split('#')[0].rstrip('/')
        return normalized_url in self.visited_urls
    
    def _is_critical_action(self, action_name: str) -> bool:
        """Проверка, является ли действие критическим (требует более строгого обнаружения циклов)"""
        return action_name in ["navigate", "click_element"]
    
    def _get_action_signature(self, action: Dict[str, Any]) -> tuple:
        """Получение подписи действия для сравнения"""
        action_name = action.get("action", "")
        params = action.get("parameters", {}) or {}
        page_changed = action.get("page_changed", True)
        new_elements = action.get("new_elements", {})
        has_new_elements = (
            new_elements.get("new_modals", False) or
            new_elements.get("new_forms", False) or
            new_elements.get("new_interactive_elements", False)
        )
        effective_page_changed = page_changed or has_new_elements
        signature = f"{action_name}:{json.dumps(params, sort_keys=True, default=str)}"
        return (signature, effective_page_changed, has_new_elements, action_name)
    
    def _check_consecutive_repeats(self, recent_actions: list, critical_threshold: int = 2, normal_threshold: int = 3) -> Optional[Dict[str, Any]]:
        """Проверка последовательных повторений действий"""
        if len(recent_actions) < 2:
            return None
        
        action_signatures = [self._get_action_signature(a) for a in recent_actions]
        consecutive_repeats = 1
        last_signature = None
        
        for signature, effective_page_changed, has_new_elements, action_name in action_signatures:
            if signature == last_signature:
                if not effective_page_changed and not has_new_elements:
                    consecutive_repeats += 1
                    # Для критических действий - порог 2, для остальных - 3
                    threshold = critical_threshold if self._is_critical_action(action_name) else normal_threshold
                    if consecutive_repeats >= threshold:
                        return {
                            "detected": True,
                            "reason": f"Повторяющееся действие без изменения страницы ({consecutive_repeats} раза): {signature}",
                            "action": action_name,
                            "parameters": recent_actions[-consecutive_repeats].get("parameters"),
                            "repeats": consecutive_repeats,
                            "is_critical": self._is_critical_action(action_name)
                        }
                else:
                    consecutive_repeats = 1
            else:
                consecutive_repeats = 1
            last_signature = signature
        
        return None
    
    def _check_repeating_errors(self, recent_actions: list, threshold: int = 2) -> Optional[Dict[str, Any]]:
        """Проверка повторяющихся ошибок"""
        error_messages = []
        page_changes = []
        action_names = []
        action_params = []
        
        for action in recent_actions:
            result = action.get("result", {})
            page_changed = action.get("page_changed", True)
            action_name = action.get("action", "")
            params = action.get("parameters", {})
            
            if not result.get("success") and result.get("error"):
                error_messages.append(result.get("error", ""))
                page_changes.append(page_changed)
                action_names.append(action_name)
                action_params.append(params)
        
        if len(error_messages) >= threshold:
            unique_errors = set(error_messages)
            if len(unique_errors) <= 1 and not any(page_changes):
                if len(set(action_names)) <= 1 and len(set(str(p) for p in action_params)) <= 1:
                    return {
                        "detected": True,
                        "reason": f"Повторяющееся действие с ошибкой без изменения страницы ({len(error_messages)} раза): {action_names[0] if action_names else 'unknown'}",
                        "error": error_messages[0],
                        "action": action_names[0] if action_names else None,
                        "parameters": action_params[0] if action_params else None,
                        "repeats": len(error_messages)
                    }
        
        return None
    
    def _check_repeating_urls(self, recent_actions: list, threshold: int = 2) -> Optional[Dict[str, Any]]:
        """Проверка повторяющихся URL"""
        recent_urls = []
        page_changes = []
        
        for action in recent_actions:
            if action.get("action") == "navigate":
                url = action.get("result", {}).get("url", "") or action.get("parameters", {}).get("url", "")
                page_changed = action.get("page_changed", True)
                if url:
                    normalized_url = url.split('?')[0].split('#')[0].rstrip('/')
                    recent_urls.append(normalized_url)
                    page_changes.append(page_changed)
        
        if len(recent_urls) >= threshold:
            unique_urls = set(recent_urls)
            if len(unique_urls) <= 1 and not any(page_changes):
                return {
                    "detected": True,
                    "reason": f"Повторяющийся URL без изменения страницы ({len(recent_urls)} раза): {recent_urls[0]}",
                    "url": recent_urls[0],
                    "repeats": len(recent_urls)
                }
        
        return None
    
    def _check_pattern_repetition(self, recent_action_descriptions: list) -> Optional[Dict[str, Any]]:
        """Проверка паттернов повторения (A-B-A-B)"""
        if len(recent_action_descriptions) >= 4:
            pattern_length = 2
            for i in range(len(recent_action_descriptions) - pattern_length * 2):
                pattern = recent_action_descriptions[i:i+pattern_length]
                next_pattern = recent_action_descriptions[i+pattern_length:i+pattern_length*2]
                if pattern == next_pattern:
                    return {
                        "detected": True,
                        "reason": f"Паттерн повторения: {' -> '.join(pattern)}",
                        "pattern": pattern
                    }
        return None
    
    def detect_loop(self, lookback: int = 4) -> Optional[Dict[str, Any]]:
        """
        Обнаружение циклов в последних действиях (оптимизированная версия)
        
        Args:
            lookback: Количество последних действий для анализа
            
        Returns:
            Dict с информацией о цикле или None если цикл не обнаружен
        """
        if len(self.action_history) < 2:
            return None
        
        # Проверяем последние действия
        recent_actions = self.action_history[-lookback*2:]
        
        # 1. Проверка последовательных повторений (для критических действий порог 2, для остальных 3)
        repeat_check = self._check_consecutive_repeats(recent_actions[-6:], critical_threshold=2, normal_threshold=3)
        if repeat_check:
            return repeat_check
        
        # 2. Проверка повторяющихся ошибок (порог 2)
        error_check = self._check_repeating_errors(recent_actions[-lookback:], threshold=2)
        if error_check:
            return error_check
        
        # 3. Проверка повторяющихся URL (порог 2 для критических)
        url_check = self._check_repeating_urls(recent_actions[-lookback:], threshold=2)
        if url_check:
            return url_check
        
        # 4. Проверка паттернов повторения (A-B-A-B)
        recent_action_descriptions = []
        for action in recent_actions[-lookback:]:
            action_name = action.get("action", "")
            params = action.get("parameters", {})
            desc = f"{action_name}"
            if params.get("description"):
                desc += f":{params.get('description')}"
            elif params.get("element_description"):
                desc += f":{params.get('element_description')}"
            elif params.get("url"):
                url = params.get('url', '')
                url_normalized = url.split('?')[0].split('#')[0].rstrip('/')
                desc += f":{url_normalized}"
            recent_action_descriptions.append(desc)
        
        pattern_check = self._check_pattern_repetition(recent_action_descriptions)
        if pattern_check:
            return pattern_check
        
        # 5. Специальная проверка для extract_text с одинаковым описанием
        if len(recent_actions) >= 2:
            extract_text_actions = [
                (a.get("action"), a.get("parameters", {}), a.get("page_changed", True))
                for a in recent_actions[-3:]
                if a.get("action") == "extract_text"
            ]
            if len(extract_text_actions) >= 2:
                descriptions = [params.get("description", "") for _, params, _ in extract_text_actions]
                page_changes = [page_changed for _, _, page_changed in extract_text_actions]
                if len(set(descriptions)) <= 1 and descriptions[0] and not any(page_changes):
                    return {
                        "detected": True,
                        "reason": f"Повторяющееся extract_text с тем же описанием без изменения страницы ({len(extract_text_actions)} раза): {descriptions[0]}",
                        "error": f"extract_text с описанием '{descriptions[0]}' повторяется без результата",
                        "action": "extract_text",
                        "parameters": {"description": descriptions[0]},
                        "repeats": len(extract_text_actions)
                    }
        
        # 6. Специальная проверка для query_dom с одинаковыми вопросами (порог 2)
        if len(recent_actions) >= 2:
            query_dom_actions = [
                (a.get("action"), a.get("parameters", {}), a.get("page_changed", True), a.get("result", {}))
                for a in recent_actions[-5:]
                if a.get("action") == "query_dom"
            ]
            if len(query_dom_actions) >= 2:
                queries = [params.get("query", "") for _, params, _, _ in query_dom_actions]
                page_changes = [page_changed for _, _, page_changed, _ in query_dom_actions]
                # Нормализуем вопросы используя улучшенную нормализацию
                normalized_queries = [self._normalize_query(q) for q in queries if q]
                if len(normalized_queries) >= 2:
                    # Проверяем, есть ли одинаковые или похожие вопросы
                    similar_pairs = 0
                    for i in range(len(normalized_queries)):
                        for j in range(i + 1, len(normalized_queries)):
                            if normalized_queries[i] == normalized_queries[j] or \
                               self._are_queries_similar(normalized_queries[i], normalized_queries[j]):
                                similar_pairs += 1
                    
                    # Если есть хотя бы одна пара похожих вопросов и страница не менялась
                    if similar_pairs >= 1 and not any(page_changes):
                        return {
                            "detected": True,
                            "reason": f"Повторяющийся query_dom с похожими вопросами без изменения страницы ({len(query_dom_actions)} раза): {queries[-1][:100]}",
                            "error": f"query_dom с похожими вопросами повторяется без результата",
                            "action": "query_dom",
                            "parameters": {"query": queries[-1] if queries else ""},
                            "repeats": len(query_dom_actions)
                        }
        
        return None
    
    def add_action(self, action: Dict[str, Any]):
        """Добавление действия в историю"""
        self.action_history.append(action)
        self.iteration_count += 1
    
    def set_error(self, error: str):
        """Установка ошибки"""
        self.last_error = error
        self.state = AgentState.ERROR
    
    def get_state_info(self) -> Dict[str, Any]:
        """Получение информации о текущем состоянии"""
        return {
            "state": self.state.value,
            "task": self.current_task,
            "iteration_count": self.iteration_count,
            "actions_count": len(self.action_history),
            "last_error": self.last_error
        }
    
    def add_query_dom(self, query: str, answer: str, url: str = ""):
        """Добавление вопроса query_dom в историю"""
        normalized_query = self._normalize_query(query)
        self.query_dom_history.append({
            "query": query,
            "normalized_query": normalized_query,
            "answer": answer,
            "url": url,
            "iteration": self.iteration_count
        })
        # Оставляем только последние 20 вопросов
        if len(self.query_dom_history) > 20:
            self.query_dom_history = self.query_dom_history[-20:]
    
    def _normalize_query(self, query: str) -> str:
        """Нормализация вопроса для сравнения (улучшенная версия)"""
        normalized = query.lower().strip()
        # Убираем лишние пробелы
        normalized = ' '.join(normalized.split())
        # Убираем знаки препинания в конце
        normalized = normalized.rstrip('?.,!;:')
        # Нормализуем только общие технические термины (БЕЗ доменных слов типа "письмо", "вакансия" и т.д.)
        synonyms = {
            'селектор': 'селектор',
            'селекторы': 'селектор',
            'идентификатор': 'селектор',
            'идентификаторы': 'селектор',
            'элемент': 'элемент',
            'элементы': 'элемент',
            'список': 'список',
            'видны': 'виден',
            'видно': 'виден',
            'есть ли': 'есть',
            'какие': 'какой',
            'первый': 'первый',
            'первые': 'первый',
            '10': '10',
            'десять': '10'
        }
        words = normalized.split()
        normalized_words = [synonyms.get(w, w) for w in words]
        return ' '.join(normalized_words)
    
    def was_query_asked(self, query: str, url: str = "") -> bool:
        """Проверка, был ли уже задан похожий вопрос"""
        normalized_query = self._normalize_query(query)
        # Проверяем точное совпадение или очень похожие вопросы
        for q in self.query_dom_history:
            existing_normalized = self._normalize_query(q["query"])
            # Проверяем точное совпадение или совпадение ключевых слов
            if normalized_query == existing_normalized:
                # Если URL совпадает или не указан - считаем повторением
                if not url or not q.get("url") or url == q.get("url"):
                    return True
            # Также проверяем семантическое сходство (если вопросы очень похожи по ключевым словам)
            elif self._are_queries_similar(normalized_query, existing_normalized):
                if not url or not q.get("url") or url == q.get("url"):
                    return True
        return False
    
    def _are_queries_similar(self, query1: str, query2: str) -> bool:
        """Проверка семантического сходства вопросов"""
        words1 = set(query1.split())
        words2 = set(query2.split())
        
        # Если вопросы очень короткие - требуем точное совпадение
        if len(words1) < 3 or len(words2) < 3:
            return False
        
        # Вычисляем пересечение ключевых слов
        common_words = words1 & words2
        # Если больше 70% слов совпадают - вопросы похожи
        similarity = len(common_words) / max(len(words1), len(words2))
        return similarity >= 0.7
    
    def get_query_answer(self, query: str, url: str = "") -> Optional[str]:
        """Получение ответа на похожий вопрос из истории"""
        normalized_query = self._normalize_query(query)
        # Ищем самый последний похожий вопрос
        for q in reversed(self.query_dom_history):
            existing_normalized = self._normalize_query(q["query"])
            if normalized_query == existing_normalized or self._are_queries_similar(normalized_query, existing_normalized):
                if not url or not q.get("url") or url == q.get("url"):
                    return q.get("answer", "")
        return None
    
    def get_recent_query_dom_info(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Получение информации о последних query_dom вопросах"""
        return self.query_dom_history[-limit:]
    
    def reset(self):
        """Сброс состояния"""
        self.state = AgentState.IDLE
        self.current_task = None
        self.action_history = []
        self.visited_urls = []
        self.query_dom_history = []
        self.iteration_count = 0
        self.last_error = None

