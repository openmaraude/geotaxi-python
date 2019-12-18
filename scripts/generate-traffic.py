#!/usr/bin/env python3

import argparse
import hashlib
import json
import socket
import time
import uuid


def run(host, port, num, sleep, api_key, operator):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    taxi_id = str(uuid.uuid4())

    if not api_key:
        api_key = str(uuid.uuid4())

    if not operator:
        operator = 'fake_operator'

    for idx in range(num):
        unix_timestamp = int(time.time())

        data = {
            'timestamp': str(unix_timestamp),
            'operator': operator,
            'version': '1',
            'lat': '48.856613',
            'lon': '2.352222',
            'device': 'mobile',
            'taxi': taxi_id,
            'status': 'free',
        }

        h = hashlib.sha1(''.join([
            data['timestamp'],
            data['operator'],
            data['taxi'],
            data['lat'],
            data['lon'],
            data['device'],
            data['status'],
            data['version'],
            api_key
        ]).encode('utf8')).hexdigest()

        data['hash'] = h

        sock.sendto(
            json.dumps(data).encode('utf8'),
            (host, port)
        )

        if sleep > 0:
            time.sleep(sleep)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--host', type=str, default='127.0.0.1',
        help='geotaxi host'
    )
    parser.add_argument(
        '--port', type=int, default=8080,
        help='geotaxi port'
    )
    parser.add_argument(
        'num', type=int, default=1, nargs='?',
        help='Number of messages to send'
    )
    parser.add_argument(
        '-s', '--sleep', type=float, default=0.001,
        help='Time to sleep between two messages'
    )
    parser.add_argument(
        '--api-key', type=str,
        help='API key, to set if server has authentication enabled'
    )
    parser.add_argument(
        '--operator', type=str,
        help='Operator name. Must be the owner of --api-key if authentication is enabled.'
    )

    args = parser.parse_args()
    run(args.host, args.port, args.num, args.sleep, args.api_key, args.operator)


if __name__ == '__main__':
    main()
