import signal
import time
import os
import gc

import pytest

import vanilla


# TODO: remove
import logging
logging.basicConfig()


def test_lazy():
    class C(object):
        @vanilla.lazy
        def now(self):
            return time.time()

    c = C()
    want = c.now
    time.sleep(0.01)
    assert c.now == want


def test_Scheduler():
    s = vanilla.Scheduler()
    s.add(4, 'f2')
    s.add(9, 'f4')
    s.add(3, 'f1')
    item3 = s.add(7, 'f3')

    assert 0.003 - s.timeout() < 0.001
    assert len(s) == 4

    s.remove(item3)
    assert 0.003 - s.timeout() < 0.001
    assert len(s) == 3

    assert s.pop() == ('f1', ())
    assert 0.004 - s.timeout() < 0.001
    assert len(s) == 2

    assert s.pop() == ('f2', ())
    assert 0.009 - s.timeout() < 0.001
    assert len(s) == 1

    assert s.pop() == ('f4', ())
    assert not s


class TestHub(object):
    def test_spawn(self):
        h = vanilla.Hub()
        a = []

        h.spawn_later(10, lambda: a.append(1))
        h.spawn(lambda: a.append(2))

        h.sleep(1)
        assert a == [2]

        h.sleep(10)
        assert a == [2, 1]

    def test_exception(self):
        h = vanilla.Hub()

        def raiser():
            raise Exception()
        h.spawn(raiser)
        h.sleep(1)

        a = []
        h.spawn(lambda: a.append(2))
        h.sleep(1)
        assert a == [2]

    def test_stop(self):
        h = vanilla.Hub()

        @h.spawn
        def _():
            h.sleep(20)

        h.stop()

    def test_stop_on_term(self):
        h = vanilla.Hub()

        @h.spawn
        def _():
            h.sleep(20)
            os.kill(os.getpid(), signal.SIGINT)

        h.stop_on_term()


@pytest.mark.parametrize('primitive, a', [
    ('pipe', ()),
    ('dealer', ()),
    ('router', ()),
    ('channel', ()), ])
class TestAbandoned(object):
    @staticmethod
    def assert_abandoned_sender(sender, check):
        pytest.raises(vanilla.Abandoned, sender.send, 10)
        check.send('done')

    def test_abandoned_sender_after_wait(self, primitive, a):
        h = vanilla.Hub()
        sender, recver = getattr(h, primitive)(*a)
        check = h.pipe()
        h.spawn(self.assert_abandoned_sender, sender, check)
        h.sleep(1)
        del recver
        gc.collect()
        assert check.recv() == 'done'

    def test_abandoned_sender_before_wait(self, primitive, a):
        h = vanilla.Hub()
        sender, recver = getattr(h, primitive)(*a)
        check = h.pipe()
        h.spawn(self.assert_abandoned_sender, sender, check)
        del recver
        gc.collect()
        assert check.recv() == 'done'

    @staticmethod
    def assert_abandoned_recver(recver, check):
        pytest.raises(vanilla.Abandoned, recver.recv)
        check.send('done')

    def test_abandoned_recver_after_wait(self, primitive, a):
        h = vanilla.Hub()
        sender, recver = getattr(h, primitive)(*a)
        check = h.pipe()
        h.spawn(self.assert_abandoned_recver, recver, check)
        h.sleep(1)
        del sender
        gc.collect()
        assert check.recv() == 'done'

    def test_abandoned_recver_before_wait(self, primitive, a):
        h = vanilla.Hub()
        sender, recver = getattr(h, primitive)(*a)
        check = h.pipe()
        h.spawn(self.assert_abandoned_recver, recver, check)
        del sender
        gc.collect()
        assert check.recv() == 'done'


