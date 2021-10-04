# -*- coding: utf-8 -*-
from __future__ import absolute_import
import copy
import json
import base64

import scrapy
from scrapy.core.engine import ExecutionEngine
from scrapy.utils.test import get_crawler
from scrapy.http import Response, TextResponse
from scrapy.downloadermiddlewares.httpcache import HttpCacheMiddleware

import scrapy_splash
from scrapy_splash.utils import to_native_str
from scrapy_splash import (
    SplashRequest,
    SplashMiddleware,
    SlotPolicy,
    SplashCookiesMiddleware,
    SplashDeduplicateArgsMiddleware,
)


def _get_crawler(settings_dict):
    settings_dict = settings_dict.copy()
    settings_dict['DOWNLOAD_HANDLERS'] = {'s3': None}  # for faster test running
    crawler = get_crawler(settings_dict=settings_dict)
    if not hasattr(crawler, 'logformatter'):
        crawler.logformatter = None
    crawler.engine = ExecutionEngine(crawler, lambda _: None)
    # spider = crawler._create_spider("foo")
    return crawler


def _get_mw():
    crawler = _get_crawler({})
    return SplashMiddleware.from_crawler(crawler)


def _get_cookie_mw():
    return SplashCookiesMiddleware(debug=True)


def test_nosplash():
    mw = _get_mw()
    cookie_mw = _get_cookie_mw()
    req = scrapy.Request("http://example.com")
    old_meta = copy.deepcopy(req.meta)

    assert cookie_mw.process_request(req, None) is None
    assert mw.process_request(req, None) is None
    assert old_meta == req.meta

    # response is not changed
    response = Response("http://example.com", request=req)
    response2 = mw.process_response(req, response, None)
    response3 = cookie_mw.process_response(req, response, None)
    assert response2 is response
    assert response3 is response
    assert response3.url == "http://example.com"




def test_dont_process_response():
    mw = _get_mw()
    req = SplashRequest("http://example.com/",
        endpoint="render.html",
        dont_process_response=True,
    )
    req2 = mw.process_request(req, None)
    resp = Response("http://example.com/")
    resp2 = mw.process_response(req2, resp, None)
    assert resp2.__class__ is Response
    assert resp2 is resp




