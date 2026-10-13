FROM python:3.12-alpine
RUN apk add --no-cache python3 ca-certificates
COPY activation_server.py /usr/local/bin/activation_server.py
CMD ["python3", "/usr/local/bin/activation_server.py"]
