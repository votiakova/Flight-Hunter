FROM python:3.11-slim

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY . .

# Создаём папку для данных
RUN mkdir -p data

CMD ["python", "bot.py"]
