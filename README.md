# geotaxi-python

`geotaxi-python` is a high performance UDP server which receives the real-time position of taxis and store them into redis. It can handle the positions of thousands of taxis simultaneously.

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

# Development

Use [APITaxi_devel](https://github.com/openmaraude/APITaxi_devel) to run the project locally.

## Run unit tests

To run unittests, install and run tox:

```
$> pip3 install tox
$> tox
```

## Change jsonschema

If you want to change the jsonschema of a message, you can do so by editing the variable API_SCHEMA in geotaxi/jsonschema_definition and the run `geotaxi-generate-jsonschema`. It will generate geotaxi/jsonschema.py for you.
**Never edit geotaxi/jsonschema.py by hand.**

# FAQ

**Why rewrite geotaxi?**

geotaxi was originally written in C for performances reasons, but the code became really difficult to maintain. Huge refactoring was needed, and it wa faster to rewrite in Python, which provides good enough performances for our needs.

**Is there any functional difference between geotaxi and geotaxi-python?**

Yes. `geotaxi` (C version) sends messages to fluentd through a UDP socket. The Python library we use to send fluentd messages only supports TCP (see the [Github issue](https://github.com/fluent/fluent-logger-python/issues/75), so `geotaxi-python` requires to setup Fluentd to accept TCP, like:

```
<source>
  @type forward
  port 24224
</source>

<match geotaxi.position>
  @type stdout
</match>
```

**How can I generate fake traffic to test geotaxi?**

Use [scripts/generate-traffic.py](scripts/generate-traffic.py).

```
usage: generate-traffic.py [-h] [--host HOST] [--port PORT] [-s SLEEP]
                           [--api-key API_KEY] [--operator OPERATOR]
                           [num]

positional arguments:
  num                   Number of messages to send

optional arguments:
  -h, --help            show this help message and exit
  --host HOST           geotaxi host
  --port PORT           geotaxi port
  -s SLEEP, --sleep SLEEP
                        Time to sleep between two messages
  --api-key API_KEY     API key, to set if server has authentication enabled
  --operator OPERATOR   Operator name. Must be the owner of --api-key if
                        authentication is enabled.
```

**How can I know if geotaxi drops packets?**

Install `netstat` (with `apt-get install net-tools`) and run [./scripts/netstat.py](scripts/netstat.py). From another shell, generate some traffic, then press enter in the first shell.

The script reads netstat counters before and after you press enter, and displays the differences between these counters. Packets are lost if any of the counters `packet receive errors`, `packets to unknown port received` or `receive buffer errors` increase.

Note packets can be received by geotaxi but dropped because the receive queue is full. In this case, geotaxi displays a warning message.
