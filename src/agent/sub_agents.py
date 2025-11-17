"""Специализированные sub-агенты для разных типов задач"""
from typing import Dict, Any, Optional, List
import json
import re
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL


class SubAgent:
    """Базовый класс для sub-агентов"""
    
    def __init__(self, name: str, system_prompt: str):
        """
        Инициализация sub-агента
        
        Args:
            name: Имя агента
            system_prompt: Системный промпт для агента
        """
        self.name = name
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.system_prompt = system_prompt
    
    async def analyze(self, context: str, task: str) -> Dict[str, Any]:
        """
        Анализ контекста и задачи
        
        Args:
            context: Контекст страницы
            task: Текущая задача
            
        Returns:
            Результат анализа с рекомендациями
        """
        prompt = f"""<context>
{context[:800]}
</context>

<task>
{task}
</task>

<thinking>
Проведи анализ по шагам:

1. Текущее состояние:
   - Что на странице? Какие элементы доступны?
   - Есть ли модальные окна/формы? (работай с ними ПЕРВЫМИ!)
   - Соответствует ли страница цели задачи?

2. История действий:
   - Последнее действие: что делал?
   - page_changed после последнего действия?
   - Если page_changed=false - НЕ повторяй это действие!
   - Есть ли новые элементы после последнего действия?

3. Динамический контент:
   - SPA/AJAX/lazy loading: учитывай задержки загрузки
   - Если элементов нет - возможно нужна задержка

4. Планирование:
   - Какое действие отличается от последних?
   - Какое действие приблизит к цели?
   - Что может пойти не так? Как предотвратить?

5. Самокритика:
   - Достигнет ли это действие прогресса?
   - Не повторяет ли последние действия?
   - Есть ли альтернативные подходы?
</thinking>

<recommendation>
Предложи конкретные действия, кратко и конкретно. Укажи последовательность действий с обоснованием.
</recommendation>"""

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=400,
                temperature=0.3
            )
            
            return {
                "success": True,
                "analysis": response.choices[0].message.content.strip(),
                "agent": self.name
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "agent": self.name
            }


class NavigationAgent(SubAgent):
    """Агент для навигации и поиска элементов"""
    
    def __init__(self):
        super().__init__(
            "NavigationAgent",
            """<role>
Эксперт по навигации и поиску элементов на веб-страницах. Ты находишь элементы навигации и определяешь оптимальный путь к цели.
</role>

<task>
Найти элементы навигации (меню, ссылки, кнопки, карточки), определить путь к цели, предложить действия.
</task>

<analysis>
Думай пошагово:

1. Поиск элементов:
   - Ищи: меню, ссылки, кнопки, карточки с ссылками, поиск на сайте, breadcrumbs
   - Приоритет: модальные окна > формы > основной контент

2. Проверка результата:
   - Если page_changed=false - НЕ повторяй действие
   - Предложи альтернативу: scroll, другой элемент, поиск

3. Динамический контент:
   - SPA/AJAX: учитывай задержки загрузки
   - Lazy loading: после scroll проверь новые элементы
</analysis>

<examples>
Пример 1: Найти вакансии
→ Ищи ссылку "Вакансии" или используй поиск на сайте

Пример 2: Перейти на страницу
→ Кликни на карточку/ссылку с нужным текстом

Пример 3: Элемент не найден
→ scroll вниз для загрузки контента или используй поиск
</examples>"""
        )


class FormAgent(SubAgent):
    """Агент для работы с формами"""
    
    def __init__(self):
        super().__init__(
            "FormAgent",
            """<role>
Эксперт по работе с формами на веб-страницах. Ты находишь поля ввода, определяешь порядок заполнения и предлагаешь действия.
</role>

<task>
Найти поля ввода, определить порядок заполнения, предложить действия для каждого поля.
</task>

<analysis>
Думай пошагово:

1. Поиск формы:
   - Приоритет: формы в модальных окнах > формы на странице
   - Ищи: input, textarea, select по label/placeholder/aria-label
   - Найди кнопку отправки формы

2. Заполнение:
   - Порядок: сверху вниз
   - textarea: используй type_text с \\n для переносов
   - Если page_changed=false после заполнения - заполни следующее поле или нажми отправку

3. Проверка результата:
   - После AJAX отправки проверь новые элементы/сообщения
   - Если действие не изменило страницу - следующий шаг, не повторение
</analysis>

<examples>
Пример 1: Форма входа
→ email → password → кнопка "Войти"

Пример 2: Форма отклика
→ сопроводительное письмо (textarea) → кнопка "Отправить"

Пример 3: Поле не заполнилось
→ проверь селектор через query_dom
</examples>"""
        )


