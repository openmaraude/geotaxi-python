# geotaxi-python

`geotaxi-python` is the python rewrite of `geotaxi`, a high performance UDP server which receives the real-time position of taxis and store them into redis. It can handle the positions of thousands of taxis simultaneously.

# Usage

```
usage: geotaxi [--help] [-v] [-h HOST] [-p PORT] [--redis-host REDIS_HOST]
               [--redis-port REDIS_PORT] [--disable-fluent]
               [--fluent-host FLUENT_HOST] [--fluent-port FLUENT_PORT]
               [--auth-enabled] [--api-url API_URL]
```

Install:

```
$> pip install -e .
$> export API_KEY=xxx # if authentication is set
$> geotaxi -h 0.0.0.0 \
  --fluentd-host fluentd \
  --redis-host redis \
  --api-url 'http://api:5000/' \
  --auth-enabled \
  -v
```

`geotaxi` creates two processes: one two receive data on the UDP socket and one to process these messages. These two processes communicate through a Python [Queue](https://docs.python.org/2/library/multiprocessing.html#multiprocessing.Queue) object. This queue has a hardcoded size. If data are retrieved faster than they are processed, the queue might be full and messages could be lost.

To get the current queue size, send signal `SIGUSR1`:

```
$> kill -s SIGUSR1 <pid>
```

# FAQ

**Why rewrite geotaxi?**

geotaxi was originally written in C for performances reasons, but the code became really difficult to maintain. Huge refactoring were needed, and it wa faster to rewrite in Python, which provides good enough performances for our needs.
