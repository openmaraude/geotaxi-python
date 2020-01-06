import argparse
import hashlib
import ujson as json
import logging, logging.config
import multiprocessing
import os
import queue
import signal
import socket
import sys
import time
import urllib

from geotaxi import jsonschema

from fluent.sender import FluentSender
from redis import Redis
from redis.exceptions import RedisError
import requests


logger = logging.getLogger(__name__)



class GeoTaxi:
    """GeoTaxi logic."""
    def __init__(self, redis, fluent=None, auth_enabled=False, api_url=None, api_key=None):
        self.redis = redis
        self.fluent = fluent

        self.auth_enabled = auth_enabled
        if self.auth_enabled:
            self.api_url = api_url
            self.api_key = api_key
            self.users = self.get_api_users()

    def get_api_users(self):
        """Retrieve {user_name: api_key} from APITaxi /users endpoint."""
        users_url = urllib.parse.urljoin(self.api_url, 'users')
        resp = requests.get(
            users_url,
            headers={
                'X-Version': '2',
                'X-Api-Key': self.api_key
            }
        )
        resp.raise_for_status()

        return {
            row['name']: row['apikey']
            for row in resp.json()['data']
        }

    def check_hash(self, data, from_addr):
        """If auth is enabled, make sure data has a valid hash."""
        if not self.auth_enabled:
            return True

        user_key = self.users.get(data['operator'])
        if not user_key:
            logger.warning('User %s not valid', data['operator'])
            return False

        valid_hash = hashlib.sha1(''.join(
            map(str,
                [
                data['timestamp'],
                data['operator'],
                data['taxi'],
                data['lat'],
                data['lon'],
                data['device'],
                data['status'],
                data['version'],
                user_key
        ])).encode('utf8')).hexdigest()

        if valid_hash == data['hash']:
            return True

        self.run_redis_action(
            'ZINCRBY',
            'badhash_operators',
            1,
            data['operator']
        )
        self.run_redis_action(
            'ZINCRBY',
            'badhash_taxis_ids',
            1,
            data['taxi']
        )
        from_ip = from_addr[0]
        self.run_redis_action(
            'ZINCRBY',
            'badhash_ips',
            1,
            from_ip
        )
        return False

    @staticmethod
    def validate_convert_coordinates(data):
        data['lon'], data['lat'] = float(data['lon']), float(data['lat'])
        return -90 <= data['lat'] <= 90 and -180 <= data['lon'] <= 180

    def parse_message(self, b_message, from_addr):
        try:
            message = b_message.decode('utf-8')
        except UnicodeDecodeError:
            logger.warning('Invalid UTF-8 message received from %s:%s', *from_addr)
            return None

        try:
            data = json.loads(message)
        except ValueError:
            logger.warning('Badly formatted JSON received from %s:%s: %s', *from_addr, message)
            return None

        try:
            jsonschema.validate(data)
        except jsonschema.JsonSchemaException as exc:
            logger.warning('Invalid request received from %s:%s: %s', *from_addr, exc.message)
            return None

        if not self.validate_convert_coordinates(data):
            logger.warning('Invalid coordinates: %s %s from %s',
                data['lon'],
                data['lat'],
                data['operator'])
            return None
        return data

    def send_fluent(self, data):
        """Send message to fluentd."""
        if not self.fluent:
            return
        self.fluent.emit('position', data)

    def run_redis_action(self, action, *params):
        action = getattr(self.redis, action.lower())

        # Run action
        try:
            action(*params)
        except socket.error:
            logger.error(
                'Error while running redis action %s %s',
                action.__name__.upper(),
                ' '.join([str(param) for param in params])
            )
        except redis.RedisError as e:
            logger.error(
                'Error while running redis action %s %s %s',
                action.__name__.upper(),
                ' '.join([str(param) for param in params]),
                e
            )

    def update_redis(self, data, from_addr):
        now = int(time.time())
        from_ip = from_addr[0]

        # HSET taxi:<id>
        self.run_redis_action(
            'HSET',
            'taxi:%s' % data['taxi'],
            data['operator'],
            '%s %s %s %s %s %s' % (data['timestamp'], data['lat'], data['lon'], data['status'],
                                   data['device'], data['version'])
        )
        # GEOADD geoindex
        self.run_redis_action(
            'GEOADD',
            'geoindex',
            data['lon'],
            data['lat'],
            data['taxi']
        )
        # GEOADD geoindex_2
        self.run_redis_action(
            'GEOADD',
            'geoindex_2',
            data['lon'],
            data['lat'],
            '%s:%s' % (data['taxi'], data['operator'])
        )
        # ZADD timestamps
        self.run_redis_action(
            'ZADD',
            'timestamps',
            {'%s:%s' % (data['taxi'], data['operator']): now}
        )
        # ZADD timestamps_id
        self.run_redis_action(
            'ZADD',
            'timestamps_id',
            {data['taxi']: now}
        )
        # SADD ips:<operator>
        self.run_redis_action(
            'SADD',
            'ips:%s' % data['operator'],
            from_ip
        )

    def handle_messages(self, msg_queue):
        logger.info('Worker started!')

        # SIGUSR1 can be sent on the master process to display the queue size.
        # Let's ignore the signal on workers in case the administrator sent the
        # signal on the worker PID by mistake.
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)

        while True:
            try:
                message, from_addr = msg_queue.get()

                data = self.parse_message(message, from_addr)
                if not data:
                    continue

                logger.debug('Received from %s:%s: %s', *from_addr, data)

                if not self.check_hash(data, from_addr):
                    continue

                self.send_fluent(data)
                self.update_redis(data, from_addr)
            # Raised when parent calls os.kill()
            except KeyboardInterrupt:
                return
            except Exception as exc:
                logger.error('Exception %s, continue execution', str(exc))


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

    redis = Redis(host=args.redis_host, port=args.redis_port)

    geotaxi = GeoTaxi(
        redis,
        fluent=fluent,
        auth_enabled=args.auth_enabled, api_url=args.api_url, api_key=api_key
    )

    run_server(args.workers, args.host, args.port, geotaxi)