class TestPipe(object):
    def test_deadlock(self):
        h = vanilla.Hub()
        p = h.pipe()
        pytest.raises(vanilla.Stop, p.send, 1)

    def test_close_recver(self):
        h = vanilla.Hub()

        check = h.pipe()
        sender, recver = h.pipe()

        @h.spawn
        def _():
            for i in xrange(10):
                try:
                    sender.send(i)
                except vanilla.Closed:
                    break
            check.send('done')

        assert recver.recv() == 0
        assert recver.recv() == 1
        recver.close()
        assert check.recv() == 'done'

    def test_close_sender(self):
        h = vanilla.Hub()

        check = h.pipe()
        sender, recver = h.pipe()

        @h.spawn
        def _():
            for i in recver:
                check.send(i)
            check.send('done')

        sender.send(0)
        assert check.recv() == 0
        sender.send(1)
        assert check.recv() == 1
        sender.close()
        assert check.recv() == 'done'

    def test_timeout(self):
        h = vanilla.Hub()

        sender, recver = h.pipe()
        check = h.pipe()

        pytest.raises(vanilla.Timeout, sender.send, 12, timeout=0)
        pytest.raises(vanilla.Timeout, recver.recv, timeout=0)
        pytest.raises(vanilla.Timeout, sender.send, 12, timeout=0)

        @h.spawn
        def _():
            h.sleep(20)
            check.send(recver.recv())

        pytest.raises(vanilla.Timeout, sender.send, 12, timeout=10)
        sender.send(12, timeout=20)
        assert check.recv() == 12

        @h.spawn
        def _():
            h.sleep(20)
            sender.send(12)

        pytest.raises(vanilla.Timeout, recver.recv, timeout=10)
        assert recver.recv(timeout=20) == 12

    def test_select(self):
        h = vanilla.Hub()

        s1, r1 = h.pipe()
        s2, r2 = h.pipe()
        check = h.pipe()

        @h.spawn
        def _():
            check.send(r1.recv())

        @h.spawn
        def _():
            s2.send(10)
            check.send('done')

        ch, item = h.select([s1, r2])
        assert ch == s1
        s1.send(20)

        ch, item = h.select([s1, r2])
        assert ch == r2
        assert item == 10

        assert check.recv() == 20
        assert check.recv() == 'done'

    def test_select_timeout(self):
        h = vanilla.Hub()

        s1, r1 = h.pipe()
        s2, r2 = h.pipe()
        check = h.pipe()

        pytest.raises(vanilla.Timeout, h.select, [s1, r2], timeout=0)

        @h.spawn
        def _():
            h.sleep(20)
            check.send(r1.recv())

        pytest.raises(vanilla.Timeout, h.select, [s1, r2], timeout=10)

        ch, item = h.select([s1, r2], timeout=20)
        assert ch == s1
        s1.send(20)
        assert check.recv() == 20

        @h.spawn
        def _():
            h.sleep(20)
            s2.send(10)
            check.send('done')

        pytest.raises(vanilla.Timeout, h.select, [s1, r2], timeout=10)

        ch, item = h.select([s1, r2], timeout=20)
        assert ch == r2
        assert item == 10
        assert check.recv() == 'done'

    def test_pipe(self):
        h = vanilla.Hub()

        p1 = h.pipe()
        p2 = h.pipe()

        p1.pipe(p2)

        h.spawn(p1.send, 1)
        assert p2.recv() == 1

    def test_pipe_to_function(self):
        h = vanilla.Hub()

        p1 = h.pipe()

        @p1.pipe
        def p2(recver, sender):
            for item in recver:
                sender.send(item*2)

        h.spawn(p1.send, 1)
        assert p2.recv() == 2
        h.spawn(p1.send, 2)
        assert p2.recv() == 4

    def test_map(self):
        h = vanilla.Hub()
        p1 = h.pipe()
        p2 = p1.map(lambda x: x * 2)

        h.spawn(p1.send, 1)
        assert p2.recv() == 2

        h.spawn(p1.send, 2)
        assert p2.recv() == 4

    def test_consume(self):
        h = vanilla.Hub()
        p = h.pipe()
        check = h.pipe()
        p.consume(lambda x: check.send(x))
        p.send(1)
        assert check.recv() == 1

    def test_exception(self):
        h = vanilla.Hub()

        p = h.pipe()
        check = h.pipe()

        @h.spawn
        def _():
            try:
                p.recv()
            except Exception, e:
                check.send(e.message)

        p.send(Exception('hai'))
        assert check.recv() == 'hai'

    # TODO: move to their own test suite
    def test_producer(self):
        h = vanilla.Hub()

        @h.producer
        def counter(sender):
            for i in xrange(10):
                sender.send(i)

        assert counter.recv() == 0
        h.sleep(10)
        assert counter.recv() == 1

    def test_trigger(self):
        h = vanilla.Hub()

        check = h.pipe()

        @h.trigger
        def go():
            check.send(1)

        h.sleep(1)
        gc.collect()

        go.trigger()
        assert check.recv() == 1

        pipe = go.middle

        h.sleep(1)
        del go
        gc.collect()
        gc.collect()

        assert pipe.recver() is None
        assert pipe.sender() is None


