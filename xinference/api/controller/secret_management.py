# Copyright 2022-2024 XProbe Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copyright 2022-2024 XProbe Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from ..database.core import get_db
from ..database.db_orm import Secret
from ..service.secret_service import get_secret_service
from .utils import handle_exception_500

logger = logging.getLogger(__name__)


class CreateSecretBody(BaseModel):
    name: str


class DeleteSecretBody(CreateSecretBody):
    secrets: str


def convert_orm_to_dict(secret: Secret):
    return {
        "name": secret.name,
        "secrets": secret.secrets,
        "created_ts": secret.created_ts,
    }


@handle_exception_500(logger=logger)
def create_secret(
    secret_body: CreateSecretBody, request: Request, db: Session = Depends(get_db)
) -> JSONResponse:
    secret_service = get_secret_service(db)
    new_secret = secret_service.create_secret(
        secret_body.name, request.headers["XINFERENCE-USERNAME"]
    )
    return JSONResponse(content=convert_orm_to_dict(new_secret))


@handle_exception_500(logger=logger)
def list_secrets(db: Session = Depends(get_db)) -> JSONResponse:
    secret_service = get_secret_service(db)
    return JSONResponse(
        content=[convert_orm_to_dict(secret) for secret in secret_service.get_secrets()]
    )


@handle_exception_500(logger=logger)
def delete_secret(
    secret_body: DeleteSecretBody, db: Session = Depends(get_db)
) -> JSONResponse:
    secret_service = get_secret_service(db)
    secret_service.delete_secret(secret_body.name)
    return JSONResponse(content={})
