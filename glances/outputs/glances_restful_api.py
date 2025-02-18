# -*- coding: utf-8 -*-
#
# This file is part of Glances.
#
# SPDX-FileCopyrightText: 2024 Nicolas Hennion <nicolas@nicolargo.com>
#
# SPDX-License-Identifier: LGPL-3.0-only
#

"""RestFull API interface class."""

import os
import sys
import tempfile
from io import open
import webbrowser
from urllib.parse import urljoin

# Replace typing_extensions by typing when Python 3.8 support will be dropped
from typing import Annotated

from glances import __version__, __apiversion__
from glances.password import GlancesPassword
from glances.timer import Timer
from glances.logger import logger

# FastAPI import
try:
    from fastapi import FastAPI, Depends, HTTPException, status, APIRouter, Request
    from fastapi.security import HTTPBasic, HTTPBasicCredentials
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.responses import HTMLResponse, ORJSONResponse
    from fastapi.templating import Jinja2Templates
    from fastapi.staticfiles import StaticFiles
except ImportError:
    logger.critical('FastAPI import error. Glances cannot start in web server mode.')
    sys.exit(2)

try:
    import uvicorn
except ImportError:
    logger.critical('Uvicorn import error. Glances cannot start in web server mode.')
    sys.exit(2)
import contextlib
import threading
import time

security = HTTPBasic()


class GlancesUvicornServer(uvicorn.Server):
    def install_signal_handlers(self):
        pass

    @contextlib.contextmanager
    def run_in_thread(self, timeout=3):
        thread = threading.Thread(target=self.run)
        thread.start()
        try:
            chrono = Timer(timeout)
            while not self.started and not chrono.finished():
                time.sleep(0.5)
            # Timeout reached
            # Something go wrong...
            # The Uvicorn server should be stopped
            if not self.started:
                self.should_exit = True
                thread.join()
            yield
        finally:
            self.should_exit = True
            thread.join()


