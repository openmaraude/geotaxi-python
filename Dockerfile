##### DEV IMAGE #####

FROM ubuntu:24.04 AS devenv

ENV DEBIAN_FRONTEND=noninteractive
ENV DEBCONF_NONINTERACTIVE_SEEN=true

RUN apt-get update && apt-get install -y \
  git \
  less \
  python3-pip \
  sudo \
  vim

RUN pip3 install virtualenv

# Create user and add in sudo
RUN useradd geotaxi
RUN echo "geotaxi ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER geotaxi
ENV VIRTUAL_ENV=/venv
ENV PATH=/venv/bin/:$PATH

WORKDIR /git/geotaxi-python

COPY devenv/entrypoint.sh /
ENTRYPOINT ["/entrypoint.sh"]

CMD ["geotaxi", "-h", "0.0.0.0", "-p", "8080", "--redis-host", "redis", "--fluent-host", "fluentd", "--api-url", "http://api:5000/", "-v"]


##### PROD IMAGE #####

FROM ubuntu:20.04

RUN apt-get update && apt-get install -y \
  python3-pip

COPY . /app
WORKDIR /app

RUN pip3 install .

RUN useradd geotaxi
USER geotaxi

COPY entrypoint.sh /
CMD ["/entrypoint.sh"]