class TestQueue(object):
    def test_queue(self):
        h = vanilla.Hub()
        b = h.queue(2)

        b.send(1, timeout=0)
        b.send(2, timeout=0)
        pytest.raises(vanilla.Timeout, b.send, 3, timeout=0)
        assert b.recv() == 1

        b.send(3, timeout=0)
        assert b.recv() == 2
        assert b.recv() == 3

        gc.collect()
        h.sleep(1)

    def test_queue_close_sender(self):
        h = vanilla.Hub()
        sender, recver = h.queue(2)

        sender.send(1, timeout=0)
        sender.send(2, timeout=0)

        del sender
        gc.collect()

        assert recver.recv() == 1
        assert recver.recv() == 2

        gc.collect()
        h.sleep(1)


class TestPulse(object):
    def test_pulse(self):
        h = vanilla.Hub()

        trigger = h.pulse(20)
        pytest.raises(vanilla.Timeout, trigger.recv, timeout=0)

        h.sleep(20)
        assert trigger.recv(timeout=0)
        pytest.raises(vanilla.Timeout, trigger.recv, timeout=0)

        h.sleep(20)
        assert trigger.recv(timeout=0)
        pytest.raises(vanilla.Timeout, trigger.recv, timeout=0)

        h.stop()


class TestDealer(object):
    def test_send_then_recv(self):
        h = vanilla.Hub()
        d = h.dealer()
        h.spawn(d.send, 1)
        assert d.recv() == 1

    def test_recv_then_send(self):
        h = vanilla.Hub()

        d = h.dealer()
        q = h.queue(10)

        h.spawn(lambda: q.send(d.recv()))
        h.spawn(lambda: q.send(d.recv()))
        h.spawn(lambda: q.send(d.recv()))
        h.sleep(1)

        d.send(1)
        assert q.recv() == 1
        d.send(2)
        d.send(3)
        assert q.recv() == 2
        assert q.recv() == 3

    def test_send_timeout(self):
        h = vanilla.Hub()
        d = h.dealer()
        pytest.raises(vanilla.Timeout, d.send, 1, timeout=10)

    def test_recv_timeout(self):
        h = vanilla.Hub()
        d = h.dealer()
        pytest.raises(vanilla.Timeout, d.recv, timeout=10)
        # assert that waiters is cleaned up after timeout
        assert not d.recver.current

    def test_send_select(self):
        h = vanilla.Hub()
        d = h.dealer()

        @h.spawn
        def _():
            ch, _ = h.select([d.sender])
            assert ch == d.sender
            d.send(1)

        h.sleep(1)
        assert d.recv() == 1

    def test_recv_select(self):
        h = vanilla.Hub()

        d = h.dealer()
        q = h.queue(10)

        def selector():
            ch, item = h.select([d.recver])
            assert ch == d.recver
            q.send(item)

        h.spawn(selector)
        h.spawn(selector)
        h.spawn(selector)
        h.sleep(1)

        d.send(1)
        assert q.recv() == 1
        d.send(2)
        d.send(3)
        assert q.recv() == 2
        assert q.recv() == 3


