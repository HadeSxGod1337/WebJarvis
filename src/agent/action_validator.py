"""Универсальная валидация результатов действий через AI"""
from typing import Dict, Any, Optional, List
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL
import json
import hashlib


class ActionResultValidator:
    """Валидатор результатов действий агента через AI"""
    
    # Критические действия, которые требуют валидации
    CRITICAL_ACTIONS = {"navigate", "click_element", "type_text"}
    
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self._validation_cache: Dict[str, Dict[str, Any]] = {}
    
    def _is_critical_action(self, action: str) -> bool:
        """Проверка, является ли действие критическим"""
        return action in self.CRITICAL_ACTIONS
    
    def _get_cache_key(self, action: str, action_params: Dict[str, Any], action_result: Dict[str, Any]) -> str:
        """Генерация ключа кэша для валидации"""
        # Используем хеш от действия, параметров и ключевых результатов
        key_data = {
            "action": action,
            "params": json.dumps(action_params, sort_keys=True, default=str),
            "success": action_result.get("success", False),
            "page_changed": action_result.get("page_changed", False),
            "new_modals": action_result.get("new_elements", {}).get("new_modals", False),
            "new_forms": action_result.get("new_elements", {}).get("new_forms", False)
        }
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _heuristic_validation(self, action: str, action_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Быстрая эвристическая валидация без AI"""
        success = action_result.get("success", False)
        page_changed = action_result.get("page_changed", False)
        new_elements = action_result.get("new_elements", {})
        has_new_modals = new_elements.get("new_modals", False)
        has_new_forms = new_elements.get("new_forms", False)
        
        # Если действие завершилось ошибкой - не валидно
        if not success:
            error = action_result.get("error", "Unknown error")
            return {
                "is_valid": False,
                "validation_message": f"Действие завершилось ошибкой: {error}",
                "suggestions": ["Проверьте селектор элемента", "Попробуйте другой подход"]
            }
        
        # Для navigate: должна измениться страница
        if action == "navigate":
            if not page_changed:
                return {
                    "is_valid": False,
                    "validation_message": "Навигация не привела к изменению страницы",
                    "suggestions": ["Проверьте URL", "Подождите загрузки страницы"]
                }
        
        # Для click_element: должна измениться страница или появиться новые элементы
        if action == "click_element":
            if not page_changed and not has_new_modals and not has_new_forms:
                return {
                    "is_valid": False,
                    "validation_message": "Клик не привел к изменению страницы или появлению новых элементов",
                    "suggestions": ["Проверьте селектор элемента", "Элемент может быть не кликабельным", "Попробуйте другой элемент"]
                }
        
        # Для type_text: успех = текст введен в поле (success=true), НЕ требуется изменение страницы
        # Страница изменится только после клика на кнопку поиска или если есть автопоиск
        if action == "type_text":
            # Если текст успешно введен - действие валидно, независимо от page_changed
            if success:
                return {
                    "is_valid": True,
                    "validation_message": "Текст успешно введен в поле",
                    "suggestions": []
                }
            # Если текст не был введен - действие не валидно
            else:
                return {
                    "is_valid": False,
                    "validation_message": "Текст не был введен в поле",
                    "suggestions": ["Проверьте селектор поля", "Поле может быть недоступно", "Попробуйте другой селектор"]
                }
        
        # Если все проверки пройдены - считаем валидным (но AI валидация может уточнить)
        return None  # None означает, что эвристическая валидация не определила проблему
    
    async def validate_action_result(
        self,
        action: str,
        action_params: Dict[str, Any],
        action_result: Dict[str, Any],
        task: str,
        page_info: Dict[str, Any],
        history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Валидация результата действия через AI с быстрой эвристической проверкой
        
        Проверяет, соответствует ли результат действия ожидаемому результату из задачи.
        
        Args:
            action: Название действия (click_element, type_text, etc.)
            action_params: Параметры действия
            action_result: Результат выполнения действия
            task: Текущая задача
            page_info: Информация о текущей странице после действия
            history: История последних действий
            
        Returns:
            Результат валидации с информацией о соответствии ожидаемому результату
        """
        # 1. Быстрая эвристическая валидация
        heuristic_result = self._heuristic_validation(action, action_result)
        if heuristic_result:
            return {
                "is_valid": heuristic_result.get("is_valid", True),
                "expected_outcome": "",
                "actual_outcome": "",
                "validation_message": heuristic_result.get("validation_message", ""),
                "suggestions": heuristic_result.get("suggestions", []),
                "from_cache": False,
                "heuristic": True
            }
        
        # 2. Проверка кэша
        cache_key = self._get_cache_key(action, action_params, action_result)
        if cache_key in self._validation_cache:
            cached_result = self._validation_cache[cache_key]
            return {
                **cached_result,
                "from_cache": True
            }
        
        # 3. AI валидация только для критических действий или если эвристика не определила
        if not self._is_critical_action(action):
            # Для некритических действий возвращаем успешную валидацию
            result = {
                "is_valid": True,
                "expected_outcome": "",
                "actual_outcome": "",
                "validation_message": "Действие выполнено успешно",
                "suggestions": [],
                "from_cache": False,
                "heuristic": False
            }
            self._validation_cache[cache_key] = result
            return result
        
        # 4. Полная AI валидация для критических действий
        # Извлекаем ожидаемый результат из задачи
        expected_outcome = await self._extract_expected_outcome(action, action_params, task)
        
        # Анализируем фактический результат
        actual_outcome = self._extract_actual_outcome(action_result, page_info)
        
        # Проверяем соответствие через AI
        validation_result = await self._check_outcome_match(
            action,
            action_params,
            expected_outcome,
            actual_outcome,
            task,
            page_info
        )
        
        result = {
            "is_valid": validation_result.get("is_valid", True),
            "expected_outcome": expected_outcome,
            "actual_outcome": actual_outcome,
            "validation_message": validation_result.get("message", ""),
            "suggestions": validation_result.get("suggestions", []),
            "from_cache": False,
            "heuristic": False
        }
        
        # Сохраняем в кэш
        self._validation_cache[cache_key] = result
        
        # Ограничиваем размер кэша (храним последние 100 результатов)
        if len(self._validation_cache) > 100:
            # Удаляем самый старый элемент
            oldest_key = next(iter(self._validation_cache))
            del self._validation_cache[oldest_key]
        
        return result
    
    async def _extract_expected_outcome(
        self,
        action: str,
        action_params: Dict[str, Any],
        task: str
    ) -> str:
        """
        Извлечение ожидаемого результата действия из задачи через AI
        
        Args:
            action: Название действия
            action_params: Параметры действия
            task: Текущая задача
            
        Returns:
            Описание ожидаемого результата
        """
        description = action_params.get("description", "") or action_params.get("element_description", "") or ""
        
        # Специальная обработка для type_text
        if action == "type_text":
            text = action_params.get("text", "")
            element_desc = action_params.get("element_description", "")
            return f"Текст '{text}' должен быть введен в поле '{element_desc}'. Страница НЕ должна измениться после ввода текста (только заполнение поля). Страница изменится только после клика на кнопку поиска или если есть автопоиск."
        
        prompt = f"""Проанализируй задачу и действие, определи ожидаемый результат действия.

ЗАДАЧА: {task}

ДЕЙСТВИЕ: {action}
ПАРАМЕТРЫ: {json.dumps(action_params, ensure_ascii=False)}

ОПИСАНИЕ ЭЛЕМЕНТА: {description}

Определи, какой результат ожидается от этого действия в контексте задачи.

Примеры:
- Если действие click_element("Откликнуться") в задаче "откликнись на вакансию" → ожидается: открытие формы отклика или переход на страницу отклика
- Если действие click_element("Добавить") в задаче "добавь картошку фри" → ожидается: товар добавлен в корзину (изменился счетчик корзины, появилось сообщение об успехе)
- Если действие navigate("hh.ru") в задаче "найди вакансии" → ожидается: переход на сайт hh.ru

Ответь кратко (1-2 предложения): какой результат ожидается от этого действия?"""

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Ты помощник для анализа ожидаемых результатов действий в браузере."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.3
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Не удалось определить ожидаемый результат: {str(e)}"
    
    def _extract_actual_outcome(
        self,
        action_result: Dict[str, Any],
        page_info: Dict[str, Any]
    ) -> str:
        """
        Извлечение фактического результата действия из результата и состояния страницы
        
        Args:
            action_result: Результат выполнения действия
            page_info: Информация о странице после действия
            
        Returns:
            Описание фактического результата
        """
        outcomes = []
        
        # Проверяем изменение страницы
        page_changed = action_result.get("page_changed", False)
        url_before = action_result.get("url_before", "")
        url_after = action_result.get("url_after", "")
        title_before = action_result.get("title_before", "")
        title_after = action_result.get("title_after", "")
        dom_changed = action_result.get("dom_changed", False)
        
        if page_changed:
            if url_before != url_after:
                outcomes.append(f"URL изменился: {url_before} → {url_after}")
            if title_before != title_after:
                outcomes.append(f"Заголовок изменился: '{title_before}' → '{title_after}'")
            if dom_changed:
                outcomes.append("DOM изменился (появились новые элементы или изменилась структура)")
        
        # Проверяем новые элементы
        new_elements = action_result.get("new_elements", {})
        has_new_modals = new_elements.get("new_modals", False)
        has_new_forms = new_elements.get("new_forms", False)
        if has_new_modals:
            outcomes.append("Появилось модальное окно")
        if has_new_forms:
            outcomes.append("Появилась форма")
        if new_elements.get("new_interactive_elements"):
            outcomes.append("Появились новые интерактивные элементы")
        
        # Проверяем модальные окна и формы
        modal_detected = action_result.get("modal_detected", False)
        form_detected = action_result.get("form_detected", False)
        if modal_detected:
            modal_info = action_result.get("modal_info", {})
            if modal_info.get("has_form"):
                outcomes.append(f"Открылось модальное окно с формой ({modal_info.get('input_count', 0)} полей)")
            else:
                outcomes.append("Открылось модальное окно")
        if form_detected:
            outcomes.append(f"Появилась форма ({action_result.get('form_count', 0)} форм)")
        
        # Проверяем успешность действия
        success = action_result.get("success", False)
        if not success:
            error = action_result.get("error", "Unknown error")
            outcomes.append(f"Действие завершилось ошибкой: {error}")
        
        # Проверяем сообщение результата
        message = action_result.get("message", "")
        
        # Специальная обработка для type_text: успех = текст введен в поле
        # Определяем type_text по сообщению или по отсутствию изменений страницы при успехе
        is_type_text = ("введен" in message.lower() and "поле" in message.lower()) or \
                       ("type_text" in message.lower()) or \
                       (success and not page_changed and not has_new_modals and not has_new_forms and message)
        
        if is_type_text:
            if success:
                if message:
                    return f"{message}. Страница не изменилась (это нормально для type_text - страница изменится только после клика на кнопку поиска)."
                else:
                    return "Текст успешно введен в поле. Страница не изменилась (это нормально для type_text - страница изменится только после клика на кнопку поиска)."
            else:
                return "Текст не был введен в поле"
        
        if message:
            outcomes.append(f"Сообщение: {message}")
        
        if not outcomes:
            if page_changed:
                return "Страница изменилась, но детали не определены"
            else:
                return "Страница не изменилась, действие могло не сработать"
        
        return "; ".join(outcomes)
    
    async def _check_outcome_match(
        self,
        action: str,
        action_params: Dict[str, Any],
        expected_outcome: str,
        actual_outcome: str,
        task: str,
        page_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Проверка соответствия фактического результата ожидаемому через AI
        
        Args:
            action: Название действия
            action_params: Параметры действия
            expected_outcome: Ожидаемый результат
            actual_outcome: Фактический результат
            task: Текущая задача
            page_info: Информация о странице
            
        Returns:
            Результат проверки с информацией о соответствии
        """
        description = action_params.get("description", "") or action_params.get("element_description", "") or ""
        
        # Получаем краткую информацию о текущей странице
        current_url = page_info.get("url", "")
        current_title = page_info.get("title", "")
        interactive_count = len(page_info.get("interactive_elements", []))
        
        prompt = f"""Проверь, соответствует ли фактический результат действия ожидаемому результату.

ЗАДАЧА: {task}

ДЕЙСТВИЕ: {action}
ОПИСАНИЕ ЭЛЕМЕНТА: {description}

ОЖИДАЕМЫЙ РЕЗУЛЬТАТ: {expected_outcome}

ФАКТИЧЕСКИЙ РЕЗУЛЬТАТ: {actual_outcome}

ТЕКУЩАЯ СТРАНИЦА:
- URL: {current_url}
- Заголовок: {current_title}
- Интерактивных элементов: {interactive_count}

Ответь в формате JSON:
{{
  "is_valid": true/false,  # соответствует ли результат ожидаемому
  "message": "краткое объяснение",  # почему результат валиден или не валиден
  "suggestions": ["предложение 1", "предложение 2"]  # что делать дальше, если результат не валиден
}}

ВАЖНО:
- Если действие должно было открыть форму/модальное окно, но форма не появилась → is_valid = false
- Если действие должно было добавить товар в корзину, но нет признаков добавления → is_valid = false
- Если действие должно было перейти на другую страницу, но URL не изменился → is_valid = false
- Если страница не изменилась после действия, которое должно было её изменить → is_valid = false
- Если действие завершилось ошибкой → is_valid = false"""

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Ты помощник для валидации результатов действий в браузере. Отвечай строго в формате JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=300,
                temperature=0.3
            )
            
            content = response.choices[0].message.content.strip()
            
            # Пытаемся распарсить JSON
            try:
                # Убираем markdown код блоки если есть
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.strip()
                
                data = json.loads(content)
                return {
                    "is_valid": bool(data.get("is_valid", True)),
                    "message": data.get("message", ""),
                    "suggestions": data.get("suggestions", [])
                }
            except json.JSONDecodeError:
                # Если не удалось распарсить JSON, анализируем текст
                content_lower = content.lower()
                is_valid = "не валиден" not in content_lower and "не соответствует" not in content_lower and "ошибка" not in content_lower
                return {
                    "is_valid": is_valid,
                    "message": content[:200],
                    "suggestions": []
                }
        except Exception as e:
            return {
                "is_valid": True,  # По умолчанию считаем валидным, если не удалось проверить
                "message": f"Не удалось проверить результат: {str(e)}",
                "suggestions": []
            }
    
    async def check_task_completion(
        self,
        task: str,
        history: List[Dict[str, Any]],
        page_info: Dict[str, Any],
        completed_steps: List[str],
        extracted_info: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Проверка выполнения задачи через AI на основе истории действий и текущего состояния
        
        Args:
            task: Текущая задача
            history: История действий
            page_info: Информация о текущей странице
            completed_steps: Список выполненных шагов
            extracted_info: Извлеченная информация
            
        Returns:
            Результат проверки выполнения задачи
        """
        # Формируем краткую историю последних действий
        recent_history = history[-10:] if len(history) > 10 else history
        history_summary = []
        for action_record in recent_history:
            action = action_record.get("action", "")
            result = action_record.get("result", {})
            success = result.get("success", False)
            page_changed = result.get("page_changed", False)
            history_summary.append(f"- {action}: успех={success}, страница_изменилась={page_changed}")
        
        history_text = "\n".join(history_summary)
        
        # Формируем информацию о выполненных шагах
        steps_text = "\n".join([f"- {step}" for step in completed_steps]) if completed_steps else "Нет выполненных шагов"
        
        # Формируем информацию об извлеченной информации
        extracted_text = "\n".join([f"- {desc}: {text[:100]}..." for desc, text in list(extracted_info.items())[:5]]) if extracted_info else "Нет извлеченной информации"
        
        current_url = page_info.get("url", "")
        current_title = page_info.get("title", "")
        
        prompt = f"""Проверь, выполнена ли задача на основе истории действий и текущего состояния.

ЗАДАЧА: {task}

ВЫПОЛНЕННЫЕ ШАГИ:
{steps_text}

ИЗВЛЕЧЕННАЯ ИНФОРМАЦИЯ:
{extracted_text}

ИСТОРИЯ ПОСЛЕДНИХ ДЕЙСТВИЙ:
{history_text}

ТЕКУЩАЯ СТРАНИЦА:
- URL: {current_url}
- Заголовок: {current_title}

Ответь в формате JSON:
{{
  "is_completed": true/false,  # выполнена ли задача
  "completion_percentage": 0-100,  # процент выполнения задачи
  "message": "краткое объяснение",  # почему задача выполнена или не выполнена
  "missing_steps": ["шаг 1", "шаг 2"],  # какие шаги еще не выполнены (если есть)
  "suggestions": ["предложение 1", "предложение 2"]  # что нужно сделать для завершения задачи
}}

ВАЖНО:
- Анализируй задачу целиком, а не отдельные действия
- Учитывай все требования из задачи (количество, качество, последовательность)
- Если задача требует выполнения нескольких шагов - проверь, все ли шаги выполнены
- Если задача требует извлечения информации - проверь, извлечена ли нужная информация
- Если задача требует выполнения действий (отклик, добавление в корзину) - проверь, выполнены ли эти действия"""

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "Ты помощник для проверки выполнения задач в браузере. Отвечай строго в формате JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,
                temperature=0.3
            )
            
            content = response.choices[0].message.content.strip()
            
            # Пытаемся распарсить JSON
            try:
                # Убираем markdown код блоки если есть
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.strip()
                
                data = json.loads(content)
                return {
                    "is_completed": bool(data.get("is_completed", False)),
                    "completion_percentage": int(data.get("completion_percentage", 0)),
                    "message": data.get("message", ""),
                    "missing_steps": data.get("missing_steps", []),
                    "suggestions": data.get("suggestions", [])
                }
            except (json.JSONDecodeError, ValueError):
                # Если не удалось распарсить JSON, анализируем текст
                content_lower = content.lower()
                is_completed = "выполнена" in content_lower or "завершена" in content_lower or "готово" in content_lower
                return {
                    "is_completed": is_completed,
                    "completion_percentage": 50 if is_completed else 30,
                    "message": content[:300],
                    "missing_steps": [],
                    "suggestions": []
                }
        except Exception as e:
            return {
                "is_completed": False,
                "completion_percentage": 0,
                "message": f"Не удалось проверить выполнение задачи: {str(e)}",
                "missing_steps": [],
                "suggestions": []
            }