def test_magic_response():
    mw = _get_mw()
    cookie_mw = _get_cookie_mw()

    req = SplashRequest('http://example.com/',
                        endpoint='execute',
                        args={'lua_source': 'function main() end'},
                        magic_response=True,
                        cookies=[{'name': 'foo', 'value': 'bar'}])
    req = cookie_mw.process_request(req, None) or req
    req = mw.process_request(req, None) or req

    resp_data = {
        'url': "http://exmaple.com/#id42",
        'html': '<html><body>Hello 404</body></html>',
        'http_status': 404,
        'headers': [
            {'name': 'Content-Type', 'value': "text/html"},
            {'name': 'X-My-Header', 'value': "foo"},
            {'name': 'Set-Cookie', 'value': "bar=baz"},
        ],
        'cookies': [
            {'name': 'foo', 'value': 'bar'},
            {'name': 'bar', 'value': 'baz', 'domain': '.example.com'},
            {'name': 'session', 'value': '12345', 'path': '/',
             'expires': '2055-07-24T19:20:30Z'},
        ],
    }
    resp = TextResponse("http://mysplash.example.com/execute",
                        headers={b'Content-Type': b'application/json'},
                        body=json.dumps(resp_data).encode('utf8'))
    resp2 = mw.process_response(req, resp, None)
    resp2 = cookie_mw.process_response(req, resp2, None)
    assert isinstance(resp2, scrapy_splash.SplashJsonResponse)
    assert resp2.data == resp_data
    assert resp2.body == b'<html><body>Hello 404</body></html>'
    assert resp2.text == '<html><body>Hello 404</body></html>'
    assert resp2.headers == {
        b'Content-Type': [b'text/html'],
        b'X-My-Header': [b'foo'],
        b'Set-Cookie': [b'bar=baz'],
    }
    assert resp2.splash_response_headers == {b'Content-Type': [b'application/json']}
    assert resp2.status == 404
    assert resp2.splash_response_status == 200
    assert resp2.url == "http://exmaple.com/#id42"
    assert len(resp2.cookiejar) == 3
    cookies = [c for c in resp2.cookiejar]
    assert {(c.name, c.value) for c in cookies} == {
        ('bar', 'baz'),
        ('foo', 'bar'),
        ('session', '12345')
    }

    # send second request using the same session and check the resulting cookies
    req = SplashRequest('http://example.com/foo',
                        endpoint='execute',
                        args={'lua_source': 'function main() end'},
                        magic_response=True,
                        cookies={'spam': 'ham'})
    req = cookie_mw.process_request(req, None) or req
    req = mw.process_request(req, None) or req

    resp_data = {
        'html': '<html><body>Hello</body></html>',
        'headers': [
            {'name': 'Content-Type', 'value': "text/html"},
            {'name': 'X-My-Header', 'value': "foo"},
            {'name': 'Set-Cookie', 'value': "bar=baz"},
        ],
        'cookies': [
            {'name': 'spam', 'value': 'ham'},
            {'name': 'egg', 'value': 'spam'},
            {'name': 'bar', 'value': 'baz', 'domain': '.example.com'},
           #{'name': 'foo', 'value': ''},  -- this won't be in response
            {'name': 'session', 'value': '12345', 'path': '/',
             'expires': '2056-07-24T19:20:30Z'},
        ],
    }
    resp = TextResponse("http://mysplash.example.com/execute",
                        headers={b'Content-Type': b'application/json'},
                        body=json.dumps(resp_data).encode('utf8'))
    resp2 = mw.process_response(req, resp, None)
    resp2 = cookie_mw.process_response(req, resp2, None)
    assert isinstance(resp2, scrapy_splash.SplashJsonResponse)
    assert resp2.data == resp_data
    cookies = [c for c in resp2.cookiejar]
    assert {c.name for c in cookies} == {'session', 'egg', 'bar', 'spam'}
    for c in cookies:
        if c.name == 'session':
            assert c.expires == 2731692030
        if c.name == 'spam':
            assert c.value == 'ham'


def test_cookies():
    mw = _get_mw()
    cookie_mw = _get_cookie_mw()

    def request_with_cookies(cookies):
        req = SplashRequest(
            'http://example.com/foo',
            endpoint='execute',
            args={'lua_source': 'function main() end'},
            magic_response=True,
            cookies=cookies)
        req = cookie_mw.process_request(req, None) or req
        req = mw.process_request(req, None) or req
        return req

    def response_with_cookies(req, cookies):
        resp_data = {
            'html': '<html><body>Hello</body></html>',
            'headers': [],
            'cookies': cookies,
        }
        resp = TextResponse(
            'http://mysplash.example.com/execute',
            headers={b'Content-Type': b'application/json'},
            body=json.dumps(resp_data).encode('utf8'))
        resp = mw.process_response(req, resp, None)
        resp = cookie_mw.process_response(req, resp, None)
        return resp

    # Concurent requests
    req1 = request_with_cookies({'spam': 'ham'})
    req2 = request_with_cookies({'bom': 'bam'})
    resp1 = response_with_cookies(req1, [
        {'name': 'spam', 'value': 'ham'},
        {'name': 'spam_x', 'value': 'ham_x'},
    ])
    resp2 = response_with_cookies(req2, [
        {'name': 'spam', 'value': 'ham'},  # because req2 was made after req1
        {'name': 'bom_x', 'value': 'bam_x'},
    ])
    assert resp1.cookiejar is resp2.cookiejar
    cookies = {c.name: c.value for c in resp1.cookiejar}
    assert cookies == {'spam': 'ham', 'spam_x': 'ham_x', 'bom_x': 'bam_x'}

    # Removing already removed
    req1 = request_with_cookies({'spam': 'ham'})
    req2 = request_with_cookies({'spam': 'ham', 'pom': 'pam'})
    resp2 = response_with_cookies(req2, [
        {'name': 'pom', 'value': 'pam'},
    ])
    resp1 = response_with_cookies(req1, [])
    assert resp1.cookiejar is resp2.cookiejar
    cookies = {c.name: c.value for c in resp1.cookiejar}
    assert cookies == {'pom': 'pam'}














