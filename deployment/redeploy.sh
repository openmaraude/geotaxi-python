# To duplicate as redeploy-prod.sh and redeploy-dev.sh
# The following values must be edited:
# - FAILOVER_IP
# - REDIS_HOST
# - REDIS_PASSWORD
# - REDIS_PORT
# - SENTRY_DSN

#!/bin/sh -x

DOCKER_IMAGE=openmaraude/geotaxi-python:latest
CONTAINER_NAME=geotaxi-dev
FAILOVER_IP=x.x.x.x  # Must be configured in the network interfaces

docker rm -f "${CONTAINER_NAME}"

docker run -ti \
	-d \
	--pull=always \
	--restart=unless-stopped \
	-e HOST=0.0.0.0 \
	-e PORT=8080 \
	-e REDIS_HOST=xxx \
	-e REDIS_PORT=xxx \
	-e REDIS_PASSWORD=xxx \
	-e API_URL=https://dev.api.taxi \
	-e VERBOSE=1 \
	-e SENTRY_DSN=xxxxxxxxxxxx \
	-e WORKERS=4 \
	-e DISABLE_FLUENT=true \
	-p "${FAILOVER_IP}:80:8080/udp" \
	--name "$CONTAINER_NAME" \
	"$DOCKER_IMAGE"
