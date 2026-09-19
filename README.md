# UralDocs

Внутренняя база знаний на Next.js, FastAPI и PostgreSQL с расширением pgvector. API поддерживает вход, управление учётными записями и документами. Отдельный worker индексирует PDF с текстовым слоем, TXT и Markdown. Поиск, чат и Telegram добавляются в следующих этапах.

## Локальный запуск

Нужны Docker Engine и Docker Compose. Из корня репозитория:

```sh
cp .env.example .env
```

Задайте в `.env` собственный `POSTGRES_PASSWORD`. URL подключения к БД формируется из переменных `POSTGRES_*`; при внешней БД можно задать `DATABASE_URL` напрямую. Если меняете адрес веб-приложения, обновите `APP_ORIGIN`. Для локального HTTP оставьте `COOKIE_SECURE=false`; при HTTPS задайте `true`.

```sh
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

Сервис `migrate` применяет Alembic-миграции после готовности PostgreSQL. API и worker запускаются после успешной миграции, веб-приложение — после готовности API. `live` проверяет процесс API, `ready` выполняет запрос к БД. Веб-страницы: <http://localhost:3000/ru> и <http://localhost:3000/en>.

Создайте первого администратора и демонстрационного пользователя. Команда дважды запросит каждый пароль без вывода на экран; пароли длиной от 12 до 1024 символов. Если admin уже существует или логин занят, команда завершается ошибкой и ничего не перезаписывает.

```sh
docker compose run --rm -it api python -m app.cli bootstrap
```

Логины по умолчанию — `admin` и `demo`. Их можно изменить: `bootstrap --admin <логин> --demo-user <логин>`. Фиксированных паролей нет. Пароли хранятся только как хеши Argon2id.

### Операторские команды

```sh
docker compose run --rm -it api python -m app.cli create-admin <логин>
docker compose run --rm -it api python -m app.cli reset-admin <логин>
docker compose run --rm -it api python -m app.cli disable-admin <логин>
```

Пароли не передаются через аргументы командной строки или переменные окружения. `reset-admin` отзывает все сеансы администратора. Последнего активного администратора отключить нельзя. Сброс пароля не включает отключённую учётную запись.

## API и доступ

Веб-приложение проксирует запросы `/api/*` к FastAPI через сервер Next.js. Браузер обращается к тому же origin, на котором открыта страница. Для изменения данных API требует заголовок `Origin`, точно совпадающий с `APP_ORIGIN`. Прямые вызовы API должны передавать его явно.

| Метод и путь | Назначение |
| --- | --- |
| `GET /health/live` | Процесс API работает |
| `GET /health/ready` | БД доступна |
| `POST /auth/login` | Вход, тело `{"username":"...","password":"..."}` |
| `POST /auth/logout` | Выход и отзыв текущего сеанса |
| `GET /auth/me` | Текущий пользователь |
| `GET /admin/users` | Список пользователей роли `user` |
| `POST /admin/users` | Создание пользователя `user` |
| `POST /admin/users/{id}/disable` | Отключение пользователя |
| `POST /admin/users/{id}/reset-password` | Установка нового постоянного пароля |
| `POST /admin/documents` | Загрузка файла, поле multipart `file` |
| `GET /admin/documents` | Список документов и статусы |
| `GET /admin/documents/{id}` | Статус одного документа |
| `DELETE /admin/documents/{id}` | Удаление оригинала и индекса |
| `POST /admin/documents/{id}/reindex` | Повторная обработка документа |
| `POST /admin/documents/reindex` | Повторная обработка всех документов |

Административные методы доступны только роли `admin`. API управляет только учётными записями роли `user`: создание и изменение другого `admin`, отключение себя и сброс собственного пароля через API запрещены. Администраторов обслуживает оператор через CLI. При сбросе пароля администратор задаёт новый постоянный пароль в теле `{"password":"..."}` и передаёт его пользователю вне приложения. Обязательной смены при следующем входе нет.

Загрузка принимает только PDF, TXT, `.md` и `.markdown`. Тип определяется по расширению и признакам содержимого, а не по заявленному MIME. Размер файла ограничен `MAX_UPLOAD_BYTES` (по умолчанию 10 МиБ); запрос ограничен до разбора multipart. Новая запись получает `pending`. Worker извлекает текст, сохраняет фрагменты с номером страницы PDF или диапазоном строк текстового файла и переводит запись в `ready`. Пустой PDF, скан без текстового слоя, повреждённый PDF и текст не в UTF-8 переходят в `failed` с причиной. После перезапуска worker снова берёт незавершённую работу по истечении `WORKER_LEASE_SECONDS`.

В `demo` фрагменты доступны для текстового поиска без embeddings. Индекс текущей конфигурации определяется сочетанием `status=ready`, текущего `generation` и совпадения `config_signature` с режимом; в `real_ai` подпись также учитывает адрес и модель embeddings. API возвращает вычисленный признак `index_current`. При изменении режима или модели запустите переиндексацию. Ошибка обработки не оставляет готовых фрагментов. Удаление документа удаляет оригинал и фрагменты каскадно.

### Контракт индекса для поиска

Для поиска соединяйте `document_chunks.document_id = documents.id` и выбирайте только `documents.status = 'ready'`, `document_chunks.generation = documents.generation` и `documents.config_signature = config_signature(settings)` из `app.kb`. В `real_ai` дополнительно требуется `document_chunks.embedding IS NOT NULL`. Фрагменты хранятся в порядке `chunk_index`; `page_number` задан для PDF, `line_start` и `line_end` — для TXT/Markdown. Текстовый индекс PostgreSQL — GIN по `to_tsvector('simple', document_chunks.text)`. Оригинал находится только в `documents.original` и не должен возвращаться поисковым API.

В текущей сборке вызов внешнего сервиса embeddings отключён: при `KB_MODE=real_ai` обработка завершится `failed` с причиной «Сервис embeddings недоступен». Режим `demo` работает без внешнего API.

Сеансы хранятся в БД по SHA-256 хешу случайного токена; браузер получает токен в cookie с `HttpOnly`, `SameSite=Lax` и настраиваемым `Secure`. На каждом запросе проверяются срок сеанса и состояние пользователя. Просроченный сеанс удаляется при обращении; при успешном входе удаляются все просроченные сеансы. Выход, отключение и сброс пароля отзывают сеансы. Настройка `SESSION_HOURS` задаёт срок сеанса.

## Разработка и проверки

Для backend нужен Python 3.12. Основные тесты используют временную SQLite-базу и не требуют запущенного Compose.

```sh
cd api
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Тесты блокировок в `api/tests/test_auth_postgres.py` запускаются при заданном `TEST_POSTGRES_URL`; без него pytest пропускает эти тесты. Для проверки всего backend suite на PostgreSQL из корня репозитория после настройки `.env` выполните:

```sh
docker compose up -d --wait db
docker compose run --rm --no-deps -v "$PWD/api:/app" api sh -c 'pip install -q -r requirements-dev.txt && TEST_POSTGRES_URL="$(python -c "from app.config import settings; print(settings.database_url)")" pytest -q'
```

Для web нужен Node.js 22:

```sh
cd web
npm ci
npm run lint
npm run typecheck
npm run build
```

Для остановки сервисов: `docker compose down`. Данные PostgreSQL сохраняются в томе `postgres_data`; команда `docker compose down -v` удаляет этот том.