def test_cache_args():
    spider = scrapy.Spider(name='foo')
    mw = _get_mw()
    mw.crawler.spider = spider
    mw.spider_opened(spider)
    dedupe_mw = SplashDeduplicateArgsMiddleware()

    # ========= Send first request - it should use save_args:
    lua_source = 'function main(splash) end'
    req = SplashRequest('http://example.com/foo',
                        endpoint='execute',
                        args={'lua_source': lua_source},
                        cache_args=['lua_source'])

    assert req.meta['splash']['args']['lua_source'] == lua_source
    # <---- spider
    req, = list(dedupe_mw.process_start_requests([req], spider))
    # ----> scheduler
    assert req.meta['splash']['args']['lua_source'] != lua_source
    assert list(mw._argument_values.values()) == [lua_source]
    assert list(mw._argument_values.keys()) == [req.meta['splash']['args']['lua_source']]
    # <---- scheduler
    # process request before sending it to the downloader
    req = mw.process_request(req, spider) or req
    # -----> downloader
    assert req.meta['splash']['args']['lua_source'] == lua_source
    assert req.meta['splash']['args']['save_args'] == ['lua_source']
    assert 'load_args' not in req.meta['splash']['args']
    assert req.meta['splash']['_local_arg_fingerprints'] == {
        'lua_source': list(mw._argument_values.keys())[0]
    }
    # <---- downloader
    resp_body = b'{}'
    resp = TextResponse("http://example.com",
                        headers={
                            b'Content-Type': b'application/json',
                            b'X-Splash-Saved-Arguments': b'lua_source=ba001160ef96fe2a3f938fea9e6762e204a562b3'
                        },
                        body=resp_body)
    resp = mw.process_response(req, resp, None)

    # ============ Send second request - it should use load_args
    req2 = SplashRequest('http://example.com/bar',
                        endpoint='execute',
                        args={'lua_source': lua_source},
                        cache_args=['lua_source'])
    req2, item = list(dedupe_mw.process_spider_output(resp, [req2, {'key': 'value'}], spider))
    assert item == {'key': 'value'}
    # ----> scheduler
    assert req2.meta['splash']['args']['lua_source'] != lua_source
    # <---- scheduler
    # process request before sending it to the downloader
    req2 = mw.process_request(req2, spider) or req2
    # -----> downloader
    assert req2.meta['splash']['args']['load_args'] == {"lua_source": "ba001160ef96fe2a3f938fea9e6762e204a562b3"}
    assert "lua_source" not in req2.meta['splash']['args']
    assert "save_args" not in req2.meta['splash']['args']
    assert json.loads(req2.body.decode('utf8')) == {
        'load_args': {'lua_source': 'ba001160ef96fe2a3f938fea9e6762e204a562b3'},
        'url': 'http://example.com/bar'
    }
    # <---- downloader
    resp = TextResponse("http://example.com/bar",
                        headers={b'Content-Type': b'application/json'},
                        body=b'{}')
    resp = mw.process_response(req, resp, spider)

    # =========== Third request is dispatched to another server where
    # =========== arguments are expired:
    req3 = SplashRequest('http://example.com/baz',
                         endpoint='execute',
                         args={'lua_source': lua_source},
                         cache_args=['lua_source'])
    req3, = list(dedupe_mw.process_spider_output(resp, [req3], spider))
    # ----> scheduler
    assert req3.meta['splash']['args']['lua_source'] != lua_source
    # <---- scheduler
    req3 = mw.process_request(req3, spider) or req3
    # -----> downloader
    assert json.loads(req3.body.decode('utf8')) == {
        'load_args': {'lua_source': 'ba001160ef96fe2a3f938fea9e6762e204a562b3'},
        'url': 'http://example.com/baz'
    }
    # <---- downloader

    resp_body = json.dumps({
        "type": "ExpiredArguments",
        "description": "Arguments stored with ``save_args`` are expired",
        "info": {"expired": ["html"]},
        "error": 498
    })
    resp = TextResponse("127.0.0.1:8050",
                        headers={b'Content-Type': b'application/json'},
                        status=498,
                        body=resp_body.encode('utf8'))
    req4 = mw.process_response(req3, resp, spider)
    assert isinstance(req4, SplashRequest)

    # process this request again
    req4, = list(dedupe_mw.process_spider_output(resp, [req4], spider))
    req4 = mw.process_request(req4, spider) or req4

    # it should become save_args request after all middlewares
    assert json.loads(req4.body.decode('utf8')) == {
        'lua_source': 'function main(splash) end',
        'save_args': ['lua_source'],
        'url': 'http://example.com/baz'
    }
    assert mw._remote_keys == {}


