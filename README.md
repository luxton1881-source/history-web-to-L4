---
title: History Web To L4
emoji: 📋
colorFrom: blue
colorTo: indigo
sdk: static
pinned: false
---

# Форма отчета ТО L4
Статический веб-интерфейс для заполнения отчетов ТО L4 из Telegram-бота.

## Реестр способов обращения к Google Таблицам

| Назначение | Таблица (ID) | Лист | Метод обращения | Столбцы / Диапазон | Описание |
|---|---|---|---|---|---|
| Справочники и мастер | `1PfWdhYCPCM4zbhV76qk5wcXIfQZzU7qSRwLlxkN7Jx0` | `Данные` | Google GViz API (прямой GET) | `C` (ФИО), `D` (ID ТГ), `I` (Виды работ), `J` (АВР), `M` (Область), `N` (Код города) | Загрузка фамилии мастера, списков стандартных работ, АВР и кодов городов для сборщика имени свича без задержек |
| Поиск адреса по ТКД | `1esH5e9nWVuE0ZtKc3TvKxSGGKweBoNEeetnREgoekyg` | `Крамер` | Google GViz API (SQL `tq`) | `Q` (ТКД), `G` (Адрес) | Запрос вида `SELECT G WHERE Q = '{cleanTkd}' LIMIT 1` для мгновенного получения адреса дома |
| Сохранение отчетов | `1PfWdhYCPCM4zbhV76qk5wcXIfQZzU7qSRwLlxkN7Jx0` | Регионы (`Суми`, `Харків`, `Чернігів`) | Python gspread / `key.json` | `A:I` | Выполняется ботом по реакции диспетчера |
