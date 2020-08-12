"""
Pyebox
"""
import asyncio
import json
import logging
import re
import datetime

import async_timeout


import aiohttp
from bs4 import BeautifulSoup


_LOGGER = logging.getLogger('pyebox')

REQUESTS_TIMEOUT = 15

HOST = "https://client.ebox.ca"
HOME_URL = "{}/".format(HOST)
LOGIN_URL = "{}/login".format(HOST)
USAGE_URL = "{}/myusage".format(HOST)

USAGE_MAP = {"before_offpeak_download": 0,
             "before_offpeak_upload": 1,
             "before_offpeak_total": 2,
             "offpeak_download": 3,
             "offpeak_upload": 4,
             "offpeak_total": 5,
             "download": 6,
             "upload": 7,
             "total": 8}


class PyEboxError(Exception):
    pass


class EboxClient(object):

    def __init__(self, username, password, timeout=REQUESTS_TIMEOUT, session=None):
        """Initialize the client object."""
        self.username = username
        self.password = password
        self._data = {}
        self._timeout = timeout
        self._session = session

    async def _get_httpsession(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def _get_login_page(self):
        """Go to the login page."""
        try:
            async with async_timeout.timeout(10):
                raw_res = await self._session.get(HOME_URL,
                                                  allow_redirects=False,
                                                  timeout=self._timeout)
        except OSError:
            raise PyEboxError("Can not connect to login page")
        # Get token
        content = await raw_res.text()
        soup = BeautifulSoup(content, 'html.parser')
        token_node = soup.find('input', {'name': '_csrf_security_token'})
        if token_node is None:
            raise PyEboxError("No token input found")
        token = token_node.attrs.get('value')
        if token is None:
            raise PyEboxError("No token found")
        return token

    async def _post_login_page(self, token):
        """Login to EBox website."""
        data = {"usrname": self.username,
                "pwd": self.password,
                "_csrf_security_token": token}

        try:
            async with async_timeout.timeout(10):
                raw_res = await self._session.post(LOGIN_URL,
                                                   data=data,
                                                   allow_redirects=False,
                                                   timeout=self._timeout)
        except OSError:
            raise PyEboxError("Can not submit login form")
        if raw_res.status != 302:
            raise PyEboxError("Bad HTTP status code")
        # search for errors
        re_results = re.search(r"err=[^&]*&", raw_res.headers.get('Location'))
        if re_results:
            await self._handle_login_error(raw_res.headers.get('Location'))
        return True

    async def _handle_login_error(self, url):
        raw_res = await self._session.get(HOST + url)
        content = await raw_res.text()
        soup = BeautifulSoup(content, 'html.parser')
        error_node = soup.find("div", id="divErrorLogin")
        if error_node:
            error_msg = error_node.find("b")
            if error_msg and error_msg.text:
                raise PyEboxError("Login error: {}".format(error_msg.text))
        raise PyEboxError("Unknown login error")

    async def _get_home_data(self):
        """Get home data."""
        # Import
        from bs4 import BeautifulSoup
        # Prepare return
        home_data = {}
        # Http request
        try:
            async with async_timeout.timeout(10):
                raw_res = await self._session.get(HOME_URL,
                                                  timeout=self._timeout)
        except OSError:
            raise PyEboxError("Can not get home page")
        # Prepare soup
        content = await raw_res.text()
        soup = BeautifulSoup(content, 'html.parser')
        # Get balance
        try:
            str_value = soup.find("div", {"class": "text_amount"}).\
                            text.split()[0]
            home_data["balance"] = float(str_value)
        except OSError:
            raise PyEboxError("Can not get current balance")
        return home_data

    async def _get_usage_data(self):
        """Get data usage."""
        # Get Usage
        raw_res = await self._session.get(USAGE_URL)
        content = await raw_res.text()
        soup = BeautifulSoup(content, 'html.parser')
        # Find all span
        span_list = soup.find_all("span", {"class": "switchDisplay"})
        if span_list is None:
            raise PyEboxError("Can not get usage page")
        usage_data = {}
        # Looking for limit
        limit_node = soup.find('span', {'class': 'text_summary3'})
        if limit_node is None:
            raise PyEboxError("Can not find limit span")
        raw_data = [d.strip() for d in limit_node.text.split("/")]
        if len(raw_data) != 2:
            raise PyEboxError("Can not get limit data")
        try:
            usage_data["limit"] = float(raw_data[1].split()[0])
        except ValueError:
            # It seems that you don't have any limit...
            usage_data["limit"] = 0.0
        except OSError:
            raise PyEboxError("Can not get limit data")
        # Get percent
        try:
            str_value = soup.find("div", {"id": "circleprogress_0"}).\
                            attrs.get("data-perc")
            usage_data["usage"] = float(str_value)
        except OSError:
            raise PyEboxError("Can not get usage percent")
        # Get data
        for key, index in USAGE_MAP.items():
            try:
                str_value = span_list[index].attrs.get("data-m").split()[0]
                usage_data[key] = abs(float(str_value)) / 1024
            except OSError:
                raise PyEboxError("Can not get %s", key)

        return usage_data

    async def _get_usage_data_day(self):
        today = datetime.date.today()
        first = today.replace(day=1)
        lastMonth = first - datetime.timedelta(days=1)
        currentYear = first.strftime("%Y")
        currentMonth = first.strftime("%m")

        lastMonthYear = lastMonth.strftime("%Y")
        lastMonhtMonth= lastMonth.strftime("%m")

        """Get data usage."""
        usage_day_detail = {}
        data_day = []
        data_day.extend(await self.fetch_data_month(currentYear, currentMonth))
        data_day.extend(await self.fetch_data_month(lastMonthYear, lastMonhtMonth))
        usage_day_detail["detail_day"] = data_day
        return usage_day_detail

    async def fetch_data_month(self, year, month):
        headers = {'Host': 'client.ebox.ca',
        'User-Agent': 'Mozilla / 5.0(X11; Linux x86_64; rv: 68.0) Gecko / 20100101 Firefox / 68.0',
        'Accept': 'application / json, text / javascript, * / *; q = 0.01',
        'Accept-Language': 'en - US, en;q = 0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://client.ebox.ca/myusage',
        'X-Requested-With': 'XMLHttpRequest',
        'Connection': 'keep-alive'
         }
        await self._session.get("https://client.ebox.ca/ajax/usages?action=getMonth&m={}&y={}".format(month, year), headers=headers)
        raw_res = await self._session.get("https://client.ebox.ca/myusage?dp={}_{}".format(year, month))
        content = await raw_res.text()
        soup = BeautifulSoup(content, 'html.parser')
        td_list = soup.find_all("td", {"class": "text_small"})

        usage_day_data = []
        for index in range(0, len(td_list), 4):
            usage_data_day = {}
            usage_data_day["date"] = td_list[index].text.split()[0]
            usage_data_day["download"] = td_list[index + 1].select("span")[0].attrs.get("data-m").split()[0]
            usage_data_day["upload"] = td_list[index + 2].select("span")[0].attrs.get("data-m").split()[0]
            usage_data_day["total"] = td_list[index + 3].select("span")[0].attrs.get("data-m").split()[0]
            if usage_data_day["date"].startswith("{}-{}".format(year, month)):
                usage_day_data.append(usage_data_day)
        return usage_day_data

    async def fetch_data(self):
        """Get the latest data from EBox."""
        # Get http session
        await self._get_httpsession()
        # Get login page
        token = await self._get_login_page()
        # Post login page
        await self._post_login_page(token)
        # Get home data
        home_data = await self._get_home_data()
        # Get usage data
        usage_data = await self._get_usage_data()
        usage_data_day = await self._get_usage_data_day()
        # Merge data
        self._data.update(home_data)
        self._data.update(usage_data)
        self._data.update(usage_data_day)
        await self.close_session()


    def get_data(self):
        """Return collected data"""
        return self._data

    async def close_session(self):
        """Close current session."""
        if not self._session.closed:
            if self._session._connector_owner:
                await self._session._connector.close()
            self._session._connector = None
