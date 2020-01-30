import hashlib
import ujson as json
import time
import urllib
import requests
import signal
import socket
import logging
from redis.exceptions import RedisError

from geotaxi import jsonschema

logger = logging.getLogger("geotaxi")

class Worker:
    """GeoTaxi worker."""
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

        pipe = self.redis.pipeline()

        self.run_redis_action(
            pipe,
            'ZINCRBY',
            'badhash_operators',
            1,
            data['operator']
        )
        self.run_redis_action(
            pipe,
            'ZINCRBY',
            'badhash_taxis_ids',
            1,
            data['taxi']
        )
        from_ip = from_addr[0]
        self.run_redis_action(
            pipe,
            'ZINCRBY',
            'badhash_ips',
            1,
            from_ip
        )
        pipe.execute()
        return False

    @staticmethod
    def validate_convert_coordinates(data):
        data['lon'], data['lat'] = float(data['lon']), float(data['lat'])
        return -90 <= data['lat'] <= 90 and -180 <= data['lon'] <= 180

    def parse_message(self, b_message, from_addr):
        try:
            message = b_message.decode('utf-8')
        except UnicodeDecodeError:
            logger.warning('Invalid UTF-8 message received from %s:%s data: %s', *from_addr, b_message)
            return None

        try:
            data = json.loads(message)
        except ValueError:
            logger.warning('Badly formatted JSON received from %s:%s: %s', *from_addr, message)
            return None

        try:
            jsonschema.validate(data)
        except jsonschema.JsonSchemaException as exc:
            logger.warning('Invalid request received from %s:%s: %s, data: %s',
             *from_addr,
             exc.message,
             data)
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

    def run_redis_action(self, pipe, action, *params):
        action = getattr(pipe, action.lower())

        # Run action
        try:
            action(*params)
        except socket.error:
            logger.error(
                'Error while running redis action %s %s',
                action.__name__.upper(),
                ' '.join([str(param) for param in params])
            )
        except RedisError as e:
            logger.error(
                'Error while running redis action %s %s %s',
                action.__name__.upper(),
                ' '.join([str(param) for param in params]),
                e
            )

    def update_redis(self, pipe, data, from_addr):
        now = int(time.time())
        from_ip = from_addr[0]

        # HSET taxi:<id>
        self.run_redis_action(
            pipe,
            'HSET',
            'taxi:%s' % data['taxi'],
            data['operator'],
            '%s %s %s %s %s %s' % (data['timestamp'], data['lat'], data['lon'], data['status'],
                                   data['device'], data['version'])
        )
        # GEOADD geoindex
        self.run_redis_action(
            pipe,
            'GEOADD',
            'geoindex',
            data['lon'],
            data['lat'],
            data['taxi']
        )
        # GEOADD geoindex_2
        self.run_redis_action(
            pipe,
            'GEOADD',
            'geoindex_2',
            data['lon'],
            data['lat'],
            '%s:%s' % (data['taxi'], data['operator'])
        )
        # ZADD timestamps
        self.run_redis_action(
            pipe,
            'ZADD',
            'timestamps',
            {'%s:%s' % (data['taxi'], data['operator']): now}
        )
        # ZADD timestamps_id
        self.run_redis_action(
            pipe,
            'ZADD',
            'timestamps_id',
            {data['taxi']: now}
        )
        # SADD ips:<operator>
        self.run_redis_action(
            pipe,
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
                pipe = self.redis.pipeline()
                self.update_redis(pipe, data, from_addr)
                pipe.execute()
            # Raised when parent calls os.kill()
            except KeyboardInterrupt:
                return
            except Exception as exc:
                logger.error('Exception %s, continue execution', str(exc))
