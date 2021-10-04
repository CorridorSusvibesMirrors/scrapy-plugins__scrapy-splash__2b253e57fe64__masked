# -*- coding: utf-8 -*-
from __future__ import absolute_import

import copy
import json
import logging
import warnings
from collections import defaultdict



from scrapy_splash.responsetypes import responsetypes
from scrapy_splash.cookies import jar_to_har, har_to_jar
from scrapy_splash.utils import (
    scrapy_headers_to_unicode_dict,
    json_based_hash,
    parse_x_splash_saved_arguments_header,
)
from scrapy_splash.response import get_splash_status, get_splash_headers


logger = logging.getLogger(__name__)


class SlotPolicy(object):
    PER_DOMAIN = 'per_domain'
    SINGLE_SLOT = 'single_slot'
    SCRAPY_DEFAULT = 'scrapy_default'

    _known = {PER_DOMAIN, SINGLE_SLOT, SCRAPY_DEFAULT}


class SplashCookiesMiddleware(object):
    """
    This downloader middleware maintains cookiejars for Splash requests.

    It gets cookies from 'cookies' field in Splash JSON responses
    and sends current cookies in 'cookies' JSON POST argument instead of
    sending them in http headers.

    It should process requests before SplashMiddleware, and process responses
    after SplashMiddleware.
    """
    def __init__(self, debug=False):
        self.jars = defaultdict(CookieJar)
        self.debug = debug

    @classmethod
    def from_crawler(cls, crawler):
        return cls(debug=crawler.settings.getbool('SPLASH_COOKIES_DEBUG'))

    def process_request(self, request, spider):
        """
        For Splash requests add 'cookies' key with current
        cookies to ``request.meta['splash']['args']`` and remove cookie
        headers sent to Splash itself.
        """
        if 'splash' not in request.meta:
            return

        if request.meta.get('_splash_processed'):
            request.headers.pop('Cookie', None)
            return

        splash_options = request.meta['splash']

        splash_args = splash_options.setdefault('args', {})
        if 'cookies' in splash_args:  # cookies already set
            return

        if 'session_id' not in splash_options:
            return

        jar = self.jars[splash_options['session_id']]

        cookies = self._get_request_cookies(request)
        har_to_jar(jar, cookies)

        splash_args['cookies'] = jar_to_har(jar)
        self._debug_cookie(request, spider)

    def process_response(self, request, response, spider):
        """
        For Splash JSON responses add all cookies from
        'cookies' in a response to the cookiejar.
        """
        from scrapy_splash import SplashJsonResponse
        if not isinstance(response, SplashJsonResponse):
            return response

        if 'cookies' not in response.data:
            return response

        if 'splash' not in request.meta:
            return response

        if not request.meta.get('_splash_processed'):
            warnings.warn("SplashCookiesMiddleware requires SplashMiddleware")
            return response

        splash_options = request.meta['splash']
        session_id = splash_options.get('new_session_id',
                                        splash_options.get('session_id'))
        if session_id is None:
            return response

        jar = self.jars[session_id]
        request_cookies = splash_options['args'].get('cookies', [])
        har_to_jar(jar, response.data['cookies'], request_cookies)
        self._debug_set_cookie(response, spider)
        response.cookiejar = jar
        return response

    def _get_request_cookies(self, request):
        if isinstance(request.cookies, dict):
            return [
                {'name': k, 'value': v} for k, v in request.cookies.items()
            ]
        return request.cookies or []

    def _debug_cookie(self, request, spider):
        if self.debug:
            cl = request.meta['splash']['args']['cookies']
            if cl:
                cookies = '\n'.join(
                    'Cookie: {}'.format(self._har_repr(c)) for c in cl)
                msg = 'Sending cookies to: {}\n{}'.format(request, cookies)
                logger.debug(msg, extra={'spider': spider})

    def _debug_set_cookie(self, response, spider):
        if self.debug:
            cl = response.data['cookies']
            if cl:
                cookies = '\n'.join(
                    'Set-Cookie: {}'.format(self._har_repr(c)) for c in cl)
                msg = 'Received cookies from: {}\n{}'.format(response, cookies)
                logger.debug(msg, extra={'spider': spider})

    @staticmethod
    def _har_repr(har_cookie):
        return '{}={}'.format(har_cookie['name'], har_cookie['value'])


class SplashDeduplicateArgsMiddleware(object):
    """
    Spider middleware which allows not to store duplicate Splash argument
    values in request queue. It works together with SplashMiddleware downloader
    middleware.
    """
    local_values_key = '_splash_local_values'

    def process_spider_output(self, response, result, spider):
        for el in result:
            if isinstance(el, scrapy.Request):
                yield self._process_request(el, spider)
            else:
                yield el

    def process_start_requests(self, start_requests, spider):
        if not hasattr(spider, 'state'):
            spider.state = {}
        spider.state.setdefault(self.local_values_key, {})  # fingerprint => value dict

        for req in start_requests:
            yield self._process_request(req, spider)

    def _process_request(self, request, spider):
        """
        Replace requested meta['splash']['args'] values with their fingerprints.
        This allows to store values only once in request queue, which helps
        with disk queue size.

        Downloader middleware should restore the values from fingerprints.
        """
        if 'splash' not in request.meta:
            return request

        if '_replaced_args' in request.meta['splash']:
            # don't process re-scheduled requests
            # XXX: does it work as expected?
            warnings.warn("Unexpected request.meta['splash']['_replaced_args']")
            return request

        request.meta['splash']['_replaced_args'] = []
        cache_args = request.meta['splash'].get('cache_args', [])
        args = request.meta['splash'].setdefault('args', {})

        for name in cache_args:
            if name not in args:
                continue
            value = args[name]
            fp = 'LOCAL+' + json_based_hash(value)
            spider.state[self.local_values_key][fp] = value
            args[name] = fp
            request.meta['splash']['_replaced_args'].append(name)

        return request



