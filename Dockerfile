FROM python:3.11-slim
WORKDIR /app
RUN pip install --upgrade pip
COPY requirements.txt /app/
RUN pip install -r requirements.txt
COPY . /app
ENV DJANGO_SETTINGS_MODULE=eventsite.settings
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "eventsite.wsgi:application"]
