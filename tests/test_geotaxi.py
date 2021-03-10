import fakeredis
import pytest
import requests

from geotaxi.worker import Worker


class MockFluent:
    """Fake FluentSender."""

    def __init__(self):
        self._records = []

    def emit(self, tag, data):
        self._records.append((tag, data))


class TestWorker:

    def test_geotaxi_error_5xx(self, requests_mock):
        requests_mock.get('http://api.tests/users', status_code=500)

        with pytest.raises(requests.exceptions.HTTPError):
            Worker(
                None,
                auth_enabled=True,
                api_url='http://api.tests',
                api_key='f4k3'
            )

    def test_get_api_users_ok(self, requests_mock):
        requests_mock.get('http://api.tests/users', json={
            'data': [
                {'name': 'user1', 'apikey': 'key1'},
                {'name': 'user2', 'apikey': 'key2'},
            ]
        })

        worker = Worker(
            None,
            auth_enabled=True,
            api_url='http://api.tests',
            api_key='f4k3'
        )
        users = worker.get_api_users()
        assert len(users) == 2
        assert 'user1' in users and users['user1'] == 'key1'
        assert 'user2' in users and users['user2'] == 'key2'

    def test_check_hash(self, requests_mock):
        requests_mock.get('http://api.tests/users', json={
            'data': [
                {'name': 'user1', 'apikey': 'key1'},
            ]
        })
        redis = fakeredis.FakeStrictRedis()
        worker = Worker(
            redis,
            auth_enabled=True,
            api_url='http://api.tests',
            api_key='f4k3'
        )
        is_valid = worker.check_hash({
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

        is_valid = worker.check_hash({
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
        worker = Worker(None)
        fromaddr = ('127.0.2.3', 8909)

        # Bad json
        assert worker.parse_message(b'{badjson', fromaddr) is None

        # Invalid UTF8
        assert worker.parse_message(b'\xff', fromaddr) is None

        # Empty dict
        assert worker.parse_message(b'{}', fromaddr) is None

        # Missing field "hash"
        assert worker.parse_message(b'''{
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
        assert isinstance(worker.parse_message(b'''{
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
        worker = Worker(None, fluent=fluent)
        worker.send_fluent({'key': 'value'})
        assert fluent._records == [('position', {'key': 'value'})]

    def test_send_backend(self, requests_mock):
        def callback(request, context):
            assert request.json() == {
                'data': [
                    {
                        'positions': [
                            {
                                'taxi_id': 'taxi',
                                'lon': 18.0,  # Casted to float
                                'lat': 17.0,
                            }
                        ]
                    }
                ]
            }

            context.status_code = 200
            return ''

        requests_mock.get('http://api.tests/users', json={
            'data': [
                {'name': 'user1', 'apikey': 'key1'},
            ]
        })
        requests_mock.post('http://api.tests/geotaxi', text=callback)
        worker = Worker(
            None,
            auth_enabled=True,
            api_url='http://api.tests',
            api_key='key1',
        )

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

        # Try to send
        worker.send_backend(payload)
