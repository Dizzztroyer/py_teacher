"""
logic/topics.py — встроенный банк тем Python (используется если нет PDF).
"""
from __future__ import annotations
import random

TOPICS: list[dict] = [
    {
        "title": "Типы данных Python",
        "description": "int, float, str, bool, NoneType — базовые типы Python",
        "keywords": "Python data types int float str bool None type()",
    },
    {
        "title": "list — изменяемый список",
        "description": "Изменяемый упорядоченный список. Методы: append, extend, insert, remove, pop, sort",
        "keywords": "Python list append extend insert remove pop sort slicing indexing",
    },
    {
        "title": "tuple — неизменяемый кортеж",
        "description": "Неизменяемый упорядоченный кортеж. Hashable. Распаковка.",
        "keywords": "Python tuple immutable hashable unpacking packing",
    },
    {
        "title": "dict — словарь",
        "description": "Словарь ключ→значение. get, update, pop, items, keys, values, comprehension",
        "keywords": "Python dict dictionary methods get update pop items keys values comprehension",
    },
    {
        "title": "set — множество",
        "description": "Неупорядоченное множество уникальных элементов. Операции: |, &, -, ^",
        "keywords": "Python set frozenset union intersection difference membership O(1)",
    },
    {
        "title": "Функции: *args и **kwargs",
        "description": "*args — произвольные позиционные аргументы (tuple), **kwargs — именованные (dict)",
        "keywords": "Python args kwargs positional keyword arguments unpacking",
    },
    {
        "title": "LEGB — правило областей видимости",
        "description": "Local → Enclosing → Global → Built-in. global, nonlocal.",
        "keywords": "Python LEGB scope local enclosing global builtin nonlocal",
    },
    {
        "title": "Генераторы и yield",
        "description": "Ленивая генерация значений. yield, next(), generator expression, yield from",
        "keywords": "Python generator yield next StopIteration lazy evaluation generator expression",
    },
    {
        "title": "Замыкания (closures)",
        "description": "Внутренняя функция, запоминающая переменные enclosing-области. nonlocal, __closure__",
        "keywords": "Python closure free variable nonlocal __closure__ factory function",
    },
    {
        "title": "Декораторы",
        "description": "Функция, принимающая функцию и возвращающая обёртку. functools.wraps.",
        "keywords": "Python decorator functools.wraps wrapper higher order function stacking",
    },
]

_used: list[str] = []


def reset_used() -> None:
    """Сброс истории использованных тем (для тестов)."""
    global _used
    _used = []


def pick_topic(exclude_titles: list[str] | None = None) -> dict:
    """Выбирает тему из банка, избегая недавно использованных."""
    global _used
    exclude = set(exclude_titles or [])
    available = [t for t in TOPICS if t["title"] not in exclude and t["title"] not in _used]
    if not available:
        # Все темы исчерпаны — сбрасываем и начинаем новый цикл
        _used = []
        available = [t for t in TOPICS if t["title"] not in exclude]
    if not available:
        available = list(TOPICS)

    topic = random.choice(available)
    _used.append(topic["title"])
    if len(_used) > len(TOPICS):
        _used = _used[-len(TOPICS):]
    return topic
