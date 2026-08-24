# EduTutor — Шпаргалка по управлению процессами

## ⚡ Быстрый старт (одна команда)

```powershell
# Автозапуск frontend + backend через скрипт
cd c:\otus\project_work
python run_server.py
```

---

## 🛑 Остановка всех процессов EduTutor

### Шаг 1: Найти процессы на портах 5173 (frontend) и 8000 (backend)

```powershell
# Посмотреть, что слушает порты 5173 и 8000
netstat -ano | findstr ":5173 :8000"
```

Пример вывода:
```
TCP    0.0.0.0:5173    0.0.0.0:0    LISTENING    12345
TCP    0.0.0.0:8000    0.0.0.0:0    LISTENING    67890
```

Последнее число — **PID** процесса.

### Шаг 2: Убить процессы по PID

```powershell
# Замени 12345 и 67890 на реальные PID из предыдущей команды
Stop-Process -Id 12345 -Force
Stop-Process -Id 67890 -Force
```

### Альтернатива: Убить ВСЕ процессы Python и Node.js

⚠️ **Осторожно!** Это убьёт все процессы Python/Node на системе.

```powershell
# Убить все python процессы
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force

# Убить все node процессы (если нужно)
Get-Process -Name node -ErrorAction SilentlyContinue | Stop-Process -Force
```

### Альтернатива: По названию

```powershell
# Найти процессы по имени/vite/uvicorn
tasklist | findstr /i "python node uvicorn vite"
```

---

## 🚀 Запуск серверов

### Подготовка виртуального окружения

```powershell
cd c:\otus\project_work
.venv\Scripts\Activate.ps1
```

### Backend (порт 8000) — Терминал 1

```powershell
# Вариант 1: Через uvicorn (рекомендуется)
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000

# Вариант 2: Через скрипт автозапуска
python run_server.py

# Проверка здоровья API
curl http://localhost:8000/api/health
```

### Frontend (порт 5173) — Терминал 2

```powershell
cd c:\otus\project_work\frontend
npm run dev
```

### Открыть в браузере

| Сервис | URL |
|--------|-----|
| UI | http://localhost:5173 |
| API Docs | http://localhost:8000/docs |
| Health Check | http://localhost:8000/api/health |

---

## ✅ Проверка запуска

```powershell
# Проверить, что порты заняты
netstat -ano | findstr ":5173 :8000"

# Проверить frontend (должен ответить HTML)
curl http://localhost:5173

# Проверить backend (должен вернуть JSON)
curl http://localhost:8000/api/health
```

---

## 🐛 Troubleshooting

### Порт уже занят

```powershell
# Найти进程, занимающий порт 8000
netstat -ano | findstr ":8000"

# Убить
Stop-Process -Id <PID> -Force
```

### Виртуальное окружение не активировано

```powershell
# Проверить, активировано ли
$env:VIRTUAL_ENV

# Активировать
c:\otus\project_work\.venv\Scripts\Activate.ps1
```

### OLE DB/PowerShell ошибки

Если `Get-NetTCPConnection` не работает — используй `netstat -ano` вместо неё.

---

## 📋 Полная команда остановки и запуска (copy-paste)

```powershell
# === ОСТАНОВКА ===
netstat -ano | findstr ":5173 :8000" | ForEach-Object { $_.Trim().Split(' ')[-1] } | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# === ЗАПУСК BACKEND (в Терминале 1) ===
cd c:\otus\project_work
.venv\Scripts\Activate.ps1
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000

# === ЗАПУСК FRONTEND (в Терминале 2) ===
cd c:\otus\project_work\frontend
npm run dev
```