def test_splash_request_no_url():
    mw = _get_mw()
    lua_source = "function main(splash) return {result='ok'} end"
    req1 = SplashRequest(meta={'splash': {
        'args': {'lua_source': lua_source},
        'endpoint': 'execute',
    }})
    req = mw.process_request(req1, None)
    assert req.url == 'http://127.0.0.1:8050/execute'
    assert json.loads(to_native_str(req.body)) == {
        'url': 'about:blank',
        'lua_source': lua_source
    }


def test_post_request():
    mw = _get_mw()
    for body in [b'', b'foo=bar']:
        req1 = scrapy.Request("http://example.com",
                              method="POST",
                              body=body,
                              meta={'splash': {'endpoint': 'render.html'}})
        req = mw.process_request(req1, None)
        assert json.loads(to_native_str(req.body)) == {
            'url': 'http://example.com',
            'http_method': 'POST',
            'body': to_native_str(body),
        }








def test_float_wait_arg():
    mw = _get_mw()
    req1 = scrapy.Request("http://example.com", meta={
        'splash': {
            'endpoint': 'render.html',
            'args': {'wait': 0.5}
        }
    })
    req = mw.process_request(req1, None)
    assert json.loads(to_native_str(req.body)) == {'url': req1.url, 'wait': 0.5}


def test_slot_policy_single_slot():
    mw = _get_mw()
    meta = {'splash': {
        'slot_policy': scrapy_splash.SlotPolicy.SINGLE_SLOT
    }}

    req1 = scrapy.Request("http://example.com/path?key=value", meta=meta)
    req1 = mw.process_request(req1, None)

    req2 = scrapy.Request("http://fooexample.com/path?key=value", meta=meta)
    req2 = mw.process_request(req2, None)

    assert req1.meta.get('download_slot')
    assert req1.meta['download_slot'] == req2.meta['download_slot']


def test_slot_policy_per_domain():
    mw = _get_mw()
    meta = {'splash': {
        'slot_policy': scrapy_splash.SlotPolicy.PER_DOMAIN
    }}

    req1 = scrapy.Request("http://example.com/path?key=value", meta=meta)
    req1 = mw.process_request(req1, None)

    req2 = scrapy.Request("http://example.com/path2", meta=meta)
    req2 = mw.process_request(req2, None)

    req3 = scrapy.Request("http://fooexample.com/path?key=value", meta=meta)
    req3 = mw.process_request(req3, None)

    assert req1.meta.get('download_slot')
    assert req3.meta.get('download_slot')

    assert req1.meta['download_slot'] == req2.meta['download_slot']
    assert req1.meta['download_slot'] != req3.meta['download_slot']




