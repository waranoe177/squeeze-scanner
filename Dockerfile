FROM python:3.12-slim
WORKDIR /app
# git not needed at runtime (no clone); build tools only for wheels if any
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scanner/ ./scanner/
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh
RUN sed -i 's/\r$//' ./deploy/entrypoint.sh && chmod +x ./deploy/entrypoint.sh
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["./deploy/entrypoint.sh"]
