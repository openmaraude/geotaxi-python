import argparse
import asyncio
import json
import logging
import socket
import time

from aiofluent import FluentSender
import aioredis
import jsonschema


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
    def __init__(self, redis_host, redis_port, fluent=None):
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.fluent = fluent
        self.redis = None

    def parse_message(self, message, from_addr):
        try:
            data = json.loads(message)
        except json.decoder.JSONDecodeError:
            logger.warning('Badly formatted JSON received from %s:%s: %s', *from_addr,  message)
            return

        try:
            jsonschema.validate(instance=data, schema=API_MESSAGE)
        except jsonschema.ValidationError as exc:
            logger.warning('Invalid request received from %s:%s: %s', *from_addr, exc.message)
            return
        return data

    async def send_fluent(self, data):
        """Send message to fluentd."""
        if not self.fluent:
            return
        await self.fluent.emit('geotaxi', data)

    async def update_redis(self, data, from_addr):
        if not self.redis:
            connstr = 'redis://%s:%s' % (self.redis_host, self.redis_port)

            try:
                self.redis = await aioredis.create_redis_pool(connstr)
            except socket.error as exc:
                logger.error('Unable to connect to redis: %s' % exc)
                return

        now = int(time.time())
        from_ip = from_addr[0]

        for redis_action in (
            {
                'action_name': 'HSET taxi:<id>',
                'action': self.redis.hset,
                'params': (
                    'taxi:%s' % data['taxi'],
                    data['operator'],
                    '%s %s %s %s %s %s' % (data['timestamp'], data['lat'], data['lon'],
                                          data['status'], data['device'], data['version'])
                )
            },
            {
                'action_name': 'GEOADD geoindex',
                'action': self.redis.geoadd,
                'params': ('geoindex', data['lon'], data['lat'], data['taxi'])
            },
            {
                'action_name': 'GEOADD geoindex_2',
                'action': self.redis.geoadd,
                'params': ('geoindex_2', data['lon'], data['lat'], '%s:%s' % (data['taxi'], data['operator']))
            },
            {
                'action_name': 'ZADD timestamps',
                'action': self.redis.zadd,
                'params': ('timestamps', now, '%s:%s' % (data['taxi'], data['operator']))
            },
            {
                'action_name': 'ZADD timestamps_id',
                'action': self.redis.zadd,
                'params': ('timestamps_id', now, data['taxi'])
            },
            {
                'action_name': 'SADD ips:<operator>',
                'action': self.redis.sadd,
                'params':('ips:%s' % data['operator'], from_ip)
            }
        ):
            try:
                await redis_action['action'](*redis_action['params'])
            except socket.error:
                logger.error('Error while running redis action %s', redis_action['action_name'])
                return

    async def handle_message(self, message, from_addr):
        data = self.parse_message(message, from_addr)
        if not data:
            return

        await asyncio.gather(
            self.send_fluent(data),
            self.update_redis(data, from_addr)
        )


class GeoTaxiServer:
    """GeoTaxi UDP server."""
    def __init__(self, asyncio_loop, geotaxi):
        self.geotaxi = geotaxi
        self.asyncio_loop = asyncio_loop

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
        lambda: GeoTaxiServer(loop, geotaxi), local_addr=(host, port)
    )
    transport, protocol = loop.run_until_complete(listen)

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
    args = parser.parse_args()

    loglevel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=loglevel)

    if args.disable_fluent:
        fluent = None
    else:
        fluent = FluentSender(host=args.fluent_host, port=args.fluent_port)

    geotaxi = GeoTaxi(args.redis_host, args.redis_port, fluent=fluent)

    run_server(args.host, args.port, geotaxi)
