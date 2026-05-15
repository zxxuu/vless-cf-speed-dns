FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache ca-certificates git

COPY . /app

RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
