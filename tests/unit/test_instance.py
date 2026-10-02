from procwatch.instance import Instance


def test_second_instance_refuses_even_without_published_url(tmp_path):
    with Instance(tmp_path) as first, Instance(tmp_path) as second:
        assert first.acquire()
        assert not second.acquire()


def test_live_owner_metadata_is_available_and_private(tmp_path):
    with Instance(tmp_path) as first, Instance(tmp_path) as second:
        assert first.acquire()
        first.publish({'url': 'http://127.0.0.1:1234/', 'read_only': False})
        assert not second.acquire()
        assert second.read()['url'] == 'http://127.0.0.1:1234/'
        assert (tmp_path / 'instance.lock').stat().st_mode & 0o777 == 0o600


def test_stale_metadata_does_not_prevent_restart(tmp_path):
    with Instance(tmp_path) as first:
        assert first.acquire()
        first.publish({'pid': 999, 'url': 'stale'})
    with Instance(tmp_path) as replacement:
        assert replacement.acquire()
        assert replacement.read() is None


def test_exception_releases_lock(tmp_path):
    try:
        with Instance(tmp_path) as first:
            assert first.acquire()
            raise ValueError('test')
    except ValueError:
        pass
    with Instance(tmp_path) as replacement:
        assert replacement.acquire()