class TestRouter(object):
    def test_send_then_recv(self):
        h = vanilla.Hub()
        r = h.router()

        h.spawn(r.send, 3)
        h.spawn(r.send, 2)
        h.spawn(r.send, 1)
        h.sleep(1)

        assert r.recv() == 3
        assert r.recv() == 2
        assert r.recv() == 1

    def test_recv_then_send(self):
        h = vanilla.Hub()

        r = h.router()
        q = h.queue(10)

        @h.spawn
        def _():
            q.send(r.recv())

        h.sleep(1)
        r.send(1)
        assert q.recv() == 1

    def test_pipe(self):
        h = vanilla.Hub()

        r = h.router()

        p1 = h.pipe()
        p2 = h.pipe()

        p1.pipe(r)
        p2.pipe(r)

        h.spawn(p1.send, 1)
        h.spawn(p2.send, 2)

        assert r.recv() == 1
        assert r.recv() == 2


class TestChannel(object):
    def test_send_then_recv(self):
        h = vanilla.Hub()
        ch = h.channel()

        h.spawn(ch.send, 3)
        h.spawn(ch.send, 2)
        h.spawn(ch.send, 1)
        h.sleep(1)

        assert ch.recv() == 3
        assert ch.recv() == 2
        assert ch.recv() == 1

    def test_recv_then_send(self):
        h = vanilla.Hub()

        d = h.dealer()
        q = h.queue(10)

        h.spawn(lambda: q.send(d.recv()))
        h.spawn(lambda: q.send(d.recv()))
        h.spawn(lambda: q.send(d.recv()))
        h.sleep(1)

        d.send(1)
        assert q.recv() == 1
        d.send(2)
        d.send(3)
        assert q.recv() == 2
        assert q.recv() == 3

    def test_no_queue(self):
        h = vanilla.Hub()
        ch = h.channel()
        pytest.raises(vanilla.Timeout, ch.send, 1, timeout=0)

    def test_queue(self):
        h = vanilla.Hub()
        ch = h.channel(2)
        ch.send(1, timeout=0)
        ch.send(2, timeout=0)
        pytest.raises(vanilla.Timeout, ch.send, 3, timeout=0)
        assert ch.recv() == 1
        ch.send(3, timeout=0)
        assert ch.recv() == 2
        assert ch.recv() == 3


class TestBroadcast(object):
    def test_broadcast(self):
        h = vanilla.Hub()

        b = h.broadcast()
        check = h.queue(10)

        def subscriber(s, name):
            for item in s:
                check.send((name, item))

        s1 = b.subscribe()
        s2 = b.subscribe()

        h.spawn(subscriber, s1, 's1')
        h.spawn(subscriber, s2, 's2')
        h.sleep(1)

        b.send(1)
        assert check.recv() == ('s1', 1)
        assert check.recv() == ('s2', 1)

        b.send(2)
        assert check.recv() == ('s1', 2)
        assert check.recv() == ('s2', 2)

        s1.close()
        b.send(3)
        assert check.recv() == ('s2', 3)
        pytest.raises(vanilla.Timeout, check.recv, timeout=0)

    def test_broadcast_pipe(self):
        h = vanilla.Hub()

        b = h.broadcast()

        source = h.pulse(20)
        source.pipe(b)

        check = h.queue(10)

        def subscriber(s, name):
            for item in s:
                check.send((name, item))

        s1 = b.subscribe()
        s2 = b.subscribe()

        h.spawn(subscriber, s1, 's1')
        h.spawn(subscriber, s2, 's2')
        h.sleep(1)

        assert check.recv() == ('s1', True)
        assert check.recv() == ('s2', True)


class TestValue(object):
    def test_value(self):
        h = vanilla.Hub()

        v = h.value()
        check = h.pipe()

        @h.spawn
        def _():
            while True:
                check.send(v.recv())

        pytest.raises(vanilla.Timeout, check.recv, timeout=0)
        v.send(1)
        assert check.recv() == 1
        assert check.recv() == 1

        v.clear()
        pytest.raises(vanilla.Timeout, check.recv, timeout=0)

    def test_value_timeout(self):
        h = vanilla.Hub()
        v = h.value()
        pytest.raises(vanilla.Timeout, v.recv, timeout=0)


