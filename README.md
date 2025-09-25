# Print Queue (FastAPI)

Мікросервіс, що приймає PDF у "чергу друку" і імітує друк у памʼяті процесу.

## Технології
- Python 3.12
- FastAPI + Uvicorn
- PyPDF2 (підрахунок сторінок PDF)
- Зберігання тільки в RAM (без БД)

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API буде на `http://127.0.0.1:8000`, документація:
- Swagger: `http://127.0.0.1:8000/docs`

## Ендпойнти

### 1) Створити задачу (додати pdf файл у чергу)

**POST** `/jobs`

Параметри:
- `file` — PDF до 10 МБ (обовʼязковий параметр)
- `title` — (необовʼязковий)

Відповідь `201`: # скорочений об'єкт
```json
{ "job_id": "uuid", 
  "filename": "document.pdf", 
  "pages": 5, 
  "status": "queued", 
  "created_at": "2025-09-25T12:00:00Z"
}
```

Можливі помилки:
- `400` — відсутній файл / не PDF / >10 МБ
- `415` — неправильний тип файлу (не pdf`)

**cURL приклад:**
```bash
curl -X POST "http://127.0.0.1:8000/jobs" \
  -F "file=@sample.pdf;type=application/pdf" \
  -F "title=My PDF"
```

### 2) Список задач

**GET** `/jobs?status=queued|printing|done|canceled|error`

Відповідь `200`: масив обʼєктів # повний об'єкт
```json
{ "job_id": "uuid", 
  "filename": "document.pdf", 
  "pages": 5, 
  "status": "queued", 
  "created_at": "2025-09-25T12:00:00Z",
  "updated_at": "2025-09-25T12:01:00Z"
}
```

```bash
curl "http://127.0.0.1:8000/jobs" # Всі статуси
curl "http://127.0.0.1:8000/jobs?status=printing" # Один статус
```

### 3) Отримати одну задачу

**GET** `/jobs/{id}`

Відповідь `200`: повний обʼєкт.

```bash
curl "http://127.0.0.1:8000/jobs/<JOB_ID>"
```

### 4) Скасувати одну задачу

**POST** `/jobs/{id}/cancel`

Дозволено лише зі станів `queued` або `printing`.

- `200` — повертає оновлений обʼєкт (якщо `queued`, статус одразу `canceled`; якщо `printing`, стан стане `canceled` протягом друку)
- `409` — якщо стан не дозволяє
- `404` — якщо не знайдено

```bash
curl -X POST "http://127.0.0.1:8000/jobs/<JOB_ID>/cancel"
```

### 5) Завантажити сирцевий файл

**GET** `/jobs/{id}/file` — повертає PDF (заголовок `Content-Disposition: attachment`).

```bash
curl -OJ "http://127.0.0.1:8000/jobs/<JOB_ID>/file"
```

## Нотатки по реалізації

- Валідація розміру (≤ 10 МБ) і MIME типу (`application/pdf`).
- `PyPDF2` використовується для підрахунку сторінок. Некоректні PDF -> `400`.
- Зміни стану: `queued` -> `printing` -> `done`. При скасуванні: `queued/printing` -> `canceled`. У випадку винятку -> `error`.
- Друк симулюється в окремому **background task** (`asyncio`), посторінково з невеликою затримкою. 
- Затримку можна збільшити для тестування дії скасування задачі.
- Отримання списку підтримує фільтр `status`.