class ReadingAgent(SubAgent):
    """Агент для чтения и извлечения информации"""
    
    def __init__(self):
        super().__init__(
            "ReadingAgent",
            """<role>
Эксперт по чтению и извлечению информации со страниц. Ты находишь информацию, анализируешь структуру данных и предлагаешь действия для извлечения.
</role>

<task>
Найти информацию, проанализировать структуру данных, предложить действия для извлечения.
</task>

<analysis>
Думай пошагово:

1. Поиск информации:
   - Ищи: списки, таблицы, карточки, статьи с нужной информацией
   - Структура: список элементов, таблица, отдельные блоки
   - Используй visible_text_preview для общего понимания

2. Проверка извлечения:
   - Если информация уже извлечена (extracted_info) - НЕ повторяй!
   - Предложи следующий шаг задачи
   - Если page_changed=false после extract_text - другой подход

3. Динамический контент:
   - Lazy loading: после scroll проверь новые элементы
   - Если is_at_bottom=true - не прокручивай дальше
</analysis>

<recommendations>
- extract_text для конкретных элементов
- visible_text_preview для общего понимания
- Если информации недостаточно - scroll для загрузки дополнительных элементов
- Если элемент не найден - scroll, затем повторный поиск
</recommendations>

<examples>
Пример 1: Извлечь резюме
→ найди блок с текстом резюме → extract_text("резюме")

Пример 2: Список вакансий
→ прокрути → извлеки информацию о каждой вакансии

Пример 3: Уже извлечено
→ переходи к следующему шагу задачи
</examples>"""
        )


class DecisionAgent(SubAgent):
    """Агент для принятия решений о следующих шагах"""
    
    def __init__(self):
        super().__init__(
            "DecisionAgent",
            """<role>
Эксперт по принятию решений о следующих шагах выполнения задачи. Ты анализируешь ситуацию, историю действий и определяешь оптимальный следующий шаг, избегая циклов и повторений.
</role>

<analysis_process>
Думай пошагово (Chain-of-Thought):

1. Текущее состояние:
   - Что сделано? (проверь completed_steps, extracted_info)
   - Что осталось сделать?
   - Соответствует ли текущая страница цели задачи?
   - Есть ли модальные окна/формы? (работай с ними ПЕРВЫМИ!)

2. Анализ истории:
   - Последние 3-5 действий: что делал?
   - page_changed после каждого действия?
   - КРАСНЫЙ ФЛАГ: если page_changed=false - НЕ повторяй действие!
   - Если 2+ одинаковых действий БЕЗ page_changed - это цикл!
   - Были ли ошибки? Как их обработал?

3. Динамический контент:
   - SPA/AJAX: учитывай задержки загрузки
   - Lazy loading: после scroll проверь новые элементы
   - Если элементов нет сразу - возможно нужна задержка

4. Планирование:
   - Какое действие максимально приблизит к цели?
   - Отличается ли от последних действий?
   - Если страница не изменилась - нужен СОВЕРШЕННО ДРУГОЙ подход

5. Самокритика:
   - Достигнет ли это действие прогресса?
   - Не повторяет ли последние действия?
   - Учитывает ли динамический контент?
   - Есть ли альтернативные подходы?
</analysis_process>

<loop_prevention>
ПРЕДОТВРАЩЕНИЕ ЦИКЛОВ:

Правила:
- Если page_changed=false - НЕ повторяй действие
- Если ошибка - НЕ повторяй без изменений
- Если 2+ одинаковых действий - это цикл! Измени стратегию немедленно!

Стратегии при цикле:
- extract_text не работает → scroll → другой элемент/описание
- click_element не работает → другой элемент/прокрутка/альтернативный подход
- navigate не работает → проверь URL, попробуй другой способ навигации
</loop_prevention>

<recommendations>
Рекомендации должны:
- Продвигать задачу к цели
- Избегать повторений последних действий
- Учитывать динамический контент
- Если задача выполнена - предложить task_complete
</recommendations>"""
        )


