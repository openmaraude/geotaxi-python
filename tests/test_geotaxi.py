import time
from unittest import mock

import fakeredis
import pytest
import requests
import requests_mock

from geotaxi.geotaxi import GeoTaxi


class MockFluent:
    """Fake FluentSender."""
    def __init__(self):
        self._records = []

    def emit(self, tag, data):
        self._records.append((tag, data))


class TestGeoTaxi:

    def test_geotaxi_error_5xx(self):
        with requests_mock.mock() as mock:
            mock.get('http://api.tests/users', status_code=500)

            with pytest.raises(requests.exceptions.HTTPError):
                geotaxi = GeoTaxi(
                    None,
                    auth_enabled=True,
                    api_url='http://api.tests',
                    api_key='f4k3'
                )

    def test_get_api_users_ok(self):
        with requests_mock.mock() as mock:
            mock.get('http://api.tests/users', json={
                'data': [
                    {'name': 'user1', 'apikey': 'key1'},
                    {'name': 'user2', 'apikey': 'key2'},
                ]
            })

            geotaxi = GeoTaxi(
                None,
                auth_enabled=True,
                api_url='http://api.tests',
                api_key='f4k3'
            )
            users = geotaxi.get_api_users()
            assert len(users) == 2
            assert 'user1' in users and users['user1'] == 'key1'
            assert 'user2' in users and users['user2'] == 'key2'

    def test_check_hash(self):
        with requests_mock.mock() as mock:
            mock.get('http://api.tests/users', json={
                'data': [
                    {'name': 'user1', 'apikey': 'key1'},
                ]
            })
            redis = fakeredis.FakeStrictRedis()
            geotaxi = GeoTaxi(
                redis,
                auth_enabled=True,
                api_url='http://api.tests',
                api_key='f4k3'
            )
            is_valid = geotaxi.check_hash({
                'timestamp': '1',
                'operator': 'user1',
                'taxi': 'taxi',
                'lat': '17',
                'lon': '18',
                'device': 'mobile',
                'status': 'free',
                'version': '1',
                'hash': '63f3d6cf5f25e96bd085aca81d715a695c9c36e2'
            }, ('127.0.2.3', 9999))
            assert is_valid is True

            is_valid = geotaxi.check_hash({
                'timestamp': '1',
                'operator': 'user1',
                'taxi': 'taxi',
                'lat': '17',
                'lon': '18',
                'device': 'mobile',
                'status': 'free',
                'version': '1',
                'hash': 'b4dhash'
            }, ('127.0.2.3', 9999))
            assert is_valid is False

            assert b'badhash_operators' in redis.keys()
            assert redis.zrange(b'badhash_operators', 0, -1, withscores=True) == [(b'user1', 1.0)]

            assert b'badhash_taxis_ids' in redis.keys()
            assert redis.zrange(b'badhash_taxis_ids', 0, -1, withscores=True) == [(b'taxi', 1.0)]

            assert b'badhash_ips' in redis.keys()
            assert redis.zrange(b'badhash_ips', 0, -1, withscores=True) == [(b'127.0.2.3', 1.0)]

    def test_parse_message(self):
        geotaxi = GeoTaxi(None)
        fromaddr = ('127.0.2.3', 8909)

        # Bad json
        assert geotaxi.parse_message(b'{badjson', fromaddr) is None

        # Invalid UTF8
        assert geotaxi.parse_message(b'\xff', fromaddr) is None

        # Empty dict
        assert geotaxi.parse_message(b'{}', fromaddr) is None

        # Missing field "hash"
        assert geotaxi.parse_message(b'''{
            "timestamp": "1",
            "operator": "user1",
            "taxi": "taxi",
            "lat": "17",
            "lon": "18",
            "device": "mobile",
            "status": "free",
            "version": "1",
        }''', fromaddr) is None

        # Valid
        assert isinstance(geotaxi.parse_message(b'''{
            "timestamp": "1",
            "operator": "user1",
            "taxi": "taxi",
            "lat": "17",
            "lon": "18",
            "device": "mobile",
            "status": "free",
            "version": "1",
            "hash": "b4dhash"
        }''', fromaddr), dict)

    def test_send_fluent(self):
        fluent = MockFluent()
        geotaxi = GeoTaxi(None, fluent=fluent)
        geotaxi.send_fluent({'key': 'value'})
        assert fluent._records == [('position', {'key': 'value'})]

    def test_update_redis(self):
        redis = fakeredis.FakeRedis()
        geotaxi = GeoTaxi(redis)

        payload = {
            'timestamp': '1',
            'operator': 'user1',
            'taxi': 'taxi',
            'lat': '17',
            'lon': '18',
            'device': 'mobile',
            'status': 'free',
            'version': '1',
            'hash': 'b4dhash'
        }
        fromaddr = ('127.0.3.4', 9132)

        # fakeredis doesn't implement geoadd. Fake the method.
        redis.geoadd = mock.MagicMock()

        # Try to update redis.
        geotaxi.update_redis(payload, fromaddr)

        # GEOADD should have been called twice
        assert redis.geoadd.call_count == 2
        redis.geoadd.assert_any_call('geoindex', '18', '17', 'taxi')
        redis.geoadd.assert_any_call('geoindex_2', '18', '17', 'taxi:user1')

        # There should be six keys stored (the two GEOADD above are not listed)
        assert len(redis.keys()) == 4

        assert b'taxi:%s' % payload['taxi'].encode('utf8') in redis.keys()
        assert b'user1' in redis.hgetall('taxi:%s' % payload['taxi'])

        assert b'timestamps' in redis.keys()
        assert redis.zrange(b'timestamps', 0, -1) == [b'taxi:user1']

        assert b'timestamps_id' in redis.keys()
        assert redis.zrange(b'timestamps_id', 0, -1) == [b'taxi']

        assert b'ips:%s' % payload['operator'].encode('utf8') in redis.keys()
        assert fromaddr[0].encode('utf8') in redis.smembers(b'ips:%s' % payload['operator'].encode('utf8'))
