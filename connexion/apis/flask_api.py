"""
This module defines a Flask Connexion API which implements translations between Flask and
Connexion requests / responses.
"""
import logging
import typing as t

import flask
from flask import Response as FlaskResponse

from connexion.apis import flask_utils
from connexion.apis.abstract import AbstractAPI
from connexion.decorators import SyncDecorator
from connexion.http_facts import FORM_CONTENT_TYPES
from connexion.jsonifier import Jsonifier
from connexion.lifecycle import ConnexionRequest, ConnexionResponse
from connexion.operations import AbstractOperation
from connexion.uri_parsing import AbstractURIParser
from connexion.utils import is_json_mimetype

logger = logging.getLogger("connexion.apis.flask_api")


class FlaskApi(AbstractAPI):
    def _set_base_path(self, base_path):
        super()._set_base_path(base_path)
        self._set_blueprint()

    def _set_blueprint(self):
        logger.debug("Creating API blueprint: %s", self.base_path)
        endpoint = flask_utils.flaskify_endpoint(self.base_path)
        self.blueprint = flask.Blueprint(
            endpoint,
            __name__,
            url_prefix=self.base_path,
            template_folder=str(self.options.openapi_console_ui_from_dir),
        )

    def _add_operation_internal(self, method, path, operation):
        operation_id = operation.operation_id
        logger.debug(
            "... Adding %s -> %s", method.upper(), operation_id, extra=vars(operation)
        )

        flask_path = flask_utils.flaskify_path(
            path, operation.get_path_parameter_types()
        )
        endpoint_name = flask_utils.flaskify_endpoint(
            operation.operation_id, operation.randomize_endpoint
        )

        endpoint = FlaskOperation.from_operation(
            operation, pythonic_params=self.pythonic_params
        )

        self.blueprint.add_url_rule(
            flask_path, endpoint_name, endpoint, methods=[method]
        )

    @classmethod
    def get_response(cls, response, mimetype=None):
        """Gets ConnexionResponse instance for the operation handler
        result. Status Code and Headers for response.  If only body
        data is returned by the endpoint function, then the status
        code will be set to 200 and no headers will be added.

        If the returned object is a flask.Response then it will just
        pass the information needed to recreate it.

        :type response: flask.Response | (flask.Response,) | (flask.Response, int) | (flask.Response, dict) | (flask.Response, int, dict)
        :rtype: ConnexionResponse
        """
        return cls._get_response(response, mimetype=mimetype)

    @classmethod
    def _is_framework_response(cls, response):
        """Return True if provided response is a framework type"""
        return flask_utils.is_flask_response(response)

    @classmethod
    def _framework_to_connexion_response(cls, response, mimetype):
        """Cast framework response class to ConnexionResponse used for schema validation"""
        return ConnexionResponse(
            status_code=response.status_code,
            mimetype=response.mimetype,
            content_type=response.content_type,
            headers=response.headers,
            body=response.get_data() if not response.direct_passthrough else None,
            is_streamed=response.is_streamed,
        )

    @classmethod
    def _connexion_to_framework_response(cls, response, mimetype):
        """Cast ConnexionResponse to framework response class"""
        flask_response = cls._build_response(
            mimetype=response.mimetype or mimetype,
            content_type=response.content_type,
            headers=response.headers,
            status_code=response.status_code,
            data=response.body,
        )

        return flask_response

    @classmethod
    def _build_response(
        cls,
        mimetype,
        content_type=None,
        headers=None,
        status_code=None,
        data=None,
    ):
        if cls._is_framework_response(data):
            return flask.current_app.make_response((data, status_code, headers))

        data, status_code, serialized_mimetype = cls._prepare_body_and_status_code(
            data=data,
            mimetype=mimetype,
            status_code=status_code,
        )

        kwargs = {
            "mimetype": mimetype or serialized_mimetype,
            "content_type": content_type,
            "headers": headers,
            "response": data,
            "status": status_code,
        }
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        return flask.current_app.response_class(**kwargs)

    @staticmethod
    def get_request(*, uri_parser: AbstractURIParser, **kwargs) -> ConnexionRequest:  # type: ignore
        return ConnexionRequest(flask.request, uri_parser=uri_parser)

    @staticmethod
    def get_body(request: ConnexionRequest) -> t.Any:
        """Get body from a sync request based on the content type."""
        if is_json_mimetype(request.content_type):
            return request.get_json(silent=True)
        elif request.mimetype in FORM_CONTENT_TYPES:
            return request.form
        else:
            # Return explicit None instead of empty bytestring so it is handled as null downstream
            return request.get_data() or None

    @classmethod
    def _set_jsonifier(cls):
        """
        Use Flask specific JSON loader
        """
        cls.jsonifier = Jsonifier(flask.json, indent=2)


class FlaskOperation:
    def __init__(
        self,
        operation: AbstractOperation,
        fn: t.Callable,
        uri_parser_class: t.Type[AbstractURIParser],
        api: AbstractAPI,
        mimetype: str,
        operation_id: str,
        pythonic_params: bool,
    ) -> None:
        self._operation = operation
        self._fn = fn
        self.uri_parser_class = uri_parser_class
        self.api = api
        self.mimetype = mimetype
        self.operation_id = operation_id
        self.pythonic_params = pythonic_params

    @classmethod
    def from_operation(
        cls, operation: AbstractOperation, pythonic_params: bool
    ) -> "FlaskOperation":
        return cls(
            operation,
            fn=operation.function,
            uri_parser_class=operation.uri_parser_class,
            api=operation.api,
            mimetype=operation.get_mimetype(),
            operation_id=operation.operation_id,
            pythonic_params=pythonic_params,
        )

    @property
    def fn(self) -> t.Callable:
        decorator = SyncDecorator(
            self._operation,
            uri_parser_cls=self.uri_parser_class,
            framework=self.api,
            parameter=True,
            response=True,
            pythonic_params=self.pythonic_params,
        )
        return decorator(self._fn)

    def __call__(self, *args, **kwargs) -> FlaskResponse:
        return self.fn(*args, **kwargs)