class SubAgentManager:
    """Менеджер для управления sub-агентами"""
    
    def __init__(self, enable_sub_agents: bool = True):
        """
        Инициализация менеджера sub-агентов
        
        Args:
            enable_sub_agents: Включить ли использование sub-агентов
        """
        self.enable_sub_agents = enable_sub_agents
        self.agents = {
            "navigation": NavigationAgent(),
            "form": FormAgent(),
            "reading": ReadingAgent(),
            "decision": DecisionAgent(),
            "outcome": OutcomeAgent(),
            "dom": DOMSubAgent()
        }
    
    async def get_recommendation(
        self, 
        context: str, 
        task: str, 
        agent_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Получение рекомендации от sub-агента
        
        Args:
            context: Контекст страницы
            task: Текущая задача
            agent_type: Тип агента (navigation, form, reading, decision) или None для автоопределения
            
        Returns:
            Рекомендация от агента
        """
        if not self.enable_sub_agents:
            return {"success": False, "message": "Sub-агенты отключены"}
        
        # Автоопределение типа агента на основе задачи
        if not agent_type:
            agent_type = self._determine_agent_type(task, context)
        
        agent = self.agents.get(agent_type)
        if not agent:
            # Используем DecisionAgent по умолчанию
            agent = self.agents["decision"]
        
        return await agent.analyze(context, task)

    async def query_dom(self, query: str, context: str, page_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Задать конкретный вопрос о структуре страницы DOM Sub-agent'у
        
        Args:
            query: Вопрос о странице (например, "Есть ли на странице поле поиска? Какой у него селектор?")
            context: Контекст страницы (текстовое описание)
            page_info: Информация о странице (словарь с элементами, URL, и т.д.)
            
        Returns:
            Результат с ответом на вопрос от DOM Sub-agent'а
        """
        if not self.enable_sub_agents:
            return {"success": False, "error": "Sub-агенты отключены"}
        
        agent = self.agents.get("dom")
        if not agent:
            return {"success": False, "error": "DOM Sub-agent не найден"}
        
        return await agent.query_dom(query, context, page_info)
    
    async def evaluate_outcome(self, context: str, task: str, last_action: str) -> Dict[str, Any]:
        """Проверка страницы после действия (мастер резюме или продолжение отклика)"""
        if not self.enable_sub_agents:
            return {"success": False, "resume_wizard": False}
        agent = self.agents.get("outcome")
        if not agent:
            return {"success": False, "resume_wizard": False}
        return await agent.evaluate_outcome(context, task, last_action)
    
    def _determine_agent_type(self, task: str, context: str) -> str:
        """
        Определение типа агента на основе задачи и контекста
        
        Args:
            task: Текущая задача
            context: Контекст страницы
            
        Returns:
            Тип агента
        """
        task_lower = task.lower()
        context_lower = context.lower()
        
        # Проверка на навигацию
        nav_keywords = ["найди", "перейди", "открой", "ссылка", "find", "navigate", "go to"]
        if any(keyword in task_lower for keyword in nav_keywords):
            return "navigation"
        
        # Проверка на работу с формами
        form_keywords = ["заполни", "введи", "форма", "поле", "fill", "form", "input", "register", "login"]
        if any(keyword in task_lower or keyword in context_lower for keyword in form_keywords):
            return "form"
        
        # Проверка на чтение информации
        reading_keywords = ["прочитай", "извлеки", "найди информацию", "read", "extract", "get information"]
        if any(keyword in task_lower for keyword in reading_keywords):
            return "reading"
        
        # По умолчанию используем DecisionAgent
        return "decision"


class DOMSubAgent(SubAgent):
    """Агент для ответов на конкретные вопросы о структуре страницы и элементах"""
    
    def __init__(self):
        super().__init__(
            "DOMSubAgent",
            """<role>
Эксперт по анализу структуры веб-страниц и ответам на вопросы о элементах. Ты отвечаешь на конкретные вопросы о структуре страницы, находишь элементы по описанию и предоставляешь селекторы.
</role>

<task>
Отвечать на вопросы о структуре страницы, находить элементы по описанию, предоставлять селекторы, анализировать состояние элементов.
</task>

<analysis_process>
Chain-of-Thought процесс (думай пошагово):

ШАГ 1: Анализ типа страницы
   - Список элементов (list_item, card) или детальная страница?
   - Есть ли элементы с type="list_item" в списке элементов?
   - Сколько элементов списка видно?
   - Если вопрос о деталях, но страница - список - найди элементы списка!

ШАГ 2: Понимание вопроса
   - Что именно спрашивается? (элемент, селектор, состояние, элемент списка)
   - Какие ключевые слова в вопросе? (письмо, вакансия, кнопка, список и т.д.)
   - Есть ли упоминание "список", "элементы списка", "письма", "вакансии"?

ШАГ 3: Поиск элементов (ВСЕГДА используй селекторы из списка!)
   - Сначала найди элементы с type="list_item" если вопрос про список
   - Ищи в интерактивных элементах (button, link, input, list_item)
   - Проверь clickable_elements внутри list_item если есть
   - Приоритет: элементы in_modal > элементы in_form > основной контент
   - Используй частичное совпадение текста (case-insensitive)
   - Если точного совпадения нет - найди наиболее похожий элемент И УКАЖИ ЕГО СЕЛЕКТОР

ШАГ 4: Формирование ответа
   - Естественным языком, КРАТКО но ДЕТАЛЬНО (максимум 2-3 предложения)
   - ВСЕГДА включай селекторы из списка элементов - НИКОГДА не говори "не указаны в данных"
   - ВСЕГДА описывай визуальное состояние элементов (текст, иконки, стрелки, счетчики, состояние) когда это релевантно для понимания функциональности
   - Если элемент не найден точно - найди похожий и укажи его селектор с объяснением
   - БЕЗ "воды", только факты с селекторами и описанием визуального состояния
</analysis_process>

<search_rules>
КРИТИЧЕСКИ ВАЖНО: Используй ГИБКИЙ ПОИСК по частичному совпадению текста!

ПРАВИЛА ПОИСКА:
1. ЧАСТИЧНОЕ СОВПАДЕНИЕ ТЕКСТА:
   - Ищи элементы по ЧАСТИЧНОМУ совпадению текста (не только точное совпадение)
   - Регистр НЕ важен: "Откликнуться" = "откликнуться" = "ОТКЛИКНУТЬСЯ"
   - Игнорируй лишние пробелы и знаки препинания
   - Примеры: "Откликнуться" найдет "Откликнуться", "откликнуться", "Откликнуться на вакансию"
   
2. КОНТЕКСТНЫЙ ПОИСК:
   - Если вопрос про "кнопку рядом с первой вакансией GO разработчика":
     * Сначала найди элементы, связанные с "GO разработчик" или "Go разработчик"
     * Затем найди кнопки рядом с этими элементами (в том же контейнере/карточке)
     * Учитывай структуру: элементы в одной карточке/блоке находятся рядом
   - Если вопрос про "кнопку в форме" - ищи элементы с in_form=true
   - Если вопрос про "кнопку в модальном окне" - ищи элементы с in_modal=true
   
3. ПОИСК ПОХОЖИХ ЭЛЕМЕНТОВ:
   - Если точного совпадения нет - найди наиболее похожий элемент
   - Учитывай тип элемента (button, link, input)
   - Учитывай контекст (рядом с чем находится элемент)
   - Учитывай aria-label, title, data-атрибуты если они есть
</search_rules>

<answer_rules>
КРИТИЧЕСКИ ВАЖНО: ВСЕГДА используй селекторы из списка элементов! НИКОГДА не говори "не указаны в данных"!

ПРАВИЛА ОТВЕТА:
- Прямо на вопрос, естественным языком, МАКСИМУМ 2-3 предложения
- ВСЕГДА селекторы из списка элементов - если элемент есть в списке, ОБЯЗАТЕЛЬНО укажи его селектор
- ВСЕГДА описывай визуальное состояние элементов (текст, иконки, стрелки, счетчики, состояние) когда это релевантно для понимания функциональности
- Наличие: "да/нет" + селектор + описание визуального состояния (что отображается, какие иконки, стрелки, счетчики)
- Селектор: конкретный селектор БЕЗ лишних слов, формат: "Селектор: [селектор]"
- Визуальное состояние: опиши что отображается на элементе (текст, иконки, стрелки, счетчики, состояние) - это помогает понять функциональность
- Состояние: кратко (текст, счетчики, видимость) + селектор + описание визуального состояния
- Несколько элементов: перечисли с селекторами и описанием визуального состояния, формат: "[селектор1] (что отображается), [селектор2] (что отображается)"
- Элементы списков: если вопрос про список - найди элементы с type="list_item" и укажи их селекторы + описание визуального состояния
- Если элемент не найден точно - найди наиболее похожий в списке и укажи его селектор + кратко объясни почему
- Не найден: кратко почему (1 предложение максимум)
- БЕЗ фраз "не указаны в предоставленных данных", "не указаны в данных", "отсутствуют в списке" - ВСЕГДА используй селекторы!
- БЕЗ длинных объяснений, БЕЗ формальной структуры, БЕЗ "водных" фраз

ЗАПРЕЩЕНО:
✗ "На странице видны вакансии Go разработчика, но конкретные селекторы для этих вакансий не указаны в предоставленных данных..."
✗ "Селекторы не указаны в данных"
✗ "Отсутствуют в списке элементов"
✗ Длинные объяснения без селекторов

ОБЯЗАТЕЛЬНО:
✓ Найди элементы в списке (даже если они не идеально совпадают) и укажи их селекторы
✓ Если есть элементы с type="list_item" - используй их для ответов про списки
✓ Если элемент не найден точно - найди похожий и укажи его селектор
</answer_rules>

<examples>
✗ ПЛОХО (много воды):
"На странице видны вакансии Go разработчика, но конкретные селекторы для этих вакансий не указаны в предоставленных данных. Однако, в видимом тексте упоминается вакансия 'Golang-разработчик в DevPlatform...'"

✓ ХОРОШО (кратко, по делу, с описанием визуального состояния):
"Да, есть вакансии Go разработчика. Первая вакансия: селектор .vacancy-card:first-child, кнопка 'Откликнуться': селектор .vacancy-card:first-child .apply-btn"

✓ ХОРОШО (с описанием визуального состояния):
"Да, есть кнопка 'Откликнуться'. Селектор: .vacancy-card:first-child .apply-btn"

✓ ХОРОШО (с описанием визуального состояния):
"3 кнопки: #submit-btn (Отправить), .cancel-btn (Отмена), .close-btn (Закрыть)"

✓ ХОРОШО (с описанием визуального состояния):
"Форма открылась. Поля: input[name='email'], input[name='password'], кнопка: button[type='submit']"

✓ ОТЛИЧНО (детальное описание визуального состояния):
"Да, есть кнопка с адресом доставки (.context_235.24342) в шапке сайта. Отображается: 'Такой то адрес' с иконкой геолокации и стрелкой вниз, что указывает на возможность изменить адрес."

✓ ОТЛИЧНО (детальное описание счетчика):
"Счетчик корзины показывает число 1. Селектор: .cart-counter. Отображается: 'В корзину 388 ₽' с иконкой корзины."

✓ ОТЛИЧНО (детальное описание кнопки):
"Есть кнопка добавления в корзину. Селектор: .add-to-cart-btn. Отображается: текст '+ 1' с иконкой плюса, что указывает на возможность добавить товар."

✓ ХОРОШО (если не найден точно):
"Похожий элемент: .vacancy-item:first-child (возможно это вакансия Go разработчика). Селектор кнопки: .vacancy-item:first-child .btn-apply"

✓ ХОРОШО (если не найден):
"Нет. Нужна прокрутка страницы"

✓ ХОРОШО (частичное совпадение):
"Да, есть кнопка с текстом 'Откликнуться на вакансию'. Селектор: .vacancy-card:first-child button"

✓ ХОРОШО (контекстный поиск):
"Да, есть кнопка 'Откликнуться' рядом с первой вакансией. Вакансия: .vacancy-item:first-child, кнопка: .vacancy-item:first-child .apply-button"

✓ ХОРОШО (элементы списков):
"Да, есть список писем. Первое письмо: селектор [role='listitem']:first-child, кликабельный элемент внутри: a.mail-link"

✓ ХОРОШО (похожий элемент вместо "не найдено"):
"Похожий элемент: .mail-item:first-child (возможно это письмо). Селектор: .mail-item:first-child"
</examples>"""
        )
    
    def _filter_elements_by_relevance(self, elements: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """
        Умная фильтрация элементов по релевантности к вопросу
        
        Args:
            elements: Список элементов
            query: Вопрос пользователя
            
        Returns:
            Отфильтрованный и отсортированный список элементов по релевантности
        """
        # Защита от None: если elements равен None, заменяем на пустой список
        if elements is None:
            elements = []
        
        # Извлекаем ключевые слова из вопроса (убираем стоп-слова)
        query_lower = query.lower()
        stop_words = {'есть', 'ли', 'на', 'странице', 'какой', 'у', 'неё', 'него', 'какие', 'селектор', 'селекторы', 
                     'элемент', 'элементы', 'кнопка', 'кнопки', 'поле', 'поля', 'рядом', 'с', 'первой', 'второй',
                     'третьей', 'после', 'до', 'в', 'для', 'как', 'что', 'где', 'когда', 'почему'}
        
        # Словарь синонимов для улучшения поиска
        synonyms = {
            'письмо': ['email', 'mail', 'message', 'letter', 'сообщение'],
            'письма': ['emails', 'mails', 'messages', 'letters', 'сообщения'],
            'вакансия': ['job', 'vacancy', 'работа'],
            'вакансии': ['jobs', 'vacancies', 'работы'],
            'список': ['list', 'списки'],
            'элемент': ['item', 'элементы'],
            'кнопка': ['button', 'btn'],
            'ссылка': ['link', 'a']
        }
        
        # Извлекаем значимые слова из вопроса
        words = re.findall(r'\b[а-яёa-z]{3,}\b', query_lower)
        keywords = [w for w in words if w not in stop_words]
        
        # Расширяем ключевые слова синонимами
        expanded_keywords = set(keywords)
        for keyword in keywords:
            if keyword in synonyms:
                expanded_keywords.update(synonyms[keyword])
        keywords = list(expanded_keywords)
        
        # Если нет ключевых слов, возвращаем все элементы
        if not keywords:
            return elements
        
        # Вычисляем релевантность каждого элемента
        scored_elements = []
        for elem in elements:
            score = 0
            elem_text = (elem.get('text', '') or '').lower()
            elem_type = (elem.get('type', '') or '').lower()
            elem_selector = (elem.get('selector', '') or '').lower()
            elem_aria_label = (elem.get('aria_label', '') or '').lower() if elem.get('aria_label') else ''
            
            # Проверяем совпадение ключевых слов
            for keyword in keywords:
                # Точное совпадение в тексте элемента
                if keyword in elem_text:
                    score += 10
                # Частичное совпадение (подстрока)
                elif any(keyword in word for word in elem_text.split()):
                    score += 5
                # Совпадение в селекторе
                if keyword in elem_selector:
                    score += 3
                # Совпадение в aria-label
                if keyword in elem_aria_label:
                    score += 8
                # Совпадение типа элемента с ключевыми словами
                if keyword in ['кнопка', 'button', 'btn'] and elem_type == 'button':
                    score += 5
                if keyword in ['ссылка', 'link'] and elem_type == 'link':
                    score += 5
                if keyword in ['поле', 'input', 'ввод'] and elem_type == 'input':
                    score += 5
                # Бонус за элементы списков (list_item)
                if keyword in ['список', 'list', 'элемент', 'item', 'письмо', 'email', 'mail', 'message', 'вакансия', 'job', 'vacancy'] and elem_type == 'list_item':
                    score += 15  # Высокий бонус за элементы списков
                
                # Бонус за совпадение в родительском контейнере (для контекстного поиска)
                parent_container = elem.get('parent_container')
                if parent_container:
                    container_text = (parent_container.get('text_preview', '') or '').lower()
                    if keyword in container_text:
                        score += 7  # Элемент находится в контейнере, релевантном к вопросу
                
                # Проверяем кликабельные элементы внутри list_item
                if elem_type == 'list_item':
                    clickable_elements = elem.get('clickable_elements', [])
                    # Защита от None: если clickable_elements равен None, заменяем на пустой список
                    if clickable_elements is None:
                        clickable_elements = []
                    for clickable in clickable_elements:
                        clickable_text = (clickable.get('text', '') or '').lower()
                        if keyword in clickable_text:
                            score += 10  # Бонус за совпадение в кликабельных элементах списка
            
            # Бонус за элементы в модальных окнах (высокий приоритет)
            if elem.get('in_modal'):
                score += 20
            # Бонус за элементы в формах
            if elem.get('in_form'):
                score += 10
            # Бонус за элементы с id
            if elem.get('id'):
                score += 5
            # Бонус за элементы списков (list_item) - они важны для поиска элементов в списках
            if elem_type == 'list_item':
                score += 5
            
            scored_elements.append((score, elem))
        
        # Сортируем по релевантности (от большего к меньшему)
        scored_elements.sort(key=lambda x: x[0], reverse=True)
        
        # Возвращаем элементы (без score)
        return [elem for score, elem in scored_elements]
    
    def _format_element_info(self, elem: Dict[str, Any], index: int) -> str:
        """
        Форматирование информации об элементе с большим контекстом
        
        Args:
            elem: Элемент для форматирования
            index: Индекс элемента
            
        Returns:
            Отформатированная строка с информацией об элементе
        """
        parts = [f"{index}."]
        
        # Текст элемента (не обрезаем слишком агрессивно)
        if elem.get("text"):
            text = elem.get('text', '').strip()
            # Ограничиваем до 200 символов вместо 100
            if len(text) > 200:
                text = text[:200] + "..."
            parts.append(f"Текст: '{text}'")
        
        # Селектор (обязательно)
        if elem.get("selector"):
            parts.append(f"Селектор: {elem.get('selector')}")
        
        # Тип элемента
        if elem.get("type"):
            parts.append(f"Тип: {elem.get('type')}")
        
        # Тег
        if elem.get("tag"):
            parts.append(f"Тег: {elem.get('tag')}")
        
        # Контекстная информация
        context_parts = []
        if elem.get('in_modal'):
            context_parts.append("в модальном окне")
        if elem.get('in_form'):
            context_parts.append("в форме")
        if elem.get('visible') is False:
            context_parts.append("невидим")
        if elem.get('id'):
            context_parts.append(f"id={elem.get('id')}")
        if elem.get('aria_label'):
            context_parts.append(f"aria-label='{elem.get('aria_label')[:50]}'")
        
        # Информация о родительском контейнере (для контекстного поиска)
        parent_container = elem.get('parent_container')
        if parent_container:
            container_text = parent_container.get('text_preview', '')
            if container_text:
                # Берем первые 50 символов текста контейнера
                container_preview = container_text[:50] + ("..." if len(container_text) > 50 else "")
                context_parts.append(f"в контейнере '{container_preview}'")
        
        # Информация о элементах списков (list_item)
        if elem.get('type') == 'list_item':
            if elem.get('list_index') is not None:
                context_parts.append(f"индекс в списке: {elem.get('list_index')}")
            if elem.get('clickable_elements'):
                clickable_count = len(elem.get('clickable_elements', []))
                context_parts.append(f"кликабельных элементов внутри: {clickable_count}")
            if elem.get('is_clickable'):
                context_parts.append("сам элемент кликабелен")
        
        if context_parts:
            parts.append(f"({', '.join(context_parts)})")
        
        # Для элементов списков добавляем информацию о кликабельных элементах внутри
        if elem.get('type') == 'list_item' and elem.get('clickable_elements'):
            clickable_info = []
            clickable_elements = elem.get('clickable_elements', [])
            # Защита от None: если clickable_elements равен None, заменяем на пустой список
            if clickable_elements is None:
                clickable_elements = []
            for clickable in clickable_elements[:3]:  # Показываем первые 3
                clickable_text = clickable.get('text', '')[:30]
                clickable_selector = clickable.get('selector', '')
                if clickable_text:
                    clickable_info.append(f"{clickable.get('type')} '{clickable_text}' ({clickable_selector})")
                else:
                    clickable_info.append(f"{clickable.get('type')} ({clickable_selector})")
            if clickable_info:
                parts.append(f"Кликабельные внутри: {', '.join(clickable_info)}")
        
        return " | ".join(parts)
    
    async def query_dom(self, query: str, context: str, page_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ответ на конкретный вопрос о структуре страницы
        
        Args:
            query: Вопрос о странице (например, "Есть ли на странице поле поиска? Какой у него селектор?")
            context: Контекст страницы (текстовое описание)
            page_info: Информация о странице (словарь с элементами, URL, и т.д.)
            
        Returns:
            Результат с ответом на вопрос
        """
        # Формируем информацию о странице для анализа
        interactive_elements = page_info.get("interactive_elements", [])
        # Защита от None: если interactive_elements равен None, заменяем на пустой список
        if interactive_elements is None:
            interactive_elements = []
        visible_text = page_info.get("visible_text_preview", "")
        url = page_info.get("url", "")
        title = page_info.get("title", "")
        
        # Умная фильтрация элементов по релевантности к вопросу
        filtered_elements = self._filter_elements_by_relevance(interactive_elements, query)
        # Защита от None: если filtered_elements равен None, заменяем на пустой список
        if filtered_elements is None:
            filtered_elements = []
        
        # Формируем список элементов для анализа с улучшенным форматированием
        elements_info = []
        # Берем больше элементов (до 200), но приоритизированных по релевантности
        max_elements = min(200, len(filtered_elements))
        for i, elem in enumerate(filtered_elements[:max_elements], 1):
            elem_desc = self._format_element_info(elem, i)
            elements_info.append(elem_desc)
        
        elements_text = "\n".join(elements_info) if elements_info else "Нет интерактивных элементов"
        
        # Динамически определяем лимит на основе размера (минимум 2000 символов, максимум 5000)
        # Это позволяет включить больше элементов, если они релевантны, но не перегружать промпт
        elements_text_limit = min(5000, max(2000, len(elements_text)))
        elements_text_truncated = elements_text[:elements_text_limit]
        
        if len(elements_text) > elements_text_limit:
            elements_text_truncated += f"\n... (показано {max_elements} из {len(interactive_elements)} элементов, отсортированных по релевантности)"
        
        
        prompt = f"""<question>
{query}
</question>

<page_info>
URL: {url}
Заголовок: {title}
</page_info>

<thinking>
1. Определи тип страницы по элементам (список vs детальная)
   - Если вопрос о деталях, но страница - список - укажи это!

2. Проанализируй вопрос:
   - Что спрашивается? (элемент, селектор, состояние)
   - Какие ключевые слова в вопросе? (извлеки существительные и действия)
   - Есть ли контекст? (например, "рядом с вакансией", "в форме", "в модальном окне")
   - Соответствует ли типу страницы?

3. Найди элементы используя ГИБКИЙ ПОИСК:
   - Если вопрос про список (письма, вакансии и т.д.) - сначала найди элементы с type="list_item"
   - Ищи по ЧАСТИЧНОМУ совпадению текста (регистр не важен)
   - Если вопрос про "кнопку рядом с X" - найди сначала X, затем кнопки рядом с ним
   - Для элементов списков проверь clickable_elements внутри list_item
   - Учитывай контекст: элементы в одной карточке/блоке находятся рядом
   - Приоритет: элементы in_modal > элементы in_form > остальные
   - Ищи в интерактивных элементах (button, link, input, list_item), формах, visible_text_preview
</thinking>

<elements>
Элементы (показано {max_elements} наиболее релевантных из {len(interactive_elements)}, отсортированы по релевантности к вопросу):
{elements_text_truncated}
</elements>

<visible_text>
Текст (500 символов): {visible_text[:500]}
</visible_text>

<context>
{context[:800]}
</context>

<answer>
Ответь естественным языком, КРАТКО (максимум 2-3 предложения, 100-200 токенов), ВСЕГДА включай селекторы из списка элементов выше.

КРИТИЧЕСКИ ВАЖНО:
- Используй ГИБКИЙ ПОИСК: ищи по частичному совпадению текста (регистр не важен)
- Если вопрос про "кнопку рядом с X" - найди сначала X, затем кнопки в том же контексте/карточке
- Если селектор есть в списке элементов - ОБЯЗАТЕЛЬНО укажи его
- Если элемент не найден точно - найди наиболее похожий по тексту/типу/контексту и укажи его селектор
- БЕЗ фраз "не указаны в данных" или "не указаны в предоставленных данных"
- БЕЗ длинных объяснений, только факты с селекторами
- Формат: "Да/Нет, [краткое описание]. Селектор: [селектор]"

ФОРМАТ СЕЛЕКТОРОВ (КРИТИЧЕСКИ ВАЖНО!):
- Если селектор начинается с # - используй как есть: "Селектор: #delete" (БЕЗ обратных кавычек!)
- Если селектор начинается с . - используй как есть: "Селектор: .class-name" (БЕЗ обратных кавычек!)
- Если селектор содержит тег и класс - используй как есть: "Селектор: div.MessageListItem__root" (БЕЗ обратных кавычек!)
- Если селектор содержит атрибуты - используй как есть: "Селектор: input[data-qa='search']" (БЕЗ обратных кавычек!)
- ВСЕГДА используй селектор ТОЧНО как он указан в списке элементов выше!
- НЕ изменяй селектор, НЕ добавляй лишние пробелы или символы!
- НЕ используй обратные кавычки вокруг селектора! Используй формат: "Селектор: div.MessageListItem__root" а НЕ "Селектор: `div.MessageListItem__root`"

БЕЗ "водных" фраз, БЕЗ формальной структуры, БЕЗ длинных объяснений!
</answer>"""

        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,  # Краткие ответы без воды, только факты с селекторами
                temperature=0.3
            )
            
            answer = response.choices[0].message.content.strip()
            
            return {
                "success": True,
                "answer": answer,
                "agent": "DOMSubAgent"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "agent": "DOMSubAgent"
            }


class OutcomeAgent(SubAgent):
    """Агент для оценки результата действия (мастер резюме или реальный отклик)"""

    def __init__(self):
        super().__init__(
            "OutcomeAgent",
            "Ты эксперт по оценке результата действий. Определи, ведет ли текущая страница к мастеру создания резюме или к реальному процессу отклика."
        )

    async def evaluate_outcome(self, context: str, task: str, last_action: str) -> Dict[str, Any]:
        prompt = f"""=== ТЕКУЩАЯ ЗАДАЧА ===
{task}

=== ПОСЛЕДНЕЕ ДЕЙСТВИЕ ===
{last_action}

=== СОСТОЯНИЕ СТРАНИЦЫ ПОСЛЕ ДЕЙСТВИЯ ===
{context}

Ответь строго в JSON:
{{
  "resume_wizard": true/false,  # true если страница похожа на мастер создания/редактирования резюме
  "reason": "краткое объяснение"
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0
            )
            content = response.choices[0].message.content.strip()
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = {"resume_wizard": False, "reason": content[:100]}
            return {
                "success": True,
                "resume_wizard": bool(data.get("resume_wizard", False)),
                "reason": data.get("reason", "")
            }
        except Exception as e:
            return {
                "success": False,
                "resume_wizard": False,
                "error": str(e)
            }