class TestDescriptor(object):
    def test_human_mask(self):
        C = vanilla.C
        mask = C.EPOLLIN | C.EPOLLOUT | C.EPOLLHUP | C.EPOLLERR | \
            C.EPOLLET | C.EPOLLRDHUP
        assert set(vanilla.Descriptor.humanize_mask(mask)) == set(
            ['in', 'out', 'hup', 'err', 'et', 'rdhup'])

    def test_recv_bytes(self):
        h = vanilla.Hub()
        r, w = os.pipe()

        r = h.poll.fileno(r)
        w = h.poll.fileno(w)

        w.send('123')
        assert r.recv_bytes(2) == '12'

        h.spawn_later(10, w.send, '2')
        assert r.recv_bytes(2) == '32'
        # TODO: h.stop()

    def test_recv_partition(self):
        h = vanilla.Hub()
        r, w = os.pipe()

        r = h.poll.fileno(r)
        w = h.poll.fileno(w)

        w.send('12\r\n3')
        assert r.recv_partition('\r\n') == '12'

        h.spawn_later(10, w.send, '2\r\n')
        assert r.recv_partition('\r\n') == '32'

    def test_close_read(self):
        h = vanilla.Hub()
        r, w = os.pipe()

        r = h.poll.fileno(r)
        w = h.poll.fileno(w)

        w.send('123')
        assert r.recv_bytes(2) == '12'

        os.close(r.conn.fileno())
        w.send('2')
        pytest.raises(vanilla.Closed, w.send, '3')

        assert r.recv_bytes(1) == '3'
        pytest.raises(vanilla.Closed, r.recv)

    def test_close_write(self):
        h = vanilla.Hub()
        r, w = os.pipe()

        r = h.poll.fileno(r)
        w = h.poll.fileno(w)

        w.send('123')
        assert r.recv_bytes(2) == '12'

        os.close(w.conn.fileno())
        w.send('2')
        pytest.raises(vanilla.Closed, w.send, '3')

        assert r.recv_bytes(1) == '3'
        pytest.raises(vanilla.Closed, r.recv)

    def test_stop(self):
        h = vanilla.Hub()
        r, w = os.pipe()

        r = h.poll.fileno(r)
        w = h.poll.fileno(w)

        h.stop()


class TestSignal(object):
    # TODO: test abandoned
    def test_signal(self):
        h = vanilla.Hub()

        signal.setitimer(signal.ITIMER_REAL, 50.0/1000)

        s1 = h.signal.subscribe(signal.SIGALRM)
        s2 = h.signal.subscribe(signal.SIGALRM)

        assert s1.recv() == signal.SIGALRM
        assert s2.recv() == signal.SIGALRM

        signal.setitimer(signal.ITIMER_REAL, 10.0/1000)
        s1.close()

        pytest.raises(vanilla.Halt, s1.recv)
        assert s2.recv() == signal.SIGALRM

        # TODO:
        return
        # assert that removing the last listener for a signal cleans up the
        # registered file descriptor
        s2.close()
        assert not h.registered


class TestTCP(object):
    def test_tcp(self):
        h = vanilla.Hub()

        @h.tcp.listen()
        def server(conn):
            conn.send(' '.join([conn.recv()]*2))

        client = h.tcp.connect(server.port)
        client.send('Toby')
        assert client.recv() == 'Toby Toby'

        h.stop()