class GlancesRestfulApi(object):
    """This class manages the Restful API server."""

    API_VERSION = __apiversion__

    def __init__(self, config=None, args=None):
        # Init config
        self.config = config

        # Init args
        self.args = args

        # Init stats
        # Will be updated within route
        self.stats = None

        # cached_time is the minimum time interval between stats updates
        # i.e. HTTP/RESTful calls will not retrieve updated info until the time
        # since last update is passed (will retrieve old cached info instead)
        self.timer = Timer(0)

        # Load configuration file
        self.load_config(config)

        # Set the bind URL
        self.bind_url = urljoin('http://{}:{}/'.format(self.args.bind_address, self.args.port), self.url_prefix)

        # FastAPI Init
        if self.args.password:
            self._app = FastAPI(dependencies=[Depends(self.authentication)])
            self._password = GlancesPassword(username=args.username, config=config)

        else:
            self._app = FastAPI()
            self._password = None

        # Change the default root path
        if self.url_prefix != '/':
            self._app.include_router(APIRouter(prefix=self.url_prefix.rstrip('/')))

        # Set path for WebUI
        webui_root_path = config.get_value(
            'outputs', 'webui_root_path', default=os.path.dirname(os.path.realpath(__file__))
        )
        if webui_root_path == '':
            webui_root_path = os.path.dirname(os.path.realpath(__file__))
        self.STATIC_PATH = os.path.join(webui_root_path, 'static/public')
        self.TEMPLATE_PATH = os.path.join(webui_root_path, 'static/templates')
        self._templates = Jinja2Templates(directory=self.TEMPLATE_PATH)

        # FastAPI Enable CORS
        # https://fastapi.tiangolo.com/tutorial/cors/
        self._app.add_middleware(
            CORSMiddleware,
            # allow_origins=["*"],
            allow_origins=[self.bind_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # FastAPI Enable GZIP compression
        # https://fastapi.tiangolo.com/advanced/middleware/
        self._app.add_middleware(GZipMiddleware, minimum_size=1000)

        # FastAPI Define routes
        self._app.include_router(self._router())

    def load_config(self, config):
        """Load the outputs section of the configuration file."""
        # Limit the number of processes to display in the WebUI
        self.url_prefix = '/'
        if config is not None and config.has_section('outputs'):
            n = config.get_value('outputs', 'max_processes_display', default=None)
            logger.debug('Number of processes to display in the WebUI: {}'.format(n))
            self.url_prefix = config.get_value('outputs', 'url_prefix', default='/')
            logger.debug('URL prefix: {}'.format(self.url_prefix))

    def __update__(self):
        # Never update more than 1 time per cached_time
        if self.timer.finished():
            self.stats.update()
            self.timer = Timer(self.args.cached_time)

    def app(self):
        return self._app()

    def authentication(self, creds: Annotated[HTTPBasicCredentials, Depends(security)]):
        """Check if a username/password combination is valid."""
        if creds.username == self.args.username:
            # check_password and get_hash are (lru) cached to optimize the requests
            if self._password.check_password(self.args.password, self._password.get_hash(creds.password)):
                return creds.username

        # If the username/password combination is invalid, return an HTTP 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    def _router(self):
        """Define a custom router for Glances path."""
        router = APIRouter()

        # REST API
        router.add_api_route(
            '/api/%s/status' % self.API_VERSION,
            status_code=status.HTTP_200_OK,
            response_class=ORJSONResponse,
            endpoint=self._api_status,
        )

        router.add_api_route(
            '/api/%s/config' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_config
        )
        router.add_api_route(
            '/api/%s/config/{section}' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_config_section,
        )
        router.add_api_route(
            '/api/%s/config/{section}/{item}' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_config_section_item,
        )

        router.add_api_route('/api/%s/args' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_args)
        router.add_api_route(
            '/api/%s/args/{item}' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_args_item
        )

        router.add_api_route(
            '/api/%s/pluginslist' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_plugins
        )
        router.add_api_route('/api/%s/all' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_all)
        router.add_api_route(
            '/api/%s/all/limits' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_all_limits
        )
        router.add_api_route(
            '/api/%s/all/views' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_all_views
        )

        router.add_api_route('/api/%s/help' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_help)
        router.add_api_route('/api/%s/{plugin}' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api)
        router.add_api_route(
            '/api/%s/{plugin}/history' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_history
        )
        router.add_api_route(
            '/api/%s/{plugin}/history/{nb}' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_history,
        )
        router.add_api_route(
            '/api/%s/{plugin}/top/{nb}' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_top
        )
        router.add_api_route(
            '/api/%s/{plugin}/limits' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_limits
        )
        router.add_api_route(
            '/api/%s/{plugin}/views' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_views
        )
        router.add_api_route(
            '/api/%s/{plugin}/{item}' % self.API_VERSION, response_class=ORJSONResponse, endpoint=self._api_item
        )
        router.add_api_route(
            '/api/%s/{plugin}/{item}/history' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_item_history,
        )
        router.add_api_route(
            '/api/%s/{plugin}/{item}/history/{nb}' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_item_history,
        )
        router.add_api_route(
            '/api/%s/{plugin}/{item}/description' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_item_description,
        )
        router.add_api_route(
            '/api/%s/{plugin}/{item}/unit' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_item_unit,
        )
        router.add_api_route(
            '/api/%s/{plugin}/{item}/{value}' % self.API_VERSION,
            response_class=ORJSONResponse,
            endpoint=self._api_value,
        )

        # Restful API
        bindmsg = 'Glances RESTful API Server started on {}api/{}'.format(self.bind_url, self.API_VERSION)
        logger.info(bindmsg)

        # WEB UI
        if not self.args.disable_webui:
            # Template for the root index.html file
            router.add_api_route('/', response_class=HTMLResponse, endpoint=self._index)

            # Statics files
            self._app.mount("/static", StaticFiles(directory=self.STATIC_PATH), name="static")

            logger.info("Get WebUI in {}".format(self.STATIC_PATH))

            bindmsg = 'Glances Web User Interface started on {}'.format(self.bind_url)
        else:
            bindmsg = 'The WebUI is disable (--disable-webui)'

        logger.info(bindmsg)
        print(bindmsg)

        return router

    def start(self, stats):
        """Start the bottle."""
        # Init stats
        self.stats = stats

        # Init plugin list
        self.plugins_list = self.stats.getPluginsList()

        if self.args.open_web_browser:
            # Implementation of the issue #946
            # Try to open the Glances Web UI in the default Web browser if:
            # 1) --open-web-browser option is used
            # 2) Glances standalone mode is running on Windows OS
            webbrowser.open(self.bind_url, new=2, autoraise=1)

        # Start Uvicorn server
        self._start_uvicorn()

    def _start_uvicorn(self):
        # Run the Uvicorn Web server
        uvicorn_config = uvicorn.Config(
            self._app, host=self.args.bind_address, port=self.args.port, access_log=self.args.debug
        )
        try:
            self.uvicorn_server = GlancesUvicornServer(config=uvicorn_config)
        except Exception as e:
            logger.critical('Error: Can not ran Glances Web server ({})'.format(e))
            self.uvicorn_server = None
        else:
            with self.uvicorn_server.run_in_thread():
                while not self.uvicorn_server.should_exit:
                    time.sleep(1)

    def end(self):
        """End the Web server"""
        logger.info("Close the Web server")

    def _index(self, request: Request):
        """Return main index.html (/) file.

        Parameters are available through the request object.
        Example: http://localhost:61208/?refresh=5

        Note: This function is only called the first time the page is loaded.
        """
        refresh_time = request.query_params.get('refresh', default=max(1, int(self.args.time)))

        # Update the stat
        self.__update__()

        # Display
        return self._templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "refresh_time": refresh_time,
            },
        )

    def _api_status(self):
        """Glances API RESTful implementation.

        Return a 200 status code.
        This entry point should be used to check the API health.

        See related issue:  Web server health check endpoint #1988
        """

        return ORJSONResponse({'version': __version__})

    def _api_help(self):
        """Glances API RESTful implementation.

        Return the help data or 404 error.
        """
        try:
            plist = self.stats.get_plugin("help").get_view_data()
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get help view data (%s)" % str(e))

        return ORJSONResponse(plist)

    def _api_plugins(self):
        """Glances API RESTFul implementation.

        @api {get} /api/%s/pluginslist Get plugins list
        @apiVersion 2.0
        @apiName pluginslist
        @apiGroup plugin

        @apiSuccess {String[]} Plugins list.

        @apiSuccessExample Success-Response:
            HTTP/1.1 200 OK
            [
               "load",
               "help",
               "ip",
               "memswap",
               "processlist",
               ...
            ]

         @apiError Cannot get plugin list.

         @apiErrorExample Error-Response:
            HTTP/1.1 404 Not Found
        """
        # Update the stat
        self.__update__()

        try:
            plist = self.plugins_list
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get plugin list (%s)" % str(e))

        return ORJSONResponse(plist)

    def _api_all(self):
        """Glances API RESTful implementation.

        Return the JSON representation of all the plugins
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if self.args.debug:
            fname = os.path.join(tempfile.gettempdir(), 'glances-debug.json')
            try:
                with open(fname) as f:
                    return f.read()
            except IOError:
                logger.debug("Debug file (%s) not found" % fname)

        # Update the stat
        self.__update__()

        try:
            # Get the RAW value of the stat ID
            statval = self.stats.getAllAsDict()
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get stats (%s)" % str(e))

        return ORJSONResponse(statval)

    def _api_all_limits(self):
        """Glances API RESTful implementation.

        Return the JSON representation of all the plugins limits
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        try:
            # Get the RAW value of the stat limits
            limits = self.stats.getAllLimitsAsDict()
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get limits (%s)" % str(e))

        return ORJSONResponse(limits)

    def _api_all_views(self):
        """Glances API RESTful implementation.

        Return the JSON representation of all the plugins views
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        try:
            # Get the RAW value of the stat view
            limits = self.stats.getAllViewsAsDict()
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get views (%s)" % str(e))

        return ORJSONResponse(limits)

    def _api(self, plugin):
        """Glances API RESTful implementation.

        Return the JSON representation of a given plugin
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        # Update the stat
        self.__update__()

        try:
            # Get the RAW value of the stat ID
            statval = self.stats.get_plugin(plugin).get_raw()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get plugin %s (%s)" % (plugin, str(e))
            )

        return ORJSONResponse(statval)

    def _api_top(self, plugin, nb: int = 0):
        """Glances API RESTful implementation.

        Return the JSON representation of a given plugin limited to the top nb items.
        It is used to reduce the payload of the HTTP response (example: processlist).

        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        # Update the stat
        self.__update__()

        try:
            # Get the RAW value of the stat ID
            statval = self.stats.get_plugin(plugin).get_raw()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get plugin %s (%s)" % (plugin, str(e))
            )

        print(statval)

        if isinstance(statval, list):
            statval = statval[:nb]

        return ORJSONResponse(statval)

    def _api_history(self, plugin, nb: int = 0):
        """Glances API RESTful implementation.

        Return the JSON representation of a given plugin history
        Limit to the last nb items (all if nb=0)
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        # Update the stat
        self.__update__()

        try:
            # Get the RAW value of the stat ID
            statval = self.stats.get_plugin(plugin).get_raw_history(nb=int(nb))
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get plugin history %s (%s)" % (plugin, str(e))
            )

        return statval

    def _api_limits(self, plugin):
        """Glances API RESTful implementation.

        Return the JSON limits of a given plugin
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        try:
            # Get the RAW value of the stat limits
            ret = self.stats.get_plugin(plugin).limits
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get limits for plugin %s (%s)" % (plugin, str(e))
            )

        return ORJSONResponse(ret)

    def _api_views(self, plugin):
        """Glances API RESTful implementation.

        Return the JSON views of a given plugin
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        try:
            # Get the RAW value of the stat views
            ret = self.stats.get_plugin(plugin).get_views()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get views for plugin %s (%s)" % (plugin, str(e))
            )

        return ORJSONResponse(ret)

    def _api_item(self, plugin, item):
        """Glances API RESTful implementation.

        Return the JSON representation of the couple plugin/item
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        # Update the stat
        self.__update__()

        try:
            # Get the RAW value of the stat views
            ret = self.stats.get_plugin(plugin).get_raw_stats_item(item)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cannot get item %s in plugin %s (%s)" % (item, plugin, str(e)),
            )

        return ORJSONResponse(ret)

    def _api_item_history(self, plugin, item, nb: int = 0):
        """Glances API RESTful implementation.

        Return the JSON representation of the couple plugin/history of item
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error

        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        # Update the stat
        self.__update__()

        try:
            # Get the RAW value of the stat history
            ret = self.stats.get_plugin(plugin).get_raw_history(item, nb=nb)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get history for plugin %s (%s)" % (plugin, str(e))
            )
        else:
            return ORJSONResponse(ret)

    def _api_item_description(self, plugin, item):
        """Glances API RESTful implementation.

        Return the JSON representation of the couple plugin/item description
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        try:
            # Get the description
            ret = self.stats.get_plugin(plugin).get_item_info(item, 'description')
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cannot get %s description for plugin %s (%s)" % (item, plugin, str(e)),
            )
        else:
            return ORJSONResponse(ret)

    def _api_item_unit(self, plugin, item):
        """Glances API RESTful implementation.

        Return the JSON representation of the couple plugin/item unit
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        try:
            # Get the unit
            ret = self.stats.get_plugin(plugin).get_item_info(item, 'unit')
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cannot get %s unit for plugin %s (%s)" % (item, plugin, str(e)),
            )
        else:
            return ORJSONResponse(ret)

    def _api_value(self, plugin, item, value):
        """Glances API RESTful implementation.

        Return the process stats (dict) for the given item=value
        HTTP/200 if OK
        HTTP/400 if plugin is not found
        HTTP/404 if others error
        """
        if plugin not in self.plugins_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown plugin %s (available plugins: %s)" % (plugin, self.plugins_list),
            )

        # Update the stat
        self.__update__()

        try:
            # Get the RAW value
            ret = self.stats.get_plugin(plugin).get_raw_stats_value(item, value)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cannot get %s = %s for plugin %s (%s)" % (item, value, plugin, str(e)),
            )
        else:
            return ORJSONResponse(ret)

    def _api_config(self):
        """Glances API RESTful implementation.

        Return the JSON representation of the Glances configuration file
        HTTP/200 if OK
        HTTP/404 if others error
        """
        try:
            # Get the RAW value of the config' dict
            args_json = self.config.as_dict()
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get config (%s)" % str(e))
        else:
            return ORJSONResponse(args_json)

    def _api_config_section(self, section):
        """Glances API RESTful implementation.

        Return the JSON representation of the Glances configuration section
        HTTP/200 if OK
        HTTP/400 if item is not found
        HTTP/404 if others error
        """
        config_dict = self.config.as_dict()
        if section not in config_dict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown configuration item %s" % section
            )

        try:
            # Get the RAW value of the config' dict
            ret_section = config_dict[section]
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get config section %s (%s)" % (section, str(e))
            )

        return ORJSONResponse(ret_section)

    def _api_config_section_item(self, section, item):
        """Glances API RESTful implementation.

        Return the JSON representation of the Glances configuration section/item
        HTTP/200 if OK
        HTTP/400 if item is not found
        HTTP/404 if others error
        """
        config_dict = self.config.as_dict()
        if section not in config_dict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown configuration item %s" % section
            )

        try:
            # Get the RAW value of the config' dict section
            ret_section = config_dict[section]
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get config section %s (%s)" % (section, str(e))
            )

        try:
            # Get the RAW value of the config' dict item
            ret_item = ret_section[item]
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cannot get item %s in config section %s (%s)" % (item, section, str(e)),
            )

        return ORJSONResponse(ret_item)

    def _api_args(self):
        """Glances API RESTful implementation.

        Return the JSON representation of the Glances command line arguments
        HTTP/200 if OK
        HTTP/404 if others error
        """
        try:
            # Get the RAW value of the args' dict
            # Use vars to convert namespace to dict
            # Source: https://docs.python.org/%s/library/functions.html#vars
            args_json = vars(self.args)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get args (%s)" % str(e))

        return ORJSONResponse(args_json)

    def _api_args_item(self, item):
        """Glances API RESTful implementation.

        Return the JSON representation of the Glances command line arguments item
        HTTP/200 if OK
        HTTP/400 if item is not found
        HTTP/404 if others error
        """
        if item not in self.args:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown argument item %s" % item)

        try:
            # Get the RAW value of the args' dict
            # Use vars to convert namespace to dict
            # Source: https://docs.python.org/%s/library/functions.html#vars
            args_json = vars(self.args)[item]
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cannot get args item (%s)" % str(e))

        return ORJSONResponse(args_json)
