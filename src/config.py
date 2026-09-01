"""
EduTutor — конфигурация (pydantic-settings).

Все настройки читаются из .env (см. .env.example / раздел 14 SPECIFICATION.md).
Ключи API никогда не хардкодятся в коде. Одноимённые поля модели соответствуют
переменным .env 1:1.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта: <project_work>/
BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем .env из корня проекта (если он существует), не перезаписывая
# уже заданные переменные окружения.
load_dotenv(BASE_DIR / ".env", override=False)

# Роли моделей EduTutor (для каскада по провайдерам)
ROLE_TUTOR = "tutor"
ROLE_EXPERT = "expert"
ROLE_CHEAP = "cheap"
ROLE_JUDGE = "judge"


def _absolutize(path: Path, base: Path) -> Path:
    """Преобразует относительный путь в абсолютный относительно base."""
    if path.is_absolute():
        return path
    return (base / path).resolve()


class Settings(BaseSettings):
    """Pydantic-модель настроек EduTutor. Поля соответствуют переменным .env."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM-провайдеры ---
    ROUTERAI_API_KEY: str = Field(default="")
    ROUTERAI_BASE_URL: str = Field(default="https://routerai.ru/api/v1")
    OPENROUTER_API_KEY: str = Field(default="")
    OPENROUTER_BASE_URL: str = Field(default="https://openrouter.ai/api/v1")
    LLM_PRIMARY_PROVIDER: str = Field(default="routerai")

    # --- Модели тьютора / эксперта / судьи ---
    TUTOR_MODEL: str = Field(default="qwen/qwen3.7-flash")
    EXPERT_MODEL: str = Field(default="deepseek/deepseek-v4-flash")
    JUDGE_MODEL: str = Field(default="google/gemini-3.5-flash-lite")
    JUDGE_FALLBACK_MODELS: str = Field(default="google/gemini-3.1-flash-lite")
    # Fallback-модели по провайдеру (для случая 403/недоступности конкретной модели)
    FALLBACK_MODELS: str = Field(default="deepseek/deepseek-v4-flash-0731,qwen/qwen3.7-flash")

    # --- Дешёвые роли и embeddings ---
    EMBEDDING_MODEL: str = Field(default="intfloat/multilingual-e5-small")
    # Провайдер эмбеддингов: "api" (RouterAI embeddings) | "local" (sentence-transformers).
    # По умолчанию "api" — работает без MSVC/VC++ и без локального torch.
    EMBEDDING_PROVIDER: str = Field(default="api")
    # Модель эмбеддингов для API-провайдера (RouterAI). Семейство e5 — как в спецификации.
    EMBEDDING_API_MODEL: str = Field(default="intfloat/multilingual-e5-large")
    # Бэкенд векторного хранилища: "numpy" (портативный, без MSVC) | "chroma" (ChromaDB).
    VECTOR_STORE: str = Field(default="numpy")
    # Гибридный retrieval: векторный поиск + BM25 (Okapi) с fusion через RRF (7.2).
    # BM25 — чистый Python (без зависимостей); обёртка над любым VectorStore.
    HYBRID_RAG: bool = Field(default=True)
    # Адаптивная сложность через LinUCB contextual bandit (модель ученика).
    # true — выбор сложности бандитом (контекст: мастерство/класс/недавний результат);
    # false — эвристика adjust_difficulty (3 верных → ↑, 2 ошибки → ↓).
    ADAPTIVE_BANDIT: bool = Field(default=True)
    CHEAP_MODEL: str = Field(default="google/gemma-3-12b-it")
    CHEAP_FALLBACK_MODEL: str = Field(default="qwen/qwen3.7-flash")
    CHEAP_TIMEOUT_SEC: float = Field(default=30.0, gt=0)

    # --- Ollama (опционально) ---
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434")
    OLLAMA_EMBEDDING_MODEL: str = Field(default="nomic-embed-text")
    OLLAMA_CHEAP_MODEL: str = Field(default="qwen2.5:3b")

    # --- Yandex GPT (опционально, запасной РФ-провайдер; отдельный ключ) ---
    YANDEX_GPT_API_KEY: str = Field(default="")
    YANDEX_GPT_FOLDER_ID: str = Field(default="")
    YANDEX_GPT_MODEL: str = Field(default="yandexgpt-lite")

    # --- Поисковые движки ---
    YANDEX_API_KEY: str = Field(default="")
    YANDEX_FOLDER_ID: str = Field(default="")
    TAVILY_API_KEY: str = Field(default="")
    SEARCH_PRIMARY: str = Field(default="yandex")
    # lesson.edu.ru (Минпросвещения) требует авторизацию (401): только opt-in.
    ENABLE_LESSON_EDU: bool = Field(default=False)
    # ФИПИ (демоверсии ОГЭ/ЕГЭ): банк заданий — антибот (403), доступны демо-страницы. opt-in.
    ENABLE_FIPI: bool = Field(default=False)

    # --- Сбор учебных материалов (crawl4ai) ---
    CRAWL4AI_RESPECT_ROBOTS: bool = Field(default=True)
    CRAWL_RATE_LIMIT_SEC: float = Field(default=1.5, ge=0.0)
    MAX_CRAWL_PAGES: int = Field(default=20, ge=1)
    MAX_TEXTBOOK_SEARCH_SEC: float = Field(default=300.0, gt=0)
    TEXTBOOK_CATALOGS: str = Field(default="lesson.edu.ru,ru.wikibooks.org,resh.edu.ru,rusneb.ru")
    CRAWL4AI_PLAYWRIGHT_ENABLED: bool = Field(default=True)
    CRAWL4AI_PLAYWRIGHT_HEADLESS: bool = Field(default=True)
    CRAWL4AI_PLAYWRIGHT_TIMEOUT_MS: int = Field(default=30000, gt=0)
    MAX_FETCH_CHARS_HTML: int = Field(default=80000, gt=0)
    MAX_FETCH_CHARS: int = Field(default=32000, gt=0)

    # --- Сверка с ФГОС (grade_curriculum) ---
    FGOS_REFERENCE_DIR: Path = Field(default=BASE_DIR / "data" / "fgos_reference")
    TEXTBOOKS_DOWNLOADS_DIR: Path = Field(default=BASE_DIR / "downloads")

    # --- OCR сканированных учебников (3.2) ---
    OCR_LANGUAGES: str = Field(default="ru,en")
    OCR_MIN_TEXT_CHARS: int = Field(default=100, ge=0)
    OCR_PAGE_BUFFER: int = Field(default=3, ge=0)
    OCR_MAX_PAGES: int = Field(default=50, ge=1)
    OCR_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    OCR_DETECT_PAGE_NUMBERS: bool = Field(default=True)
    OCR_FORMULA_ENGINE: str = Field(default="")

    # --- Хранилище ---
    CHROMA_PERSIST_DIR: Path = Field(default=BASE_DIR / "data" / "chroma")
    SOURCES_CACHE_DIR: Path = Field(default=BASE_DIR / "data" / "sources_cache")
    CHECKPOINT_DB: Path = Field(default=BASE_DIR / "data" / "checkpoints.db")
    # Дисковый кэш графов знаний учебников (per-textbook, переживает сессии)
    KNOWLEDGE_GRAPH_DIR: Path = Field(default=BASE_DIR / "data" / "knowledge_graphs")
    # Knowledge Wiki (roadmap #2): персистентные wiki-статьи по subject/topic
    # (markdown + YAML-frontmatter OKF v0.2), накапливаются между сессиями
    KNOWLEDGE_WIKI_DIR: Path = Field(default=BASE_DIR / "data" / "knowledge_wiki")
    # Профили учеников (студенты): data/students/<student_id>.json — персистентные
    # сведения об ученике (имя, тип, класс), чтобы Wiki/мастерство/заметки были
    # персональными, а не общими на всех пользователей.
    STUDENTS_DIR: Path = Field(default=BASE_DIR / "data" / "students")

    # --- Qdrant векторное хранилище (roadmap #1; VECTOR_STORE=qdrant) ---
    # Режим сервера (docker-compose.yml): QDRANT_URL=http://localhost:6333.
    # Embedded-режим (без Docker, персистентный каталог): задать QDRANT_PATH.
    QDRANT_URL: str = Field(default="http://localhost:6333")
    QDRANT_API_KEY: str = Field(default="")
    QDRANT_PATH: Optional[Path] = Field(default=None)

    # --- Лимиты (В-7) ---
    MAX_LLM_CALLS_PER_SESSION: int = Field(default=90, ge=1)
    MAX_COST_USD: float = Field(default=1.0, ge=0.0)
    CHEAP_ALLOWANCE_USD: float = Field(default=0.3, ge=0.0)
    TUTOR_ALLOWANCE_USD: float = Field(default=0.5, ge=0.0)
    JUDGE_ALLOWANCE_USD: float = Field(default=0.5, ge=0.0)
    MAX_QUESTIONS_PER_SESSION: int = Field(default=15, ge=1)
    # Circuit breaker (guardrails.py): N подряд идущих сбоев шага графа → защитная
    # пауза (fail closed) на cooldown, чтобы не долбить недоступный AI-сервис.
    CIRCUIT_BREAKER_THRESHOLD: int = Field(default=3, ge=1)
    CIRCUIT_BREAKER_COOLDOWN_SEC: float = Field(default=30.0, ge=1.0)
    # Агентный intake (спека 5.4): true — intake ведёт agent_loop с function calling
    # (детерминированный фолбэк сохраняется); false — классический пошаговый чек-лист.
    USE_AGENT_INTAKE: bool = Field(default=True)
    # Агент в квизе (спека 7.3.1): true — тьюторинг ведёт agent_loop (модель выбирает
    # следующее действие через tools); false — детерминированный цикл квиза (быстрее,
    # по умолчанию: агентный ход квиза добавляет ~30-60с на ответ из-за 2-3 LLM-вызовов).
    USE_AGENT_TUTOR: bool = Field(default=True)
    # Антидубликат вопросов (спека 7.3.2): cosine-порог семантической близости
    # нового вопроса к уже заданным; при превышении — регенерация (≤ RETRIES раз).
    QUESTION_DEDUPE_THRESHOLD: float = Field(default=0.85, ge=0.0, le=1.0)
    QUESTION_DEDUPE_RETRIES: int = Field(default=2, ge=0)

    # --- Адаптивное обучение (roadmap: scaffolding + spaced repetition) ---
    ENABLE_SCAFFOLDING: bool = Field(default=True)
    ENABLE_SPACED_REPETITION: bool = Field(default=True)
    MAX_HINTS_PER_QUESTION: int = Field(default=2)
    REVIEW_QUIZ_SIZE: int = Field(default=5)
    REVIEW_BANK_MAX_CARDS: int = Field(default=200)
    REVIEW_BANK_DIR: Path = Field(default=BASE_DIR / "data" / "review_bank")
    # TTL бездействия сессии (сек) — устаревшие сессии очищаются сервером
    SESSION_IDLE_TTL_SEC: float = Field(default=1800.0, gt=0)
    # Таймаут одного шага графа (сек) — зависшие операции не блокируют сессию
    RUN_STEP_TIMEOUT_SEC: float = Field(default=300.0, gt=0)
    # Docling (структурированный разбор) — опция: модели скачиваются из HF,
    # в некоторых средах недоступны/медленны; рабочий парсер — pdfplumber.
    DOCLING_ENABLED: bool = Field(default=False)
    # Лимит итераций intake. ЗАМЕЧАНИЕ (дефект спеки): чек-лист — 5-6 вопросов,
    # поэтому лимит 3 (из раздела 14) исчерпывается на 4-м ответе и ломает полный
    # чек-лист. Прагматично поднят до 8: первичный чек-лист + запас на уточнения.
    # ЭКСТРЕННЫЙ старт по «не знаю» управляется streak'ом (В-3), не лимитом.
    MAX_INTAKE_ITERATIONS: int = Field(default=8, ge=0)
    SESSION_TIME_BUDGET_SEC: float = Field(default=900.0, gt=0)
    REQUEST_TIMEOUT: float = Field(default=30.0, gt=0)
    ROUTERAI_TIMEOUT: float = Field(default=120.0, gt=0)

    # --- Observability ---
    PHOENIX_ENABLED: bool = Field(default=True)
    PHOENIX_PROJECT_NAME: str = Field(default="edututor")

    # --- API (расширение заказчика) ---
    API_HOST: str = Field(default="0.0.0.0")
    API_PORT: int = Field(default=8000)

    # --- Оценка стоимости (fallback, если провайдер не вернул total_cost) ---
    COST_PER_1M_PROMPT: float = Field(default=0.14)
    COST_PER_1M_COMPLETION: float = Field(default=0.28)
    ROUTERAI_COST_PER_1M_PROMPT_RUB: float = Field(default=9.0)
    ROUTERAI_COST_PER_1M_COMPLETION_RUB: float = Field(default=18.0)
    QWEN_COST_PER_1M_PROMPT_RUB: float = Field(default=3.1)
    QWEN_COST_PER_1M_COMPLETION_RUB: float = Field(default=13.0)
    RUB_TO_USD_RATE: float = Field(default=0.0111)

    # --- Каталоги ---
    LOGS_DIR: Path = Field(default=BASE_DIR / "logs")
    OUTPUT_DIR: Path = Field(default=BASE_DIR / "output")

    @field_validator(
        "ROUTERAI_API_KEY", "OPENROUTER_API_KEY", "YANDEX_API_KEY",
        "TAVILY_API_KEY", "YANDEX_GPT_API_KEY",
        mode="before",
    )
    @classmethod
    def _strip_keys(cls, v):
        if isinstance(v, str):
            return v.strip().strip('"').strip("'")
        return v

    @field_validator(
        "CRAWL4AI_RESPECT_ROBOTS", "CRAWL4AI_PLAYWRIGHT_ENABLED",
        "PHOENIX_ENABLED", "HYBRID_RAG", "ADAPTIVE_BANDIT",
        mode="before",
    )
    @classmethod
    def _parse_bool(cls, v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "y")
        return bool(v)

    @field_validator(
        "FGOS_REFERENCE_DIR", "TEXTBOOKS_DOWNLOADS_DIR", "CHROMA_PERSIST_DIR",
        "SOURCES_CACHE_DIR", "CHECKPOINT_DB", "KNOWLEDGE_GRAPH_DIR", "LOGS_DIR", "OUTPUT_DIR",
        "QDRANT_PATH", "KNOWLEDGE_WIKI_DIR", "STUDENTS_DIR",
        mode="before",
    )
    @classmethod
    def _absolutize_paths(cls, v):
        if isinstance(v, Path):
            path = v
        elif isinstance(v, str) and v.strip():
            path = Path(v)
        else:
            return v
        return _absolutize(path, BASE_DIR)

    # --- Свойства ---

    @property
    def textbook_catalog_list(self) -> List[str]:
        return [c.strip() for c in self.TEXTBOOK_CATALOGS.split(",") if c.strip()]

    @property
    def fallback_models(self) -> List[str]:
        """Fallback-модели по провайдеру (403/недоступность модели на шлюзе)."""
        return [m.strip() for m in self.FALLBACK_MODELS.split(",") if m.strip()]

    @property
    def judge_fallback_models(self) -> List[str]:
        return [m.strip() for m in self.JUDGE_FALLBACK_MODELS.split(",") if m.strip()]

    @property
    def search_engines(self) -> List[str]:
        """Поисковики по приоритету: primary → yandex/tavily → stepik → lesson_edu → ddgs.

        stepik — легальный источник РФ без ключей (API или HTML-fallback);
        lesson_edu — opt-in (каталог Минпросвещения требует авторизации, ENABLE_LESSON_EDU);
        ddgs — всегда финальный fallback.
        """
        available = []
        if self.YANDEX_API_KEY and self.YANDEX_FOLDER_ID:
            available.append("yandex")
        if self.TAVILY_API_KEY:
            available.append("tavily")
        available.append("stepik")
        if self.ENABLE_LESSON_EDU:
            available.append("lesson_edu")
        if self.ENABLE_FIPI:
            available.append("fipi")
        primary = self.SEARCH_PRIMARY.strip().lower()
        ordered = []
        if primary in available:
            ordered.append(primary)
        for name in ("yandex", "tavily", "stepik", "lesson_edu", "fipi"):
            if name in available and name not in ordered:
                ordered.append(name)
        if "ddgs" not in ordered:
            ordered.append("ddgs")
        return ordered

    def _role_model(self, role: str) -> str:
        """Основная модель роли (без учёта провайдера)."""
        if role == ROLE_TUTOR:
            return self.TUTOR_MODEL
        if role == ROLE_EXPERT:
            return self.EXPERT_MODEL
        if role == ROLE_CHEAP:
            return self.CHEAP_MODEL
        if role == ROLE_JUDGE:
            return self.JUDGE_MODEL
        raise ValueError(f"Неизвестная роль: {role!r}")

    def provider_configs(self, role: str) -> List[dict]:
        """Каскад провайдеров для роли (раздел 4.1).

        Fallback по ПРОВАЙДЕРУ (те же модели на другом шлюзе), не по моделям.
        Судья — только RouterAI (Gemini на RouterAI, без VPN; OpenRouter для судьи
        не используется — К-4). Primary — LLM_PRIMARY_PROVIDER; остальные — fallback.
        Если primary не настроен (нет ключа) — первым идёт доступный.
        """
        role_model = self._role_model(role)
        available: dict[str, dict] = {}

        if self.ROUTERAI_API_KEY:
            available["routerai"] = {
                "name": "routerai",
                "base_url": self.ROUTERAI_BASE_URL,
                "api_key": self.ROUTERAI_API_KEY,
                "model": role_model,
                "timeout": self.ROUTERAI_TIMEOUT,
            }
        # Судья не ходит через OpenRouter (К-4): Judge работает на RouterAI без VPN.
        if self.OPENROUTER_API_KEY and role != ROLE_JUDGE:
            available["openrouter"] = {
                "name": "openrouter",
                "base_url": self.OPENROUTER_BASE_URL,
                "api_key": self.OPENROUTER_API_KEY,
                "model": role_model,
                "timeout": self.REQUEST_TIMEOUT,
            }

        primary = self.LLM_PRIMARY_PROVIDER.strip().lower()
        ordered = []
        if primary in available:
            ordered.append(available.pop(primary))
        for name in sorted(available):
            ordered.append(available[name])
        return ordered

    @property
    def tutor_providers(self) -> List[dict]:
        return self.provider_configs(ROLE_TUTOR)

    @property
    def expert_providers(self) -> List[dict]:
        return self.provider_configs(ROLE_EXPERT)

    @property
    def cheap_providers(self) -> List[dict]:
        return self.provider_configs(ROLE_CHEAP)

    @property
    def judge_providers(self) -> List[dict]:
        return self.provider_configs(ROLE_JUDGE)


def get_settings() -> Settings:
    """Singleton-получение настроек."""
    return Settings()


# Удобный алиас для использования в модулях
settings = get_settings()
