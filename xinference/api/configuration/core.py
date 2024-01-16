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
import os
from functools import lru_cache

from pydantic import BaseSettings


class BaseConfig(BaseSettings):
    DATABASE_DIALECT: str = ""
    DATABASE_DRIVER: str = ""
    DATABASE_NAME: str = ""
    DATABASE_URL: str = ""
    DATABASE_PORT: int = -1
    DATABASE_USERNAME: str = ""
    DATABASE_PASSWORD: str = ""


class ProdConfig(BaseConfig):
    class Config:
        env_file = os.path.join(os.path.dirname(__file__), "settings.prod.env")


@lru_cache
def get_settings():
    return ProdConfig()
