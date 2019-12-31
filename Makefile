VERSION = $(shell sed -En "s/^__version__[[:blank:]]*=[[:blank:]]*['\"]([0-9\.]+)['\"]/\\1/p" geotaxi/__init__.py)

all:
	@echo "To build and push Docker image, run make release"
	@echo "Do not forget to update __version__"

release:
	docker build -t openmaraude/geotaxi-python:${VERSION} -t openmaraude/geotaxi-python:latest .
	docker push openmaraude/geotaxi-python:${VERSION}
	docker push openmaraude/geotaxi-python:latest
