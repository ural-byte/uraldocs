# Локальная модель определения языка

`lid.176.ftz` — неизменённая компактная модель [fastText Language Identification](https://fasttext.cc/docs/en/language-identification.html) для 176 языков. Источник файла: <https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz>. Разработчик и распространитель — команда fastText (Facebook AI Research). Согласно официальной странице, модель обучена на Wikipedia, Tatoeba и SETimes и распространяется по [Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Файл включён без изменений; этот раздел сохраняет атрибуцию, ссылку на лицензию и указание источника.

Размер файла — **938 013 байт**. SHA-256: `8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83`. Приложение проверяет размер и хеш перед загрузкой. Python binding [fasttext-wheel 0.9.2](https://github.com/messense/fasttext-wheel) распространяется по MIT; его версия и `numpy==1.26.4` закреплены в `api/requirements.txt`.

Свежий checkout содержит модель; при сборке `api/Dockerfile` копирует её вместе с `app`. Сеть для определения языка во время работы не нужна. Проверка артефакта после checkout:

```sh
shasum -a 256 api/app/data/lid.176.ftz
docker compose --profile telegram build --no-cache telegram
docker compose --profile telegram run --rm --no-deps telegram python -c 'from app.language_id import load_language_model; print(load_language_model().predict("What is this?", k=2))'
```

На чистом `python:3.12-slim` установка всех требований API с закреплёнными версиями, `pip check` и пробное предсказание прошли до включения зависимости в проект. Если модель отсутствует или её хеш изменён, бот прекращает работу до обращения к базе знаний.

## Открытая калибровочная матрица

Значения округлены; пороги применяются к исходным числам. Правило допуска: верхний язык `ru` или `en`, вероятность не ниже 0,60 и разница с ближайшим конкурентом не ниже 0,25. При верхней вероятности ниже 0,60 допускается только узкая английская конструкция `show/list/summarize/describe/explain/find + me/us + the/a/an/all/these/those + объект` длиной от четырёх слов. Отдельные технические сокращения и числа используют язык профиля, обычные одиночные слова отклоняются.

| Вопрос | Первый | Второй | Разница | Итог |
| --- | ---: | ---: | ---: | --- |
| Что такое альфа? | ru 0,912 | uk 0,066 | 0,846 | ru |
| Как загрузить PDF? | ru 0,647 | uk 0,345 | 0,302 | ru |
| Какие документы доступны? | ru 0,983 | tt 0,003 | 0,980 | ru |
| Можно ли импортировать документ? | ru 0,925 | ba 0,019 | 0,906 | ru |
| Поддерживает ли Уралдокс импорт PDF? | ru 0,937 | sr 0,013 | 0,924 | ru |
| What is alpha? | en 0,969 | te 0,009 | 0,960 | en |
| Does UralDocs support PDF import? | en 0,693 | de 0,034 | 0,659 | en |
| How can I import a document? | en 0,672 | ru 0,055 | 0,617 | en |
| How come PDF import fails? | en 0,788 | pt 0,053 | 0,735 | en |
| Can I upload a PDF? | en 0,822 | kn 0,020 | 0,802 | en |
| Where are the documents? | en 0,996 | bn 0,000 | 0,996 | en |
| Which files can I import? | en 0,602 | it 0,065 | 0,537 | en |
| Please summarize the policy | en 0,885 | fa 0,007 | 0,878 | en |
| What documents support PDF? | en 0,676 | de 0,023 | 0,653 | en |
| Show me the PDF documents | de 0,161 | en 0,155 | 0,006 | en по ограниченной конструкции |
| Ich will ein PDF importieren. Geht das? | de 0,995 | es 0,001 | 0,994 | other |
| Hi ha documents disponibles? | it 0,230 | es 0,170 | 0,060 | other |
| Hoe maak ik een document? | nl 1,000 | fr 0,000 | 1,000 | other |
| Come funziona importazione PDF? | it 0,993 | en 0,002 | 0,991 | other |
| Necesito importar un documento PDF | es 0,794 | gl 0,028 | 0,766 | other |
| Какво е документ? | bg 0,693 | mk 0,301 | 0,392 | other |
| Как се качва документ? | bg 0,583 | mk 0,318 | 0,265 | other |
| Како ради увоз докумената? | ru 0,398 | mk 0,212 | 0,186 | other |
| Да ли могу да увезем документ? | mk 0,390 | sr 0,211 | 0,179 | other |

Это открытая регрессия, а не независимая приёмка. Для короткого или смешанного текста вероятности модели могут быть недостаточно уверенными; такой вопрос получает ясную границу RU/EN без доступа к поиску и истории.
