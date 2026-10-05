FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt
COPY backend ./backend
COPY app/static ./app/static
COPY scripts ./scripts
EXPOSE 10000
CMD ["python", "-m", "backend.hosting"]