class TestHTTP(object):
    def test_get_body(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            return request.path

        uri = 'http://localhost:%s' % serve.port
        conn = h.http.connect(uri)

        response = conn.get('/').recv()
        assert response.status.code == 200
        assert response.consume() == '/'

        response = conn.get('/toby').recv()
        assert response.status.code == 200
        assert response.consume() == '/toby'

        # TODO: stop!!, everything needs stop testing
        # h.stop()

    def test_get_chunked(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            for i in xrange(3):
                h.sleep(10)
                response.send(str(i))
            if len(request.path) > 1:
                return request.path[1:]

        uri = 'http://localhost:%s' % serve.port
        conn = h.http.connect(uri)

        response = conn.get('/').recv()
        assert response.status.code == 200
        assert list(response.body) == ['0', '1', '2']

        response = conn.get('/peace').recv()
        assert response.status.code == 200
        assert list(response.body) == ['0', '1', '2', 'peace']

        # TODO: stop!!, everything needs stop testing
        # h.stop()

    def test_post(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            return request.consume()

        uri = 'http://localhost:%s' % serve.port
        conn = h.http.connect(uri)

        response = conn.post('/').recv()
        assert response.status.code == 200
        assert response.consume() == ''

        response = conn.post('/', data='toby').recv()
        assert response.status.code == 200
        assert response.consume() == 'toby'

    def test_put(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            return request.method + request.consume()

        uri = 'http://localhost:%s' % serve.port
        conn = h.http.connect(uri)

        response = conn.put('/').recv()
        assert response.status.code == 200
        assert response.consume() == 'PUT'

        response = conn.put('/', data='toby').recv()
        assert response.status.code == 200
        assert response.consume() == 'PUTtoby'

    def test_delete(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            return request.method

        uri = 'http://localhost:%s' % serve.port
        conn = h.http.connect(uri)

        response = conn.delete('/').recv()
        assert response.status.code == 200
        assert response.consume() == 'DELETE'

    def test_404(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            raise response.HTTP404

        uri = 'http://localhost:%s' % serve.port
        response = h.http.connect(uri).get('/').recv()
        assert response.status.code == 404


class TestWebsocket(object):
    def test_websocket(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            ws = response.upgrade()
            while True:
                item = ws.recv()
                ws.send(item)

        uri = 'ws://localhost:%s' % serve.port
        ws = h.http.connect(uri).websocket('/')

        gc.collect()

        message = 'x' * 125
        ws.send(message)
        assert ws.recv() == message

        message = 'x' * 126
        ws.send(message)
        assert ws.recv() == message

        message = 'x' * 65535
        ws.send(message)
        assert ws.recv() == message

        message = 'x' * 65536
        ws.send(message)
        assert ws.recv() == message

        # test we can call select on the websocket
        message = 'x' * 125
        ws.send(message)
        assert h.select([ws.recver]) == (ws.recver, message)

    def test_websocket_end(self):
        h = vanilla.Hub()

        @h.http.listen()
        def serve(request, response):
            ws = response.upgrade()
            ws.recv()
            return

        uri = 'ws://localhost:%s' % serve.port
        ws = h.http.connect(uri).websocket('/')
        ws.send('1')
        pytest.raises(vanilla.Closed, ws.recv)


class TestBean(object):
    def conn(self, app):
        return app.hub.http.connect('http://localhost:%s' % app.port)

    def test_basic(self):
        h = vanilla.Hub()
        app = h.http.bean()

        @app.route('/')
        def index(request, response):
            return 'index'

        response = self.conn(app).get('/').recv()
        assert response.status.code == 200
        assert response.consume() == 'index'

    def test_method(self):
        h = vanilla.Hub()
        app = h.http.bean()

        @app.get('/')
        def get(request, response):
            return request.method

        @app.post('/')
        def get(request, response):
            return request.consume()

        @app.websocket('/')
        def websocket(ws):
            while True:
                ws.send(ws.recv())

        conn = self.conn(app)
        response = conn.get('/').recv()
        assert response.status.code == 200
        assert response.consume() == 'GET'

        conn = self.conn(app)
        response = conn.post('/', data='toby').recv()
        assert response.status.code == 200
        assert response.consume() == 'toby'

        conn = self.conn(app)
        ws = conn.websocket('/')
        ws.send('toby')
        assert ws.recv() == 'toby'

    def test_static(self, tmpdir):
        fh = tmpdir.join('bar.html').open('w')
        fh.write('foo')
        fh.close()

        tmpdir.mkdir('static')
        fh = tmpdir.join('static', 'foo.html').open('w')
        fh.write('bar')
        fh.close()

        h = vanilla.Hub()
        app = h.http.bean(base_path=tmpdir.strpath)
        app.static('/', 'bar.html')
        app.static('/static', 'static')

        response = self.conn(app).get('/').recv()
        assert response.status.code == 200
        assert response.headers['Content-Type'] == 'text/html'
        assert response.consume() == 'foo'

        response = self.conn(app).get('/static/foo.html').recv()
        assert response.status.code == 200
        assert response.headers['Content-Type'] == 'text/html'
        assert response.consume() == 'bar'

        # test 404
        response = self.conn(app).get('/static/bar.html').recv()
        assert response.status.code == 404
