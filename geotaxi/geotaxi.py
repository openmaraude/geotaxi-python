import argparse
import logging
import logging.config
import multiprocessing
import os
import queue
import signal
import socket
import sys
from fluent.sender import FluentSender
from redis import Redis

from geotaxi.worker import Worker

logger = logging.getLogger("geotaxi")


def signal_handler(signals, signum):
    signals.append(signum)


def run_server(workers, host, port, geotaxi):
    msg_queue = multiprocessing.Queue(1024)

    procs = [
        multiprocessing.Process(target=geotaxi.handle_messages, args=(msg_queue,))
        for _ in range(workers)
    ]
    for proc in procs:
        proc.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    sock.settimeout(0.5)

    # Catch Ctrl^C, SIGTERM and SIGUSR1
    signals = []
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
        signal.signal(sig, lambda signum, _: signal_handler(signals, signum))

    while True:
        if signal.SIGINT in signals or signal.SIGTERM in signals:
            for proc in procs:
                os.kill(proc.pid, signal.SIGKILL)
            break

        if signal.SIGUSR1 in signals:
            signals.remove(signal.SIGUSR1)
            sys.stdout.write('Queue size: %s\n' % msg_queue.qsize())
            sys.stdout.flush()

        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue

        try:
            # Put in the queue, but do not block
            msg_queue.put((data, addr), False)
        except queue.Full:
            logger.warning('Queue is full - drop message...')


class FormatWithPID(logging.Formatter):
    def format(self, record):
        record.pid = os.getpid()
        return super(FormatWithPID, self).format(record)


def main():
    parser = argparse.ArgumentParser(
        add_help=False,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--help',
        action='help',
        default=argparse.SUPPRESS,
        help=argparse._('show this help message and exit')
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true', help='Display debug messages'
    )

    parser.add_argument('-h', '--host', type=str, default='127.0.0.1',
                        help='Listen host')
    parser.add_argument('-p', '--port', type=int, default=8080,
                        help='Listen port')

    parser.add_argument('-w', '--workers', type=int,
                        default=max(1, multiprocessing.cpu_count() - 1),
                        help='Number of workers')

    parser.add_argument('--redis-host', type=str, default='127.0.0.1',
                        help='Redis host')
    parser.add_argument('--redis-port', type=str, default=6379,
                        help='Redis port')
    parser.add_argument('--redis-password', type=str, default=None,
                        help='Redis password')

    parser.add_argument('--disable-fluent', action='store_true', default=False,
                        help='If set, do not send logs to fluent')
    parser.add_argument('--fluent-host', type=str, default='127.0.0.1',
                        help='Fluentd host')
    parser.add_argument('--fluent-port', type=int, default=24224,
                        help='Fluentd port')

    parser.add_argument('--auth-enabled', action='store_true', default=False,
                        help='Enable authentication')
    parser.add_argument('--api-url', type=str, default='http://127.0.0.1:5000',
                        help='APITaxi URL, used when authentication is enabled to retrieve users')

    args = parser.parse_args()

    loglevel = logging.DEBUG if args.verbose else logging.INFO
    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,

        'formatters': {
            'default': {
                '()': FormatWithPID,
                'format': '%(asctime)s (pid %(pid)s) %(message)s'
            }
        },
        'handlers': {
            'console': {
               'level': loglevel,
               'class': 'logging.StreamHandler',
               'formatter': 'default',
            }
        },
        'loggers': {
            '': {
                'handlers': ['console'],
                'level': loglevel,
            }
        }
    })

    if not args.auth_enabled:
        logger.warning('Authentication is not enabled')

    api_key = os.getenv('API_KEY')
    if args.auth_enabled and not api_key:
        parser.error('--enable-auth is set but API_KEY environment variable is not set')

    if args.disable_fluent:
        fluent = None
    else:
        fluent = FluentSender('geotaxi', host=args.fluent_host, port=args.fluent_port)

    if args.redis_password:
        redis = Redis(host=args.redis_host, port=args.redis_port, password=args.redis_password)
    else:
        redis = Redis(host=args.redis_host, port=args.redis_port)

    worker = Worker(
        redis,
        fluent=fluent,
        auth_enabled=args.auth_enabled, api_url=args.api_url, api_key=api_key
    )

    run_server(args.workers, args.host, args.port, worker)
