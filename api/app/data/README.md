# Локальная модель определения языка

`lid.176.ftz` — неизменённая компактная модель [fastText Language Identification](https://fasttext.cc/docs/en/language-identification.html) для 176 языков. Источник файла: <https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz>. Разработчик и распространитель — команда fastText (Facebook AI Research). Согласно официальной странице, модель обучена на Wikipedia, Tatoeba и SETimes и распространяется по [Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Файл включён без изменений; здесь указаны источник, лицензия и атрибуция.

Размер файла — **938 013 байт**. SHA-256: `8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83`. Приложение проверяет размер и хеш перед загрузкой. Python binding [fasttext-wheel 0.9.2](https://github.com/messense/fasttext-wheel) распространяется по MIT; его версия и `numpy==1.26.4` закреплены в `api/requirements.txt`.

Свежий checkout содержит модель; при сборке `api/Dockerfile` копирует её вместе с `app`. Сеть для определения языка во время работы не нужна. Проверка артефакта после checkout:

```sh
shasum -a 256 api/app/data/lid.176.ftz
docker compose --profile telegram build --no-cache telegram
docker compose --profile telegram run --rm --no-deps telegram python -c 'from app.language_id import load_language_model; print(load_language_model().predict("What is this?", k=2))'
```

Если модель отсутствует или её хеш изменён, бот прекращает работу до обращения к базе знаний.

## Открытая калибровка

Для сообщения короче трёх слов возвращается `unclear`: язык берётся из Telegram-профиля, если он русский или английский. Для более длинного сообщения модель выдаёт два наиболее вероятных языка. Нужен отрыв первого от второго не менее 0,25. Порог для `ru` — 0,60, для `en` — 0,38, для иного языка — 0,60. При недостаточной уверенности результат `unclear`, при уверенном ином языке — `other` и сообщение о границе поддержки RU/EN. Грамматические шаблоны не ограничивают допустимые русские и английские вопросы. Явная команда `/ru` или `/en` имеет приоритет над классификацией.

| Вопрос | Первый | Второй | Итог без ручного выбора |
| --- | ---: | ---: | --- |
| Что такое альфа? | ru 0,912 | uk 0,066 | ru |
| Как загрузить PDF? | ru 0,647 | uk 0,345 | ru |
| Какие документы доступны? | ru 0,983 | tt 0,003 | ru |
| Is PDF supported? | en 0,583 | hu 0,047 | en |
| What is alpha? | en 0,969 | te 0,009 | en |
| Where are the documents? | en 0,996 | bn 0,000 | en |
| Show me the PDF documents | de 0,161 | en 0,155 | unclear, язык профиля |
| PDF? | de 0,296 | ja 0,124 | unclear, язык профиля |
| Ich will ein PDF importieren. Geht das? | de 0,995 | es 0,001 | other |
| hola amigo | es 0,647 | pt 0,146 | unclear, язык профиля |
| Hi ha documents disponibles? | it 0,230 | es 0,170 | unclear, язык профиля |
| Моля, покажете документа | ru 0,631 | uk 0,194 | ru, возможная ошибка модели |

Классификация best effort: на коротких, смешанных и похожих языках возможны ошибочные решения. Эта таблица фиксирует открытые примеры и не заменяет независимую приёмку.
