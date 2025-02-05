import json
from typing import List, Optional, Type, Union

import open_feature.exception
import open_feature.flag_evaluation
import pydantic
import urllib3
from urllib3.util import parse_url
from urllib.parse import urljoin, urlencode
from pydantic import BaseModel, ValidationError, PrivateAttr
from open_feature.evaluation_context import EvaluationContext
from open_feature.flag_evaluation import FlagEvaluationDetails
from open_feature.hook import Hook
from open_feature.provider.metadata import Metadata
from open_feature.provider.provider import AbstractProvider

from gofeatureflag_python_provider.metadata import GoFeatureFlagMetadata
from gofeatureflag_python_provider.options import GoFeatureFlagOptions
from gofeatureflag_python_provider.request_flag_evaluation import (
    RequestFlagEvaluation,
    GoFeatureFlagUser,
    user_from_evaluation_context,
)
from gofeatureflag_python_provider.response_flag_evaluation import (
    ResponseFlagEvaluation,
    JsonType,
)
from open_feature.flag_evaluation import Reason
from open_feature.exception import ErrorCode
from http import HTTPStatus
from open_feature.exception import (
    FlagNotFoundError,
    TypeMismatchError,
    GeneralError,
    OpenFeatureError,
)


class GoFeatureFlagProvider(AbstractProvider, BaseModel):
    options: GoFeatureFlagOptions
    _http_client: urllib3.PoolManager = PrivateAttr()

    def __init__(self, **data):
        """
        Constructor of the provider.
        It will initialize the http client for calling the GO Feature Flag relay proxy.

        :param data: data comming from pydantic configuration
        """
        super().__init__(**data)
        self._http_client = (
            self.options.urllib3PoolManager
            if self.options.urllib3PoolManager is not None
            else _default_urllib3_poolmanager()
        )

    def get_metadata(self) -> Metadata:
        return GoFeatureFlagMetadata()

    def get_provider_hooks(self) -> List[Hook]:
        return []

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagEvaluationDetails[bool]:
        return self.generic_go_feature_flag_resolver(
            bool, flag_key, default_value, evaluation_context
        )

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagEvaluationDetails[str]:
        return self.generic_go_feature_flag_resolver(
            str, flag_key, default_value, evaluation_context
        )

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagEvaluationDetails[int]:
        return self.generic_go_feature_flag_resolver(
            int, flag_key, default_value, evaluation_context
        )

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagEvaluationDetails[float]:
        return self.generic_go_feature_flag_resolver(
            float, flag_key, default_value, evaluation_context
        )

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: dict,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagEvaluationDetails[Union[list, dict]]:
        return self.generic_go_feature_flag_resolver(
            Union[dict, list], flag_key, default_value, evaluation_context
        )

    def generic_go_feature_flag_resolver(
        self,
        original_type: Type[JsonType],
        flag_key: str,
        default_value: JsonType,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagEvaluationDetails[JsonType]:
        """
        generic_go_feature_flag_resolver is a generic evaluations of your flag with GO Feature Flag relay proxy it works
        with all types.

        :param original_type: type of the request
        :param flag_key:  name of the flag
        :param default_value: default value of the flag
        :param evaluation_context: context to evaluate the flag
        :return: a FlagEvaluationDetails object containing the response for the SDK.
        """
        try:
            goff_user = user_from_evaluation_context(evaluation_context)
            goff_request = RequestFlagEvaluation(
                user=goff_user,
                defaultValue=default_value,
            )

            response = self._http_client.request(
                method="POST",
                url=urljoin(
                    str(self.options.endpoint), "/v1/feature/{}/eval".format(flag_key)
                ),
                headers={"Content-Type": "application/json"},
                body=goff_request.json(),
            )

            if response.status == HTTPStatus.NOT_FOUND.value:
                raise FlagNotFoundError(
                    "flag {} was not found in your configuration".format(flag_key)
                )

            if int(response.status) >= HTTPStatus.BAD_REQUEST.value:
                raise GeneralError(
                    "impossible to contact GO Feature Flag relay proxy instance"
                )

            response_flag_evaluation = ResponseFlagEvaluation[
                original_type
            ].parse_raw(response.data)

            if original_type == int:
                response_json = json.loads(response.data)
                # in some cases pydantic auto convert float in int.
                if type(response_json.get("value")) != int:
                    raise TypeMismatchError("unexpected type for flag {}".format(flag_key))

            if response_flag_evaluation.reason == Reason.DISABLED.value:
                return FlagEvaluationDetails[original_type](
                    flag_key=flag_key,
                    value=default_value,
                    reason=Reason.DISABLED,
                )

            if response_flag_evaluation.errorCode == ErrorCode.FLAG_NOT_FOUND.value:
                raise FlagNotFoundError(
                    "flag {} was not found in your configuration".format(flag_key)
                )

            return FlagEvaluationDetails[original_type](
                flag_key=flag_key,
                value=response_flag_evaluation.value,
                variant=response_flag_evaluation.variationType,
                reason=response_flag_evaluation.reason,
            )
        except ValidationError as exc:
            raise TypeMismatchError("unexpected type for flag {}".format(flag_key))

        except OpenFeatureError as exc:
            raise exc

        except Exception as exc:
            raise GeneralError(
                "unexpected error while evaluating flag {}: {}".format(flag_key, exc)
            )


def _default_urllib3_poolmanager() -> urllib3.PoolManager:
    """
    _default_urllib3_poolmanager configure a default HTTP Client to call the GO Feature Flag relay proxy.
    By default, we have a 10 seconds timeout to connect and read from the source.

    :return: return the default HTTP configuration to call the GO Feature Flag relay proxy
    """
    return urllib3.PoolManager(
        num_pools=100,
        timeout=urllib3.Timeout(connect=10, read=10),
        retries=urllib3.Retry(0),
    )
