cd "$(dirname "$0")"/..
docker compose run --rm etl python -m etl.data_check