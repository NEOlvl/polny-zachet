# Категория meditation_spots («Медитации»): отчёт

Дата проверки: 20.11.2026. В бота и в `content/` пока ничего не внесено.

## Сводка

- 10 мест, 18 медитаций: дом Джейсона (1), 8 мест по 2 (Джейсон и Лусия отдельно), вершина Ascent Mountain (1). Для 100% нужно 15 из 18; для трофея Positive Mental Attitude нужны все.
- В YAML 18 пунктов (`med_001`…`med_018`, `order: 30`), по одному на медитацию, как считает счётчик игры (Progress > Collections > Exploration > Meditations). Только последний `spoiler: true` (открывается после остальных).
- Названия мест в скобках — оригинал из игры, русская часть — условный перевод, сверить с меню.
- Уверенность: места, числа 18 и 15 — высокая; подсказки — средняя. PowerPyx, games.gg и tech4gamers почти совпадают и считаются одним источником; частично свои описания у Beebom (и game-checklists по сниппету).

## Источники

- PowerPyx: https://www.powerpyx.com/gta-6-all-meditation-spot-locations/ (18.11)
- games.gg: https://games.gg/grand-theft-auto-vi/guides/gta-6-all-meditation-spot-locations/
- tech4gamers: https://gta.tech4gamers.com/gta-6/collectibles/meditation-spots/ (19.11)
- Beebom: https://beebom.com/gta-6-meditation-spots-locations/ (19.11)
- Fextralife: https://gta6.wiki.fextralife.com/All_Meditation_Spot_Locations (в этот раз не открылся)

## Таблица пунктов

Все пункты: места подтверждают PowerPyx, games.gg, Beebom; уверенность высокая; статус «в YAML». Пункты 2–17 попарно (Джейсон/Лусия) по 8 местам.

| id | Название | Подсказка | spoiler |
|---|---|---|---|
| med_001 | Дом Джейсона, балкон (глава 1) | Только в главе 1, позже недоступно | нет |
| med_002–003 | Гладкий песок (Smooth Sand) | Отмель у западного берега Капри | нет |
| med_004–005 | Болото (Swamp) | Причал у вышки посреди Buckskin | нет |
| med_006–007 | Старый камень (Worn Stone) | Берег Emerald у Moonflower | нет |
| med_008–009 | Красные скалы (Red Rocks) | Manifest, пересечение рельсов и шоссе | нет |
| med_010–011 | Водяное колесо (Water Wheel) | Руины фабрики, юг Moreau | нет |
| med_012–013 | Выступ скалы (Jutting Cliff) | Скала у вышки над шахтой, Mount Kalaga | нет |
| med_014–015 | Водопад (Waterfall) | Над водопадом северо-западнее Tallaloko | нет |
| med_016–017 | Побережье (Seaside) | Скалы на пляже восточнее Waning Sands | нет |
| med_018 | Вершина (Ascent Mountain) | Каменный круг на вершине, метка после остальных | да |

## Спорное

- Балкон Джейсона: пропускаемый, нужен ли для трофея — неизвестно (все источники). Для порога 15 из 18 не нужен.
- Seaside: Beebom пишет «в Waning Sands, на песке», остальные — скалистый выступ восточнее; взято большинство.
- Red Rocks: Beebom ставит в «Manifest County», остальные — в каньоне Manifest (Kelly County); в подсказке только каньон.
- Smooth Sand: game-checklists описывает как самую северную отмель у Belpoint; подсказка осторожная.
- Beebom называет «18 мест», игра — 10 мест и 18 медитаций.
- Фильтр Fast Forward требует «50 медитаций» при 18 существующих (PC Gamer); непонятно, повторяемые ли; в пункты не вносилось.
- Зачёт за обоих героев доступен с Coffee Break (глава 2).
- Русские названия мест — мои переводы, не из игры.
- Не открылись: Fextralife; game-checklists и gtaculture видел только по сниппетам. Видео и Reddit не смотрел, `radar export` не запускался.

## Слухи

Не найдено.

## Для «Радара»

Ключ `meditation_spots`, синонимы `[meditation, meditations, meditation_spot, meditate_around_leonida]`.

## Что проверить в игре

1. Английские названия мест в меню Progress и русские переводы.
2. Что зачёт идёт отдельно за каждого героя и в сумме 18.
3. Что метка Ascent Mountain появляется после остальных 17.

## Перенос

```
Copy-Item drafts\meditation_spots.yaml content\meditation_spots.yaml
```
После копии: `pytest`, `ruff`, в боте `/reload` и `/cat meditation_spots`.
