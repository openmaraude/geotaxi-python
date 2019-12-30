FROM ubuntu

RUN apt-get update && apt-get install -y \
  python3-pip

COPY . /app
WORKDIR /app

RUN pip3 install .

RUN useradd geotaxi
USER geotaxi

COPY entrypoint.sh /
CMD ["/entrypoint.sh"]
