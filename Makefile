VERSION = $(shell sed -En "s/^__version__[[:blank:]]*=[[:blank:]]*['\"]([0-9\.]+)['\"]/\\1/p" geotaxi/__init__.py)
GIT_TAG = $(shell git tag --points-at HEAD)

all:
	@echo "To build and push Docker image, run make release"
	@echo "Do not forget to update __version__"

release:
	@echo "${GIT_TAG}" | grep -q "${VERSION}" || (echo "__version__ in geotaxi/__init__.py does not match the tag on HEAD. Please update __version__, and tag the current commit with \`git tag <version>\`." ; exit 1)
	docker build -t openmaraude/geotaxi-python:${VERSION} -t openmaraude/geotaxi-python:latest .
	docker push openmaraude/geotaxi-python:${VERSION}
	docker push openmaraude/geotaxi-python:latest
