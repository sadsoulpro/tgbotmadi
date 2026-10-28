from __future__ import annotations

from html import escape

from .analytics import METRICS, metrics
from .db import Store


def render_dashboard(store: Store, period: str, start, end, start_raw: str, end_raw: str) -> str:
    totals, sources, months, actions = metrics(store, start, end)
    options = ''.join(f'<option value="{key}"{" selected" if period == key else ""}>{label}</option>'
                      for key, label in (("all", "Всё время"), ("month", "Текущий месяц"),
                                         ("week", "Текущая неделя"), ("custom", "Свои даты")))
    body = (f'<form method="get" action="/admin"><label>Период<select name="period">{options}</select></label>'
            f'<label>С даты<input type="date" name="from" value="{escape(start_raw)}"></label>'
            f'<label>По дату<input type="date" name="to" value="{escape(end_raw)}"></label>'
            '<button>Показать</button></form>'
            '<p class="dashboard-note"><strong>Как читать статистику</strong><br>Показаны люди, впервые запустившие бота в выбранный период. Тестовые аккаунты исключены. '
            'События воронки учитываются за всё время прохождения этих пользователей. '
            'Точный подсчёт всех входящих действий ведётся с момента этого обновления; старые действия полностью восстановить нельзя.</p>')
    body += f'<div class="grid"><div class="metric">Действий в периоде<b>{actions}</b></div>'
    for key, label in METRICS:
        body += f'<div class="metric">{escape(label)}<b>{totals[key]}</b></div>'
    body += '</div>'
    body += '<article><h3>Воронка</h3><small>«Отправлено» — бот выдал шаг; «Перешёл» — человек открыл следующий шаг. Telegram не подтверждает прослушивание аудио.</small><table><tr><th>Шаг</th><th>Люди</th></tr>'
    for key, label in (("part1", "Отправлено на шаге 1"), ("part2", "Перешёл на шаг 2"),
                       ("part4", "Дошёл до части 4"), ("part7", "Дошёл до части 7"),
                       ("result", "Получил результат")):
        body += f'<tr><td>{label}</td><td>{totals[key]}</td></tr>'
    body += '</table></article>'
    headers = ''.join(f'<th>{escape(label)}</th>' for _, label in METRICS)
    body += f'<article><h3>По источникам</h3><small class="scroll-hint">Остальные столбцы справа. Проведите по таблице влево.</small><table><tr><th>Метка</th>{headers}</tr>'
    for source, counts in sorted(sources.items(), key=lambda row: (-row[1]["starts"], row[0])):
        body += f'<tr><td>{escape(source)}</td>' + ''.join(f'<td>{counts[key]}</td>' for key, _ in METRICS) + '</tr>'
    body += '</table></article>'
    body += f'<article><h3>По месяцам</h3><small class="scroll-hint">Остальные столбцы справа. Проведите по таблице влево.</small><table><tr><th>Месяц первого запуска</th>{headers}</tr>'
    for month, counts in sorted(months.items(), reverse=True):
        body += f'<tr><td>{month}</td>' + ''.join(f'<td>{counts[key]}</td>' for key, _ in METRICS) + '</tr>'
    body += '</table></article>'
    survey = store.all("""SELECT s.part,s.source,s.reason,COUNT(*) AS n FROM survey_answers s
        JOIN users u ON u.telegram_id=s.telegram_id WHERE u.is_test=0
        GROUP BY s.part,s.source,s.reason ORDER BY s.part,s.source,s.reason""")
    body += '<article><h3>Что остановило</h3><table><tr><th>Часть</th><th>Источник</th><th>Причина</th><th>Ответов</th></tr>'
    for row in survey:
        body += f'<tr><td>{row["part"]}</td><td>{escape(row["source"] or "(без метки)")}</td><td>{escape(row["reason"])}</td><td>{row["n"]}</td></tr>'
    body += '</table></article>'
    tags = store.all("""SELECT tag FROM lead_magnets UNION SELECT detail AS tag FROM events
        WHERE kind='start' AND detail IS NOT NULL AND detail!='' ORDER BY tag""")
    body += '<article><h3>Лид-магниты и теги</h3><table><tr><th>Тег</th><th>Пришли</th><th>Начали</th><th>Результат</th></tr>'
    for row in tags:
        tag = row["tag"]
        visits = store.one("""SELECT COUNT(DISTINCT e.telegram_id) AS n FROM events e
            JOIN users u ON u.telegram_id=e.telegram_id AND u.is_test=0
            WHERE e.kind='start' AND e.detail=?""", (tag,))["n"]
        started = store.one("""SELECT COUNT(DISTINCT r.telegram_id) AS n FROM runs r
            JOIN users u ON u.telegram_id=r.telegram_id AND u.is_test=0 WHERE r.source=?""", (tag,))["n"]
        results = store.one("""SELECT COUNT(DISTINCT r.telegram_id) AS n FROM runs r
            JOIN users u ON u.telegram_id=r.telegram_id AND u.is_test=0
            WHERE r.source=? AND r.completed_at IS NOT NULL""", (tag,))["n"]
        body += f'<tr><td>{escape(tag)}</td><td>{visits}</td><td>{started}</td><td>{results}</td></tr>'
    body += '</table></article>'
    return body
