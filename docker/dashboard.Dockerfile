FROM python:3.13-slim-bookworm
RUN pip install --no-cache-dir "streamlit==1.67.0" "psycopg[binary]==3.3.8" "pandas>=2.2"
WORKDIR /app
COPY services/dashboard/app.py .
ENV PYTHONUNBUFFERED=1
USER 65534
ENV HOME=/tmp
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
