FROM python:3.13-slim-bookworm
COPY services/agent_operator/server.py /app/server.py
USER 65534
EXPOSE 8090
CMD ["python", "/app/server.py"]
