---
name: sessions
description: Manage Claude Code sessions — list, search, delete, rename, move, export, and continue past sessions. Use when the user wants to see, organize, or clean up their Claude Code sessions, or asks "/sessions".
---

# Session Manager

Управляет сессиями Claude Code на локальной машине через TUI или текстовые команды.

## Когда использовать

- Пользователь пишет `/sessions`
- Пользователь говорит "покажи мои сессии", "удали старые сессии", "найди сессию про X"
- Пользователь хочет вернуться к предыдущему разговору

## Перед запуском — проверка зависимостей

Прежде чем запустить TUI, проверь что `textual` установлен:

```bash
python3 -c "import textual" 2>/dev/null || pip3 install --user textual --quiet
```

Если `pip3` недоступен — сообщи пользователю:
> Нужна библиотека textual. Установите: `pip3 install textual`

## Режимы работы

**1. Интерактивный TUI** (по умолчанию, когда пользователь пишет просто `/sessions`):

```bash
python3 ~/.claude/scripts/sessions.py
```

Управление в TUI:
- `↑↓` — навигация
- `/` — поиск
- `Del` — удалить
- `r` — переименовать
- `m` — переместить в другой проект
- `e` — экспортировать в Markdown
- `c` — продолжить сессию в Claude
- `u` — обновить резюме
- `q` — выйти

**2. Текстовые команды** (когда пользователь просит конкретное действие):

```bash
python3 ~/.claude/scripts/sessions.py list
python3 ~/.claude/scripts/sessions.py delete <8-char-id>
python3 ~/.claude/scripts/sessions.py rename <id> "Новое название"
python3 ~/.claude/scripts/sessions.py health
python3 ~/.claude/scripts/sessions.py test
```

## Примеры

**Пользователь:** "/sessions"
→ Запусти TUI: `python3 ~/.claude/scripts/sessions.py`

**Пользователь:** "удали сессию a14a5940"
→ `python3 ~/.claude/scripts/sessions.py delete a14a5940`

**Пользователь:** "покажи список сессий"
→ `python3 ~/.claude/scripts/sessions.py list`

**Пользователь:** "переименуй сессию 401d5d10 в 'Работа с Google Sheets'"
→ `python3 ~/.claude/scripts/sessions.py rename 401d5d10 "Работа с Google Sheets"`

**Пользователь:** "инструмент работает?"
→ `python3 ~/.claude/scripts/sessions.py health`

## Как читать результат

- TUI запускается интерактивно — после выхода управление вернётся в чат
- Текстовые команды возвращают вывод напрямую — покажи пользователю как есть
- При ошибках смотри лог: `~/.claude/session-backups/sessions.log`

## Перенос на VPS

```bash
scp ~/.claude/skills/sessions.md claude@<VPS_IP>:~/.claude/skills/
scp ~/.claude/scripts/sessions.py claude@<VPS_IP>:~/.claude/scripts/
```

На VPS работает идентично через SSH-терминал.
