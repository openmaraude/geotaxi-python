import argparse
import asyncio
import hashlib
import json
import logging
import os
import socket
import time
import urllib

from aiofluent import FluentSender
import aioredis
import jsonschema
import requests



logger = logging.getLogger(__name__)


API_MESSAGE = {
    'type': 'object',
    'properties': {
        'operator':  {'type': 'string'},
        'lat':       {'type': 'string'},
        'device':    {'type': 'string'},
        'lon':       {'type': 'string'},
        'timestamp': {'type': 'string'},
        'status':    {'type': 'string'},
        'version':   {'type': 'string'},
        'taxi':      {'type': 'string'},
        'hash':      {'type': 'string'},
    },
    'required': [
        'operator', 'lat', 'device', 'lon', 'timestamp', 'status', 'version', 'taxi', 'hash',
    ]
}


class GeoTaxi:
    """GeoTaxi logic."""
    def __init__(self, redis_host, redis_port, fluent=None,
                 auth_enabled=False, api_url=None, api_key=None):
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.fluent = fluent

        self.auth_enabled = auth_enabled
        if self.auth_enabled:
            self.api_url = api_url
            self.api_key = api_key
            self.users = self.get_api_users()

        self.redis = None

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

    async def check_hash(self, data, from_addr):
        """If auth is enabled, make sure data has a valid hash."""
        if not self.auth_enabled:
            return True

        user_key = self.users.get(data['operator'])
        if not user_key:
            logger.warning('User %s not valid', data['operator'])
            return False

        valid_hash = hashlib.sha1(''.join([
            data['timestamp'],
            data['operator'],
            data['taxi'],
            data['lat'],
            data['lon'],
            data['device'],
            data['status'],
            data['version'],
            user_key
        ]).encode('utf8')).hexdigest()

        if valid_hash == data['hash']:
            return True

        await self.run_redis_action(
            'ZINCRBY',
            'badhash_operators',
            1,
            data['operator']
        )
        await self.run_redis_action(
            'ZINCRBY',
            'badhash_taxis_ids',
            1,
            data['taxi']
        )
        from_ip = from_addr[0]
        await self.run_redis_action(
            'ZINCRBY',
            'badhash_ips',
            1,
            from_ip
        )
        return False

    def parse_message(self, message, from_addr):
        try:
            data = json.loads(message)
        except json.decoder.JSONDecodeError:
            logger.warning('Badly formatted JSON received from %s:%s: %s', *from_addr, message)
            return None

        try:
            jsonschema.validate(instance=data, schema=API_MESSAGE)
        except jsonschema.ValidationError as exc:
            logger.warning('Invalid request received from %s:%s: %s', *from_addr, exc.message)
            return None
        return data

    async def send_fluent(self, data):
        """Send message to fluentd."""
        if not self.fluent:
            return
        await self.fluent.emit('geotaxi', data)

    async def run_redis_action(self, action, *params):
        # Connect to redis
        if not self.redis:
            connstr = 'redis://%s:%s' % (self.redis_host, self.redis_port)

            try:
                self.redis = await aioredis.create_redis_pool(connstr)
            except socket.error as exc:
                logger.error('Unable to connect to redis: %s', exc)
                return

        action = getattr(self.redis, action.lower())

        # Run action
        try:
            await action(*params)
        except socket.error:
            logger.error(
                'Error while running redis action %s %s',
                action.__name__.upper(),
                ''.join([str(param) for param in params])
            )
            return

    async def update_redis(self, data, from_addr):
        now = int(time.time())
        from_ip = from_addr[0]

        # HSET taxi:<id>
        await self.run_redis_action(
            'HSET',
            'taxi:%s' % data['taxi'], data['operator'],
            '%s %s %s %s %s %s' % (data['timestamp'], data['lat'], data['lon'], data['status'],
                                   data['device'], data['version'])
        )
        # GEOADD geoindex
        await self.run_redis_action(
            'GEOADD',
            'geoindex',
            data['lon'],
            data['lat'],
            data['taxi']
        )
        # GEOADD geoindex_2
        await self.run_redis_action(
            'GEOADD',
            'geoindex_2',
            data['lon'],
            data['lat'],
            '%s:%s' % (data['taxi'], data['operator'])
        )
        # ZADD timestamps
        await self.run_redis_action(
            'ZADD',
            'timestamps',
            now,
            '%s:%s' % (data['taxi'], data['operator'])
        )
        # ZADD timestamps_id
        await self.run_redis_action(
            'ZADD',
            'timestamps_id',
            now,
            data['taxi']
        )
        # SADD ips:<operator>
        await self.run_redis_action(
            'SADD',
            'ips:%s' % data['operator'],
            from_ip
        )

    async def handle_message(self, message, from_addr):
        data = self.parse_message(message, from_addr)
        if not data:
            return

        valid = await self.check_hash(data, from_addr)
        if not valid:
            return

        await asyncio.gather(
            self.send_fluent(data),
            self.update_redis(data, from_addr)
        )


class GeoTaxiUDPServer:
    def __init__(self, asyncio_loop, geotaxi):
        self.geotaxi = geotaxi
        self.asyncio_loop = asyncio_loop
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        message = data.decode()
        logger.debug('Received from %s: %s', addr, message)

        task = self.geotaxi.handle_message(data, addr)
        self.asyncio_loop.create_task(task)


def run_server(host, port, geotaxi):
    loop = asyncio.get_event_loop()

    listen = loop.create_datagram_endpoint(
        lambda: GeoTaxiUDPServer(loop, geotaxi), local_addr=(host, port)
    )
    transport, _ = loop.run_until_complete(listen)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        transport.close()
        loop.close()


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

    parser.add_argument('--redis-host', type=str, default='127.0.0.1',
                        help='Redis host')
    parser.add_argument('--redis-port', type=str, default=6379,
                        help='Redis port')

    parser.add_argument('--disable-fluent', action='store_true', default=False,
                        help='If set, do not send logs to fluent')
    parser.add_argument('--fluent-host', type=str, default='127.0.0.1',
                        help='Fluentd host')
    parser.add_argument('--fluent-port', type=str, default=24224,
                        help='Fluentd port')

    parser.add_argument('--auth-enabled', action='store_true', default=False,
                        help='Enable authentication')
    parser.add_argument('--api-url', type=str, default='http://127.0.0.1:5000',
                        help='APITaxi URL, used when authentication is enabled to retrieve users')

    args = parser.parse_args()

    loglevel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=loglevel)

    if not args.auth_enabled:
        logger.warning('Authentication is not enabled')

    api_key = os.getenv('API_KEY')
    if args.auth_enabled and not api_key:
        parser.error('--enable-auth is set but API_KEY environment variable is not set')

    if args.disable_fluent:
        fluent = None
    else:
        fluent = FluentSender(host=args.fluent_host, port=args.fluent_port)

    geotaxi = GeoTaxi(
        args.redis_host, args.redis_port,
        fluent=fluent,
        auth_enabled=args.auth_enabled, api_url=args.api_url, api_key=api_key
    )

    run_server(args.host, args.port, geotaxi)
